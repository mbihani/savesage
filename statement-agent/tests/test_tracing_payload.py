"""Tests that MLflow spans carry actual payload data via set_inputs/set_outputs.

Verifies the fix for "traces appear but spans are nearly empty": the MLflow sink
now calls ``span.set_inputs()`` / ``span.set_outputs()`` (not just
``set_attributes``) so the trace view shows the real extraction payload, bank,
model_id, validation result, etc.  PII nested inside the payload (cardholder
name, transaction descriptions) must be redacted by the recursive scrubber.
"""

from datetime import UTC, datetime
import unittest

from contracts.models import Bank, TraceEvent
from graph.fakes import FakeExtractionAdapter, InMemoryResultStore, make_synthetic_request
from graph.graph import run_graph
from graph.nodes import NodeDeps
from graph.state import GraphState
from harness.config_ws4 import TracingConfig
from harness.tracing import MLflowTraceSink


class _RecordingSpan:
    """Fake live span that records set_inputs/set_outputs calls."""

    def __init__(self, name, trace_id):
        self.name = name
        self.trace_id = trace_id
        self.span_id = f"span-{name}"
        self.attributes = {}
        self.inputs = None
        self.outputs = None
        self.ended = False
        self.end_status = None

    def set_attributes(self, attrs):
        self.attributes.update(attrs)

    def set_attribute(self, k, v):
        self.attributes[k] = v

    def set_inputs(self, inputs):
        self.inputs = inputs

    def set_outputs(self, outputs):
        self.outputs = outputs

    def record_exception(self, exc):
        pass

    def end(self, outputs=None, attributes=None, status=None, end_time_ns=None):
        self.ended = True
        self.end_status = status


class _RecordingMLflow:
    """Fake mlflow that records every span and every log_param/log_metric call."""

    def __init__(self):
        self.spans: list[_RecordingSpan] = []
        self.params: dict[str, object] = {}
        self.metrics: dict[str, float] = {}
        self._trace_id = "tr-test-001"
        self.end_run_calls = 0

    def set_tracking_uri(self, uri):
        pass

    def set_experiment(self, path):
        pass

    class tracing:
        enable = staticmethod(lambda: None)

    class langchain:
        autolog = staticmethod(lambda **kw: None)

    def start_run(self):
        class _Info:
            run_id = "run-test-001"
        class _Run:
            info = _Info()
        return _Run()

    def end_run(self):
        self.end_run_calls += 1

    def log_param(self, key, value):
        self.params[key] = value

    def log_metric(self, key, value):
        self.metrics[key] = value

    def log_artifact(self, path, artifact_path=None):
        pass

    def start_span_no_context(self, *, name, span_type, parent_span=None, start_time_ns=None):
        span = _RecordingSpan(name, self._trace_id)
        self.spans.append(span)
        return span


def _config():
    return TracingConfig(
        enabled=True, tracking_uri="databricks", databricks_profile="fevm-stable",
        experiment_path="/x", autolog_langchain=False,
    )


def _run_graph_with_sink(fake_mlflow):
    """Run the full parse pipeline through the MLflowTraceSink."""
    sink = MLflowTraceSink(_config(), mlflow_factory=lambda: fake_mlflow)
    store = InMemoryResultStore()
    deps = NodeDeps(
        extraction=FakeExtractionAdapter(),
        result_store=store,
        trace_sink=sink,
    )
    state = GraphState(request=make_synthetic_request(Bank.HDFC))
    run_graph(deps, state)
    return fake_mlflow, state


class SpanPayloadTest(unittest.TestCase):
    """Spans must carry inputs/outputs, not just metadata attributes."""

    def test_root_parse_span_has_inputs_and_outputs(self):
        fake, state = _run_graph_with_sink(_RecordingMLflow())
        parse_spans = [s for s in fake.spans if s.name == "parse"]
        self.assertEqual(len(parse_spans), 1)
        span = parse_spans[0]

        # Inputs: request identity (bank visible; filename redacted by PII policy).
        self.assertIsNotNone(span.inputs)
        self.assertEqual(span.inputs["request_id"], state.request_id)
        self.assertEqual(span.inputs["bank"], "HDFC")
        # "filename" is a PII key substring → value fully redacted.
        self.assertEqual(span.inputs["filename"], "[REDACTED]")

        # Outputs: the full extraction payload + outcome.
        self.assertIsNotNone(span.outputs)
        self.assertIn("extraction", span.outputs)
        self.assertEqual(span.outputs["outcome"], "SUCCESS")
        self.assertTrue(span.outputs["schema_valid"])
        # The extraction payload structure is visible.
        extraction = span.outputs["extraction"]
        self.assertIsInstance(extraction, dict)
        self.assertIn("cards", extraction)
        self.assertIn("transactions", extraction)
        self.assertIn("rewards", extraction)

    def test_extract_span_carries_payload_and_model(self):
        fake, state = _run_graph_with_sink(_RecordingMLflow())
        extract_spans = [s for s in fake.spans if s.name == "extract"]
        self.assertEqual(len(extract_spans), 1)
        span = extract_spans[0]

        # Inputs: bank + model_id.
        self.assertIsNotNone(span.inputs)
        self.assertEqual(span.inputs["bank"], "HDFC")
        self.assertEqual(span.inputs["model_id"], "fake-luna")

        # Outputs: extraction payload + metadata.
        self.assertIsNotNone(span.outputs)
        self.assertEqual(span.outputs["model_id"], "fake-luna")
        self.assertIn("extraction", span.outputs)
        self.assertIn("latency_ms", span.outputs)

        # Attributes include model_id, latency_ms, token_usage for cost attribution.
        self.assertIn("model_id", span.attributes)
        self.assertIn("latency_ms", span.attributes)

    def test_validate_span_carries_payload_and_result(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        validate_spans = [s for s in fake.spans if s.name == "validate"]
        self.assertEqual(len(validate_spans), 1)
        span = validate_spans[0]

        # Inputs: the extraction payload being validated.
        self.assertIsNotNone(span.inputs)
        self.assertIn("extraction", span.inputs)

        # Outputs: validation result.
        self.assertIsNotNone(span.outputs)
        self.assertTrue(span.outputs["schema_valid"])
        self.assertIsInstance(span.outputs["validation_errors"], list)

    def test_persist_span_carries_request_id_and_persisted_flag(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        persist_spans = [s for s in fake.spans if s.name == "persist_extraction"]
        self.assertEqual(len(persist_spans), 1)
        span = persist_spans[0]

        self.assertIsNotNone(span.inputs)
        self.assertIn("request_id", span.inputs)
        self.assertIsNotNone(span.outputs)
        self.assertTrue(span.outputs["persisted"])

    def test_finalize_span_carries_outcome_and_extraction(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        finalize_spans = [s for s in fake.spans if s.name == "finalize"]
        self.assertEqual(len(finalize_spans), 1)
        span = finalize_spans[0]

        self.assertIsNotNone(span.outputs)
        self.assertEqual(span.outputs["outcome"], "SUCCESS")
        self.assertIn("extraction", span.outputs)
        self.assertTrue(span.outputs["schema_valid"])

    def test_route_span_carries_bank(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        route_spans = [s for s in fake.spans if s.name == "route"]
        self.assertEqual(len(route_spans), 1)
        span = route_spans[0]

        self.assertIsNotNone(span.inputs)
        self.assertEqual(span.inputs["bank"], "HDFC")
        self.assertIsNotNone(span.outputs)
        self.assertEqual(span.outputs["bank"], "HDFC")
        self.assertTrue(span.outputs["prompt_resolved"])


class SpanPIIRedactionTest(unittest.TestCase):
    """PII in span inputs/outputs must be redacted by the recursive scrubber."""

    def test_cardholder_name_redacted_in_extract_outputs(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        extract_span = next(s for s in fake.spans if s.name == "extract")
        extraction = extract_span.outputs["extraction"]
        # cards[0].cardMeta.cardDisplayName → key contains "carddisplayname" → [REDACTED]
        card_meta = extraction["cards"][0]["cardMeta"]
        self.assertEqual(card_meta["cardDisplayName"], "[REDACTED]")
        # Non-PII fields survive.
        self.assertEqual(card_meta["lastFourDigit"], "0000")
        self.assertEqual(card_meta["network"], "VISA")

    def test_transaction_descriptions_redacted_in_root_outputs(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        parse_span = next(s for s in fake.spans if s.name == "parse")
        extraction = parse_span.outputs["extraction"]
        for txn in extraction["transactions"]:
            # "description" is a PII key substring → [REDACTED]
            self.assertEqual(txn["description"], "[REDACTED]")
            # Non-PII fields survive.
            self.assertIn("date", txn)
            self.assertIn("amount", txn)

    def test_statement_meta_redacted_in_outputs(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        parse_span = next(s for s in fake.spans if s.name == "parse")
        extraction = parse_span.outputs["extraction"]
        meta = extraction["statementMeta"]
        # "issuerName" → lowercased "issuername" contains "name" → [REDACTED]
        self.assertEqual(meta["issuerName"], "[REDACTED]")
        # Non-PII fields survive (rawStatementId lowercased "rawstatementid"
        # does NOT contain "statement_id" — no underscore — so it is NOT redacted).
        self.assertEqual(meta["rawStatementId"], "synthetic-001")
        self.assertEqual(meta["statementDate"], "01/04/2026")


class RunParamsMetricsTest(unittest.TestCase):
    """The MLflow run should carry useful params and metrics."""

    def test_params_logged_on_run(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        self.assertEqual(fake.params.get("bank"), "HDFC")
        self.assertEqual(fake.params.get("outcome"), "SUCCESS")
        self.assertEqual(fake.params.get("model_id"), "fake-luna")

    def test_metrics_logged_on_run(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        self.assertIn("n_transactions", fake.metrics)
        self.assertGreater(fake.metrics["n_transactions"], 0)

    def test_end_run_called_once(self):
        fake, _ = _run_graph_with_sink(_RecordingMLflow())
        self.assertEqual(fake.end_run_calls, 1)


class EmptySpansWhenNoPayloadTest(unittest.TestCase):
    """Events without inputs/outputs (e.g. test helpers) must not break —
    set_inputs/set_outputs are simply not called."""

    def test_event_without_inputs_outputs_skips_set_calls(self):
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: fake)
        now = datetime.now(UTC)
        # An event with no inputs/outputs (the default).
        sink.record(TraceEvent(
            request_id="r1", name="parse", started_at=now, ended_at=now,
            attributes={"bank": "HDFC"}, span_id="s-parse", parent_span_id=None,
        ))
        span = fake.spans[0]
        # set_inputs/set_outputs were NOT called (inputs/outputs stayed None).
        self.assertIsNone(span.inputs)
        self.assertIsNone(span.outputs)
        # But attributes were still set.
        self.assertEqual(span.attributes.get("bank"), "HDFC")


if __name__ == "__main__":
    unittest.main()
