"""Stdlib-only tests for the inline-verdict-on-Results-view feature.

Three concerns, all exercised without third-party deps (pypi is blackholed):

(c) The full persist -> read -> API-serialise round-trip preserves the
    per-field expected/actual/outcome and row-index fields the frontend
    renders — including a transaction DISAGREE (row indices set) and a
    type-1 UNMATCHED_ROW (actual_row_index=None).
(d) The comparison -> transaction-row mapping (by actual_row_index) the
    frontend implements, including the two UNMATCHED_ROW flavours and the
    collision guard between a type-1 unmatched row and a matched row that
    happen to share a numeric index.
    Plus frontend content assertions pinning that index.html wires the
    verdict through fetch -> ResultsView -> FieldRow/TxnCell and renders
    the "seen in PDF, not extracted" section.
"""

import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import _comparison_to_dict
from contracts.models import (
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    JudgeVerdict,
    MatchMethod,
)
from db.mapping import verdict_from_dict, verdict_to_dict

_INDEX_HTML = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"


# ---------------------------------------------------------------------------
# (c) Persist -> read -> API-serialise round-trip
# ---------------------------------------------------------------------------

class VerdictSerializationRoundTripTest(unittest.TestCase):
    """The verdict travels Lakebase (verdict_to_dict -> jsonb -> verdict_from_dict)
    then the API (_comparison_to_dict). Every field the frontend renders must
    survive both hops intact."""

    def _verdict(self) -> JudgeVerdict:
        comparisons = (
            # Scalar AGREE (card field, card_index set).
            FieldComparison(
                "cards[].cardMeta.cardDisplayName", "Platinum", "Platinum",
                ComparisonOutcome.AGREE, FieldScope.SCALAR, card_index=0,
            ),
            # Transaction DISAGREE with both row indices + similarity.
            FieldComparison(
                "transactions[].amount", 100.0, 99.0,
                ComparisonOutcome.DISAGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=2, actual_row_index=1, similarity=0.91,
                rationale="off by one",
            ),
            # Type-1 UNMATCHED_ROW: Opus saw a PDF row Luna did not extract.
            FieldComparison(
                "transactions[].date", "2026-01-01", None,
                ComparisonOutcome.UNMATCHED_ROW, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=3, actual_row_index=None,
                rationale="PDF row had no description-similar extraction row",
            ),
        )
        return JudgeVerdict(
            "req-rt", "databricks-claude-opus-5", comparisons, 50.0,
            summary=json.dumps({"status": "OK"}),
        )

    def test_verdict_survives_lakebase_wire_round_trip(self):
        """verdict_to_dict -> JSON (jsonb) -> verdict_from_dict is lossless."""
        verdict = self._verdict()
        wire = json.dumps(verdict_to_dict(verdict))
        self.assertEqual(verdict_from_dict(json.loads(wire)), verdict)

    def test_api_serialisation_preserves_expected_actual_outcome_row_indices(self):
        """_comparison_to_dict on the restored verdict emits every field the
        frontend needs, in plain JSON (enums flattened to .value strings)."""
        verdict = self._verdict()
        restored = verdict_from_dict(json.loads(json.dumps(verdict_to_dict(verdict))))
        api = [_comparison_to_dict(c) for c in restored.comparisons]

        # Scalar AGREE.
        self.assertEqual(api[0]["field_path"], "cards[].cardMeta.cardDisplayName")
        self.assertEqual(api[0]["expected"], "Platinum")
        self.assertEqual(api[0]["actual"], "Platinum")
        self.assertEqual(api[0]["outcome"], "AGREE")
        self.assertEqual(api[0]["card_index"], 0)
        self.assertIsNone(api[0]["actual_row_index"])

        # Transaction DISAGREE — both row indices survive.
        self.assertEqual(api[1]["expected"], 100.0)
        self.assertEqual(api[1]["actual"], 99.0)
        self.assertEqual(api[1]["outcome"], "DISAGREE")
        self.assertEqual(api[1]["expected_row_index"], 2)
        self.assertEqual(api[1]["actual_row_index"], 1)
        self.assertEqual(api[1]["similarity"], 0.91)
        self.assertEqual(api[1]["rationale"], "off by one")
        self.assertEqual(api[1]["feedback_path"], "transactions.1.amount")

        # Type-1 UNMATCHED_ROW — actual_row_index is None; feedback_path
        # falls back to expected_row_index so the row still gets a path.
        self.assertEqual(api[2]["outcome"], "UNMATCHED_ROW")
        self.assertEqual(api[2]["expected"], "2026-01-01")
        self.assertIsNone(api[2]["actual"])
        self.assertEqual(api[2]["expected_row_index"], 3)
        self.assertIsNone(api[2]["actual_row_index"])
        self.assertEqual(api[2]["feedback_path"], "transactions.3.date")

    def test_api_serialisation_is_json_safe(self):
        """The API dicts must be JSON-serialisable (no enum/dataclass leaks)."""
        verdict = self._verdict()
        restored = verdict_from_dict(json.loads(json.dumps(verdict_to_dict(verdict))))
        api = [_comparison_to_dict(c) for c in restored.comparisons]
        json.dumps({"comparisons": api})  # must not raise


# ---------------------------------------------------------------------------
# (d) Comparison -> transaction-row mapping (the spec the frontend implements)
# ---------------------------------------------------------------------------

def group_verdict_comparisons(comparisons):
    """SPEC mirroring the ResultsView grouping logic in ``index.html``.

    Splits API-shaped comparison dicts (as ``_comparison_to_dict`` emits) into:

    - ``verdictByPath``: dict keyed by ``feedback_path`` for comparisons that
      map to a *rendered* field — matched rows (AGREE/DISAGREE/FORMAT_ONLY/
      ABSENT_IN_PDF) AND type-2 UNMATCHED_ROW (Luna extracted a row Opus did
      not see; ``actual_row_index`` is set, so it attaches to the Luna row).
    - ``unmatchedPdfRows``: list of ``{row_index, date, description, amount}``
      for type-1 UNMATCHED_ROW (Opus saw a PDF row Luna did not extract;
      ``actual_row_index`` is None — no rendered row to attach to), grouped
      three-per-row by ``expected_row_index``.

    Type-1 rows are split out BEFORE the by-path keying so a type-1 row and a
    matched row that happen to share a numeric index cannot collide/overwrite.
    """
    verdict_by_path = {}
    unmatched = []
    for c in comparisons:
        if c["outcome"] == "UNMATCHED_ROW" and c["actual_row_index"] is None:
            unmatched.append(c)
        elif c.get("feedback_path"):
            verdict_by_path[c["feedback_path"]] = c
    by_row = {}
    rows = []
    for c in unmatched:
        key = c["expected_row_index"]
        if key is None:
            continue
        if key not in by_row:
            by_row[key] = {"row_index": key, "date": None,
                           "description": None, "amount": None}
            rows.append(by_row[key])
        leaf = c["field_path"].split(".")[-1]
        by_row[key][leaf] = c["expected"]
    rows.sort(key=lambda r: r["row_index"])
    return verdict_by_path, rows


class VerdictMappingTest(unittest.TestCase):
    """Pins the comparison -> field/row mapping the frontend renders."""

    def _cmp(self, field_path, expected, actual, outcome, feedback_path,
             expected_row_index=None, actual_row_index=None):
        return {
            "field_path": field_path, "expected": expected, "actual": actual,
            "outcome": outcome, "feedback_path": feedback_path,
            "expected_row_index": expected_row_index,
            "actual_row_index": actual_row_index,
        }

    def test_matched_transaction_maps_by_actual_row_index(self):
        """Matched rows key into verdictByPath by feedback_path, which is built
        from actual_row_index — the Luna row index the frontend renders."""
        comps = [
            self._cmp("transactions[].date", "2026-01-01", "2026-01-01", "AGREE",
                      "transactions.0.date", expected_row_index=0, actual_row_index=0),
            self._cmp("transactions[].amount", 100.0, 99.0, "DISAGREE",
                      "transactions.1.amount", expected_row_index=2, actual_row_index=1),
        ]
        by_path, unmatched = group_verdict_comparisons(comps)
        self.assertEqual(set(by_path), {"transactions.0.date", "transactions.1.amount"})
        self.assertEqual(by_path["transactions.1.amount"]["expected"], 100.0)
        self.assertEqual(by_path["transactions.1.amount"]["actual"], 99.0)
        self.assertEqual(unmatched, [])

    def test_scalar_fields_key_by_feedback_path(self):
        """Scalar (card/reward) comparisons key by their concrete feedback_path."""
        comps = [
            self._cmp("cards[].cardMeta.cardDisplayName", "Platinum", "Platinum",
                      "AGREE", "cards.0.cardMeta.cardDisplayName", expected_row_index=None,
                      actual_row_index=None),
            self._cmp("rewards.closingPoints", 500, 500, "AGREE",
                      "rewards.closingPoints"),
        ]
        by_path, unmatched = group_verdict_comparisons(comps)
        self.assertEqual(set(by_path),
                         {"cards.0.cardMeta.cardDisplayName", "rewards.closingPoints"})
        self.assertEqual(unmatched, [])

    def test_type1_unmatched_row_grouped_by_expected_row_index(self):
        """Type-1 UNMATCHED_ROW (actual_row_index None): 3 fields per row
        collapse into one unmatchedPdfRows entry keyed by expected_row_index."""
        comps = [
            self._cmp("transactions[].date", "2026-03-01", None, "UNMATCHED_ROW",
                      "transactions.3.date", expected_row_index=3, actual_row_index=None),
            self._cmp("transactions[].description", "Amazon", None, "UNMATCHED_ROW",
                      "transactions.3.description", expected_row_index=3, actual_row_index=None),
            self._cmp("transactions[].amount", 50.0, None, "UNMATCHED_ROW",
                      "transactions.3.amount", expected_row_index=3, actual_row_index=None),
        ]
        by_path, unmatched = group_verdict_comparisons(comps)
        self.assertEqual(by_path, {})
        self.assertEqual(len(unmatched), 1)
        row = unmatched[0]
        self.assertEqual(row["row_index"], 3)
        self.assertEqual(row["date"], "2026-03-01")
        self.assertEqual(row["description"], "Amazon")
        self.assertEqual(row["amount"], 50.0)

    def test_type2_unmatched_row_maps_inline_by_actual_row_index(self):
        """Type-2 UNMATCHED_ROW (Luna extracted a row Opus didn't see):
        actual_row_index is set, so it keys into verdictByPath and renders
        inline on the Luna row — NOT into the 'not extracted' section."""
        comps = [
            self._cmp("transactions[].date", None, "2026-05-05", "UNMATCHED_ROW",
                      "transactions.2.date", expected_row_index=None, actual_row_index=2),
            self._cmp("transactions[].amount", None, 25.0, "UNMATCHED_ROW",
                      "transactions.2.amount", expected_row_index=None, actual_row_index=2),
        ]
        by_path, unmatched = group_verdict_comparisons(comps)
        self.assertEqual(set(by_path), {"transactions.2.date", "transactions.2.amount"})
        self.assertEqual(unmatched, [])

    def test_type1_unmatched_does_not_collide_with_matched_same_index(self):
        """A type-1 row (expected_row_index=3) and a matched row
        (actual_row_index=3) both produce feedback_path 'transactions.3.amount'
        but are DIFFERENT rows. The type-1 must go to unmatchedPdfRows and the
        matched one to verdictByPath — no collision/overwrite."""
        comps = [
            self._cmp("transactions[].amount", 50.0, None, "UNMATCHED_ROW",
                      "transactions.3.amount", expected_row_index=3, actual_row_index=None),
            self._cmp("transactions[].amount", 99.0, 99.0, "AGREE",
                      "transactions.3.amount", expected_row_index=5, actual_row_index=3),
        ]
        by_path, unmatched = group_verdict_comparisons(comps)
        self.assertEqual(set(by_path), {"transactions.3.amount"})
        # The matched row wins in by_path (actual==99), not the type-1 (actual==None).
        self.assertEqual(by_path["transactions.3.amount"]["actual"], 99.0)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["amount"], 50.0)
        self.assertEqual(unmatched[0]["row_index"], 3)

    def test_mixed_grouping(self):
        """One scalar AGREE, one type-1 row (3 fields), one type-2 field."""
        comps = [
            self._cmp("rewards.closingPoints", 500, 500, "AGREE",
                      "rewards.closingPoints"),
            self._cmp("transactions[].date", "2026-03-01", None, "UNMATCHED_ROW",
                      "transactions.3.date", expected_row_index=3, actual_row_index=None),
            self._cmp("transactions[].description", "Amazon", None, "UNMATCHED_ROW",
                      "transactions.3.description", expected_row_index=3, actual_row_index=None),
            self._cmp("transactions[].amount", 50.0, None, "UNMATCHED_ROW",
                      "transactions.3.amount", expected_row_index=3, actual_row_index=None),
            self._cmp("transactions[].date", None, "2026-05-05", "UNMATCHED_ROW",
                      "transactions.2.date", expected_row_index=None, actual_row_index=2),
        ]
        by_path, unmatched = group_verdict_comparisons(comps)
        self.assertEqual(set(by_path),
                         {"rewards.closingPoints", "transactions.2.date"})
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["row_index"], 3)
        self.assertEqual(unmatched[0]["description"], "Amazon")

    def test_unmatched_rows_sorted_by_row_index(self):
        comps = [
            self._cmp("transactions[].date", "2026-03-05", None, "UNMATCHED_ROW",
                      "transactions.5.date", expected_row_index=5, actual_row_index=None),
            self._cmp("transactions[].date", "2026-03-01", None, "UNMATCHED_ROW",
                      "transactions.1.date", expected_row_index=1, actual_row_index=None),
            self._cmp("transactions[].date", "2026-03-03", None, "UNMATCHED_ROW",
                      "transactions.3.date", expected_row_index=3, actual_row_index=None),
        ]
        _, unmatched = group_verdict_comparisons(comps)
        self.assertEqual([r["row_index"] for r in unmatched], [1, 3, 5])


# ---------------------------------------------------------------------------
# Frontend wiring — index.html content assertions
# ---------------------------------------------------------------------------

class FrontendVerdictInlineTest(unittest.TestCase):
    """Pins that index.html wires the verdict end to end: the /api/results
    fetch captures it (not discards it), ResultsView accepts and groups it,
    FieldRow/TxnCell render an inline badge, and type-1 UNMATCHED_ROW rows
    surface in a 'seen in PDF, not extracted' section."""

    @classmethod
    def setUpClass(cls):
        cls.html = _INDEX_HTML.read_text()

    def test_fetch_handler_captures_verdict(self):
        self.assertIn("verdict: apiResults.verdict || null", self.html)

    def test_resultsview_accepts_verdict_prop(self):
        self.assertIn(
            "function ResultsView({ requestId, extraction, complete, verdict, onVerdictRefresh })",
            self.html,
        )

    def test_app_passes_verdict_to_resultsview(self):
        self.assertIn("verdict=${results?.verdict}", self.html)
        self.assertIn("onVerdictRefresh=${handleVerdictRefresh}", self.html)

    def test_builds_verdict_lookup_structures(self):
        self.assertIn("verdictByPath", self.html)
        self.assertIn("unmatchedPdfRows", self.html)

    def test_unmatched_row_split_on_actual_row_index_null(self):
        """The split condition must separate type-1 (actual_row_index null)
        before by-path keying to avoid the index collision."""
        self.assertIn(
            'c.outcome === "UNMATCHED_ROW" && c.actual_row_index === null',
            self.html,
        )

    def test_renders_not_extracted_section(self):
        self.assertIn("Seen in PDF, not extracted", self.html)

    def test_fieldrow_accepts_verdict_prop(self):
        self.assertIn(
            "function FieldRow({ requestId, fieldPath, label, value, verdict })",
            self.html,
        )

    def test_txncell_accepts_verdict_prop(self):
        self.assertIn(
            "function TxnCell({ requestId, fieldPath, value, verdict })",
            self.html,
        )

    def test_verdict_badge_uses_existing_css_classes(self):
        """The badge uses the dynamic .verdict-<OUTCOME> CSS classes that
        already exist for all five outcomes."""
        self.assertIn('"verdict verdict-" + verdict.outcome', self.html)

    def test_verdict_passed_to_card_fields(self):
        self.assertIn(
            "verdict=${verdictByPath[`cards.${i}.cardMeta.cardDisplayName`]}",
            self.html,
        )

    def test_verdict_passed_to_transaction_cells(self):
        self.assertIn(
            "verdict=${verdictByPath[`transactions.${i}.date`]}",
            self.html,
        )

    def test_no_judge_panel_or_on_demand_button_added(self):
        """Scope guard: this task is inline-on-Results only — no expandable
        Judge Evaluation panel table or on-demand single-trace judge button
        was added to the Results view."""
        # 'Parse Another Statement' is the existing Results footer; a new
        # on-demand judge button would add a 'Judge' label here.
        self.assertIn("Parse Another Statement", self.html)

    def test_judge_button_renders_when_no_verdict(self):
        """When verdict is null the 'Judge this statement' button shows."""
        self.assertIn("Judge this statement", self.html)

    def test_rejudge_button_renders_when_verdict_present(self):
        """When a verdict exists a lighter 'Re-judge' affordance shows."""
        self.assertIn("Re-judge", self.html)

    def test_judge_spinner_css_exists(self):
        self.assertIn("judge-spinner", self.html)
        self.assertIn("@keyframes spin", self.html)

    def test_single_judge_hook_exists(self):
        self.assertIn("function useSingleJudge", self.html)

    def test_on_verdict_refresh_passed_to_resultsview(self):
        self.assertIn("onVerdictRefresh=${handleVerdictRefresh}", self.html)

    def test_handle_verdict_refresh_re_fetches_api_results(self):
        self.assertIn("handleVerdictRefresh", self.html)


# ---------------------------------------------------------------------------
# On-demand single-trace judge — backend helpers
# ---------------------------------------------------------------------------

class SingleJudgeSlotTest(unittest.TestCase):
    """The shared concurrency slot: acquire/release, 409-on-busy."""

    def setUp(self):
        import app.main as main_mod
        self._main = main_mod
        self._saved_running = main_mod._judge_running
        main_mod._judge_running = False

    def tearDown(self):
        self._main._judge_running = self._saved_running

    def test_acquire_when_idle(self):
        self.assertTrue(self._main._acquire_judge_slot())
        self.assertTrue(self._main._judge_running)

    def test_acquire_releases(self):
        self._main._acquire_judge_slot()
        self._main._release_judge_slot()
        self.assertFalse(self._main._judge_running)

    def test_acquire_fails_when_busy(self):
        """When a judge run is in progress, acquire returns False (→ 409)."""
        self._main._acquire_judge_slot()
        self.assertFalse(self._main._acquire_judge_slot())

    def test_release_allows_reacquire(self):
        """After release, the slot can be reacquired."""
        self._main._acquire_judge_slot()
        self._main._release_judge_slot()
        self.assertTrue(self._main._acquire_judge_slot())


class SingleJudgeBgRunnerTest(unittest.TestCase):
    """_run_single_judge_bg resolves, invokes score_trace with the
    result_store, and updates per-request status — force-rejudging even
    when the run is already tagged judged=true."""

    def setUp(self):
        import app.main as main_mod
        self._main = main_mod
        self._saved_running = main_mod._judge_running
        self._saved_status = dict(main_mod._single_judge_status)
        main_mod._single_judge_status.clear()
        main_mod._judge_running = False

    def tearDown(self):
        self._main._judge_running = self._saved_running
        self._main._single_judge_status.clear()
        self._main._single_judge_status.update(self._saved_status)

    def test_invokes_score_trace_with_run_id_and_result_store(self):
        """The bg runner calls score_trace(run_id, result_store=...) —
        reusing the existing single-trace scorer with the app's result store."""
        from tests.test_scorer import _FakeResultStore
        store = _FakeResultStore()

        with patch.object(self._main, "_get_stores", return_value=(store, None)), \
             patch("judge.scorer.score_trace", return_value={"status": "OK"}) as mock_score:
            self._main._run_single_judge_bg("req-1", "run-1")

        mock_score.assert_called_once_with("run-1", result_store=store)
        self.assertEqual(self._main._single_judge_status["req-1"]["status"], "done")

    def test_score_trace_failure_does_not_crash(self):
        """A score_trace exception lands in the status, never raised."""
        with patch.object(self._main, "_get_stores", return_value=(None, None)), \
             patch("judge.scorer.score_trace", side_effect=RuntimeError("boom")):
            self._main._run_single_judge_bg("req-1", "run-1")

        self.assertEqual(self._main._single_judge_status["req-1"]["status"], "error")

    def test_judge_error_status_surfaces(self):
        """A JUDGE_ERROR result is surfaced as an error status."""
        with patch.object(self._main, "_get_stores", return_value=(None, None)), \
             patch("judge.scorer.score_trace", return_value={"status": "JUDGE_ERROR"}):
            self._main._run_single_judge_bg("req-1", "run-1")

        self.assertEqual(self._main._single_judge_status["req-1"]["status"], "error")
        self.assertIn("JUDGE_ERROR", self._main._single_judge_status["req-1"]["error"])

    def test_force_rejudge_ignores_judged_true_tag(self):
        """score_trace is called regardless of whether the run is already
        tagged judged=true — force-rejudge is inherent (score_trace does NOT
        check the tag; only the batch sampler skips already-judged runs)."""
        with patch.object(self._main, "_get_stores", return_value=(None, None)), \
             patch("judge.scorer.score_trace", return_value={"status": "OK"}) as mock_score:
            # Simulate a run that is ALREADY judged=true (the tag exists).
            self._main._run_single_judge_bg("req-already", "run-already")

        # score_trace was still called — force re-judge.
        mock_score.assert_called_once()

    def test_releases_slot_on_completion(self):
        """The concurrency slot is released in the finally block."""
        with patch.object(self._main, "_get_stores", return_value=(None, None)), \
             patch("judge.scorer.score_trace", return_value={"status": "OK"}):
            self._main._run_single_judge_bg("req-1", "run-1")
        self.assertFalse(self._main._judge_running)

    def test_releases_slot_on_failure(self):
        """The slot is released even when score_trace raises."""
        with patch.object(self._main, "_get_stores", return_value=(None, None)), \
             patch("judge.scorer.score_trace", side_effect=RuntimeError("boom")):
            self._main._run_single_judge_bg("req-1", "run-1")
        self.assertFalse(self._main._judge_running)


class ResolveRunIdIntegrationTest(unittest.TestCase):
    """The endpoint resolves request_id → run_id via resolve_run_id; when
    None, the endpoint returns 404 (not 500). This tests the resolution
    integration with the scorer's search helper."""

    def setUp(self):
        import sys
        from tests.test_scorer import _install_fake_mlflow
        self._install = _install_fake_mlflow
        self.fake_mlflow = self._install()
        import judge.scorer as scorer_mod
        scorer_mod._mlflow_configured = False

    def tearDown(self):
        import sys
        sys.modules.pop("mlflow", None)
        sys.modules.pop("mlflow.tracking", None)

    def test_resolve_found_then_judge_succeeds(self):
        """resolve_run_id returns a run_id → bg runner judges that run."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_search_runs_result(["run-target"])
        run_id = resolve_run_id("req-target")
        self.assertEqual(run_id, "run-target")

    def test_resolve_not_found_returns_none(self):
        """resolve_run_id returns None → endpoint would 404 (never 500)."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_search_runs_result([])
        run_id = resolve_run_id("req-orphan")
        self.assertIsNone(run_id)


if __name__ == "__main__":
    unittest.main()
