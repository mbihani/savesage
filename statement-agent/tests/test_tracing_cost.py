"""Stdlib unit tests for token-usage and explicit-cost builders (WS4, requirement 5).

No mlflow import — exercises harness/tracing_cost.py. Verifies cost is set
explicitly (never relied on auto) and is 0.0 when a model has no configured rate.
"""

import unittest

from contracts.models import TokenUsage
from harness.tracing_cost import cost_attributes, model_attributes, usage_attributes


class UsageAttributesTest(unittest.TestCase):
    def test_token_usage_dataclass(self):
        u = usage_attributes(TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150))
        self.assertEqual(u, {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})

    def test_dict_usage(self):
        u = usage_attributes({"input_tokens": 1, "output_tokens": 2})
        self.assertEqual(u, {"input_tokens": 1, "output_tokens": 2})

    def test_none_when_no_usage(self):
        self.assertIsNone(usage_attributes(None))
        self.assertIsNone(usage_attributes(TokenUsage()))

    def test_drops_none_fields(self):
        u = usage_attributes(TokenUsage(input_tokens=10, output_tokens=None, total_tokens=None))
        self.assertEqual(u, {"input_tokens": 10})


class CostAttributesTest(unittest.TestCase):
    def test_priced_model(self):
        rates = {"databricks-gpt-5-6-luna": {"input": 1.0, "output": 2.0}}
        c = cost_attributes(TokenUsage(input_tokens=1_000_000, output_tokens=500_000, total_tokens=1_500_000),
                            "databricks-gpt-5-6-luna", rates)
        self.assertAlmostEqual(c["input_cost"], 1.0)
        self.assertAlmostEqual(c["output_cost"], 1.0)
        self.assertAlmostEqual(c["total_cost"], 2.0)

    def test_unpriced_model_returns_explicit_zeros(self):
        c = cost_attributes(TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                            "unknown-model", {})
        self.assertEqual(c, {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0})

    def test_no_usage_returns_none(self):
        self.assertIsNone(cost_attributes(None, "m", {}))

    def test_zero_tokens_priced_zero(self):
        rates = {"m": {"input": 5.0, "output": 5.0}}
        c = cost_attributes(TokenUsage(0, 0, 0), "m", rates)
        self.assertEqual(c, {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0})


class ModelAttributesTest(unittest.TestCase):
    def test_both_set(self):
        a = model_attributes("databricks-gpt-5-6-luna", "databricks-gpt-5-6-luna")
        self.assertEqual(a["mlflow.llm.model"], "databricks-gpt-5-6-luna")
        self.assertEqual(a["mlflow.llm.provider"], "databricks-ai-gateway")

    def test_neither_empty(self):
        self.assertEqual(model_attributes(None, None), {})


if __name__ == "__main__":
    unittest.main()
