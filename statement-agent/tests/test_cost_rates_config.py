"""Stdlib tests for the deploy-time cost-rate configuration (WS4, requirement 5).

Covers the config-only change that makes per-statement parse cost appear in MLflow
traces: (a) ``_DEFAULT_COST_RATES`` now carries the Luna extraction model's real
rates, so ``cost_attributes`` returns non-zero cost for it; (b) ``get_tracing_config``
honours a deploy-time ``WS4_COST_RATES_JSON`` override (best-effort, never raises).

No mlflow/langgraph import — exercises only ``harness/config_ws4.py`` (stdlib-only)
and ``harness/tracing_cost.py`` (stdlib-only). The span->trace auto-aggregation path
in ``harness/tracing.py`` is intentionally NOT touched here; this only asserts the
rate table that feeds ``cost_attributes(usage, model, cfg.cost_rates_per_million)``.
"""

import json
import os
import unittest
from unittest.mock import patch

from contracts.models import TokenUsage
from harness.config_ws4 import _DEFAULT_COST_RATES, get_tracing_config
from harness.tracing_cost import cost_attributes

# The AI-Gateway endpoint name (EXTRACTION_ENDPOINT) — kept as a rate-table alias.
_LUNA = "databricks-gpt-5-6-luna"
# The model_id the Luna AI-Gateway response actually returns in its ``model``
# field — this is what the extract span records and what ``cost_attributes``
# looks up (verified from the live MLflow run param / trace). It is the EFFECTIVE
# cost-lookup key: without it keyed, cost stays $0 even at a non-zero rate.
_LUNA_MODEL = "gpt-5.6-luna"
_JUDGE = "databricks-claude-opus-5"


def _defaults_copy() -> dict[str, dict[str, float]]:
    """A fresh shallow+inner copy of _DEFAULT_COST_RATES (mirrors config_ws4)."""
    return {k: dict(v) for k, v in _DEFAULT_COST_RATES.items()}


class DefaultRatesTest(unittest.TestCase):
    """The hardcoded defaults — the single source of real rates without an env override."""

    def test_luna_default_rates_are_real(self) -> None:
        # Both the effective span model_id and the endpoint-name alias carry the
        # real rate.
        self.assertEqual(_DEFAULT_COST_RATES[_LUNA], {"input": 0.2, "output": 1.2})
        self.assertEqual(_DEFAULT_COST_RATES[_LUNA_MODEL], {"input": 0.2, "output": 1.2})

    def test_judge_default_rate_stays_zero(self) -> None:
        # No judge rate was provided, and the judge path captures no usage yet —
        # out of scope here. It must stay an explicit 0.0, never silently absent.
        self.assertEqual(_DEFAULT_COST_RATES[_JUDGE], {"input": 0.0, "output": 0.0})


class CostAttributesWithDefaultRatesTest(unittest.TestCase):
    """cost_attributes priced against the (new) default rate table."""

    def test_luna_priced_from_default_rates(self) -> None:
        # cost = tokens * rate / 1_000_000 (tracing_cost.py). With 1000 in / 500 out
        # at 0.2/1.2 per 1M: input 0.0002, output 0.0006, total 0.0008.
        rates = _defaults_copy()
        c = cost_attributes(
            TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
            _LUNA, rates,
        )
        # Assert against the exact same arithmetic cost_attributes performs, so the
        # comparison is bit-identical (not float-literal repr sensitive). Note
        # cost_attributes computes total_cost = input_cost + output_cost (NOT
        # (in*r_in + out*r_out)/1e6 — the grouping changes the last float bit).
        expected_input = 1000 * 0.2 / 1_000_000   # 0.0002
        expected_output = 500 * 1.2 / 1_000_000   # 0.0006
        self.assertEqual(c["input_cost"], expected_input)
        self.assertEqual(c["output_cost"], expected_output)
        self.assertEqual(c["total_cost"], expected_input + expected_output)  # 0.0008
        self.assertNotEqual(c["input_cost"], 0.0)
        self.assertNotEqual(c["output_cost"], 0.0)
        self.assertNotEqual(c["total_cost"], 0.0)

    def test_span_model_id_is_priced_nonzero(self) -> None:
        # THE goal test: the model_id the extract span ACTUALLY records is
        # ``gpt-5.6-luna`` (the AI-Gateway ``model`` field), not the endpoint name.
        # cost_attributes must hit a rate for it and return non-zero cost — this
        # is what makes per-statement parse cost appear in MLflow traces. With
        # only the endpoint-name key keyed (the original gap), this returned
        # explicit zeros and the trace cost stayed $0.
        rates = _defaults_copy()
        usage = TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
        c = cost_attributes(usage, _LUNA_MODEL, rates)
        expected_input = 1000 * 0.2 / 1_000_000   # 0.0002
        expected_output = 500 * 1.2 / 1_000_000   # 0.0006
        self.assertEqual(c["input_cost"], expected_input)
        self.assertEqual(c["output_cost"], expected_output)
        self.assertEqual(c["total_cost"], expected_input + expected_output)
        self.assertGreater(c["total_cost"], 0.0)

    def test_luna_cost_is_nonzero_with_realistic_usage(self) -> None:
        # A realistic extraction: ~12k prompt + ~3k completion tokens.
        rates = _defaults_copy()
        c = cost_attributes(
            TokenUsage(input_tokens=12_000, output_tokens=3_000, total_tokens=15_000),
            _LUNA, rates,
        )
        self.assertEqual(c["input_cost"], 12_000 * 0.2 / 1_000_000)
        self.assertEqual(c["output_cost"], 3_000 * 1.2 / 1_000_000)
        self.assertGreater(c["total_cost"], 0.0)

    def test_pipeline_uses_config_defaults_without_env(self) -> None:
        # End-to-end config -> cost: with no WS4_COST_RATES_JSON, get_tracing_config()
        # must hand the non-zero Luna rate to cost_attributes for the model_id the
        # extract span ACTUALLY records (gpt-5.6-luna) — this is the path
        # tracing.py:491 uses: cost_attributes(usage, model, cfg.cost_rates_per_million).
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WS4_COST_RATES_JSON", None)
            cfg = get_tracing_config()
        c = cost_attributes(
            TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
            _LUNA_MODEL, cfg.cost_rates_per_million,
        )
        expected_input = 1000 * 0.2 / 1_000_000
        expected_output = 500 * 1.2 / 1_000_000
        self.assertEqual(c["input_cost"], expected_input)
        self.assertEqual(c["output_cost"], expected_output)
        self.assertEqual(c["total_cost"], expected_input + expected_output)
        self.assertGreater(c["total_cost"], 0.0)

    def test_unknown_model_returns_explicit_zeros(self) -> None:
        # Existing behaviour preserved: a model with no configured rate records
        # explicit 0.0 — never silently absent.
        rates = _defaults_copy()
        c = cost_attributes(
            TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
            "unknown-model", rates,
        )
        self.assertEqual(c, {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0})

    def test_judge_model_stays_zero_with_defaults(self) -> None:
        rates = _defaults_copy()
        c = cost_attributes(
            TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500),
            _JUDGE, rates,
        )
        self.assertEqual(c, {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0})


class EnvCostOverrideTest(unittest.TestCase):
    """get_tracing_config() honours WS4_COST_RATES_JSON, best-effort, hermetic."""

    def setUp(self) -> None:
        # Save/restore so every test starts with WS4_COST_RATES_JSON absent,
        # regardless of the ambient environment (hermetic).
        self._saved = os.environ.pop("WS4_COST_RATES_JSON", None)

    def tearDown(self) -> None:
        os.environ.pop("WS4_COST_RATES_JSON", None)
        if self._saved is not None:
            os.environ["WS4_COST_RATES_JSON"] = self._saved

    def test_valid_json_overrides_model_rate(self) -> None:
        override = json.dumps({_LUNA: {"input": 5.0, "output": 10.0}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()
        rates = cfg.cost_rates_per_million
        self.assertEqual(rates[_LUNA], {"input": 5.0, "output": 10.0})
        # The judge model is untouched by this override.
        self.assertEqual(rates[_JUDGE], {"input": 0.0, "output": 0.0})
        # And cost_attributes reflects the override end-to-end.
        c = cost_attributes(
            TokenUsage(input_tokens=1_000_000, output_tokens=500_000, total_tokens=1_500_000),
            _LUNA, rates,
        )
        self.assertEqual(c["input_cost"], 1_000_000 * 5.0 / 1_000_000)   # 5.0
        self.assertEqual(c["output_cost"], 500_000 * 10.0 / 1_000_000)   # 5.0
        self.assertEqual(c["total_cost"], 10.0)

    def test_partial_override_inherits_other_rate(self) -> None:
        # Override only input; output must inherit the default (shallow per-model merge).
        override = json.dumps({_LUNA: {"input": 9.0}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()
        self.assertEqual(cfg.cost_rates_per_million[_LUNA], {"input": 9.0, "output": 1.2})

    def test_override_adds_new_model(self) -> None:
        override = json.dumps({"some-new-model": {"input": 3.0, "output": 4.0}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()
        rates = cfg.cost_rates_per_million
        self.assertEqual(rates["some-new-model"], {"input": 3.0, "output": 4.0})
        # Defaults still present alongside the new model.
        self.assertEqual(rates[_LUNA], {"input": 0.2, "output": 1.2})

    def test_empty_string_uses_defaults(self) -> None:
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": ""}):
            cfg = get_tracing_config()
        self.assertEqual(cfg.cost_rates_per_million[_LUNA], {"input": 0.2, "output": 1.2})

    def test_missing_var_uses_defaults(self) -> None:
        # Var is guaranteed absent by setUp.
        cfg = get_tracing_config()
        self.assertEqual(cfg.cost_rates_per_million[_LUNA], {"input": 0.2, "output": 1.2})

    def test_invalid_json_uses_defaults_no_raise(self) -> None:
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": "{not valid json"}):
            cfg = get_tracing_config()  # must not raise
        self.assertEqual(cfg.cost_rates_per_million[_LUNA], {"input": 0.2, "output": 1.2})

    def test_non_dict_json_uses_defaults_no_raise(self) -> None:
        # A JSON array is valid JSON but not a JSON object.
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": "[1, 2, 3]"}):
            cfg = get_tracing_config()  # must not raise
        self.assertEqual(cfg.cost_rates_per_million[_LUNA], {"input": 0.2, "output": 1.2})

    def test_non_numeric_rate_is_skipped(self) -> None:
        # A non-numeric rate for one key must be skipped (inherit default), while a
        # numeric rate for the other key is applied — never crash cost_attributes.
        override = json.dumps({_LUNA: {"input": "not-a-number", "output": 7.0}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()
        self.assertEqual(cfg.cost_rates_per_million[_LUNA], {"input": 0.2, "output": 7.0})

    def test_huge_integer_rate_does_not_raise(self) -> None:
        # A 401-digit JSON integer overflows float() (OverflowError, NOT
        # ValueError). Without OverflowError in the caught set this propagates
        # and breaks the never-raises contract. The rate must be skipped (model
        # keeps its default) and get_tracing_config() must return normally.
        # json.dumps serializes 10**400 as a bare integer literal (no quotes),
        # and json.loads parses it back as a Python int (arbitrary precision).
        override = json.dumps({_LUNA_MODEL: {"input": 10 ** 400}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()  # must not raise
        # input kept its default (0.2); output was never overridden (1.2).
        self.assertEqual(cfg.cost_rates_per_million[_LUNA_MODEL],
                         {"input": 0.2, "output": 1.2})

    def test_infinity_rate_is_skipped(self) -> None:
        # json.loads accepts the Infinity token (Python extension) → float("inf").
        # A non-finite rate is skipped so the model keeps its default.
        override = json.dumps({_LUNA_MODEL: {"input": float("inf"), "output": float("-inf")}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()  # must not raise
        self.assertEqual(cfg.cost_rates_per_million[_LUNA_MODEL],
                         {"input": 0.2, "output": 1.2})

    def test_nan_rate_is_skipped(self) -> None:
        # json.loads accepts the NaN token → float("nan"). Non-finite → skipped.
        override = json.dumps({_LUNA_MODEL: {"input": float("nan"), "output": float("nan")}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()  # must not raise
        self.assertEqual(cfg.cost_rates_per_million[_LUNA_MODEL],
                         {"input": 0.2, "output": 1.2})

    def test_negative_rate_is_skipped(self) -> None:
        # A negative rate would produce a negative (invalid) cost on the trace.
        # It is skipped so the model keeps its default.
        override = json.dumps({_LUNA_MODEL: {"input": -5.0, "output": -0.01}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()
        self.assertEqual(cfg.cost_rates_per_million[_LUNA_MODEL],
                         {"input": 0.2, "output": 1.2})

    def test_valid_rate_applied_alongside_nonfinite_rate(self) -> None:
        # Per-rate granularity: a valid input rate is applied while a non-finite
        # output rate is skipped (inherits the default). Proves a bad rate for
        # one key does not poison the other.
        override = json.dumps({_LUNA_MODEL: {"input": 9.0, "output": float("inf")}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            cfg = get_tracing_config()
        self.assertEqual(cfg.cost_rates_per_million[_LUNA_MODEL],
                         {"input": 9.0, "output": 1.2})

    def test_config_does_not_mutate_module_defaults(self) -> None:
        # The merged table must be a copy; an override must not leak into the
        # module-level _DEFAULT_COST_RATES (which other tests/calls rely on).
        before = _defaults_copy()
        override = json.dumps({_LUNA: {"input": 99.0, "output": 99.0}})
        with patch.dict(os.environ, {"WS4_COST_RATES_JSON": override}):
            get_tracing_config()
        self.assertEqual(_DEFAULT_COST_RATES, before)


if __name__ == "__main__":
    unittest.main()
