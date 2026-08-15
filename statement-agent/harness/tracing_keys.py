"""MLflow attribute/span/assessment key constants.

These mirror ``mlflow.entities.span.SpanAttributeKey``, ``mlflow.entities.span.SpanType``
and ``mlflow.entities.AssessmentSourceType`` exactly so the pure-logic telemetry
builders in this package never need to import mlflow (keeping them stdlib-testable,
per CONTRACTS.md). The string values are part of MLflow's stable on-the-wire span
format; if they drift in a future mlflow release, update this file and the note.

Verified against the locally importable ``mlflow 3.10.1`` (requirements.txt pins
``mlflow[databricks]==3.2.0``); the keys below are identical in both.
"""

# --- Span attributes (mlflow.entities.span.SpanAttributeKey) ---
SPAN_ATTR_MODEL = "mlflow.llm.model"
SPAN_ATTR_MODEL_PROVIDER = "mlflow.llm.provider"
SPAN_ATTR_CHAT_USAGE = "mlflow.chat.tokenUsage"  # {input,output,total}_tokens
SPAN_ATTR_LLM_COST = "mlflow.llm.cost"  # {input,output,total}_cost in USD
SPAN_ATTR_MESSAGE_FORMAT = "mlflow.message.format"

# --- Span types (mlflow.entities.span.SpanType) ---
SPAN_TYPE_LLM = "LLM"
SPAN_TYPE_CHAIN = "CHAIN"
SPAN_TYPE_AGENT = "AGENT"
SPAN_TYPE_TOOL = "TOOL"
SPAN_TYPE_PARSER = "PARSER"
SPAN_TYPE_GUARDRAIL = "GUARDRAIL"
SPAN_TYPE_EVALUATOR = "EVALUATOR"
SPAN_TYPE_UNKNOWN = "UNKNOWN"

# --- Assessment source types (mlflow.entities.AssessmentSourceType) ---
ASSESSMENT_HUMAN = "HUMAN"
ASSESSMENT_CODE = "CODE"
ASSESSMENT_LLM_JUDGE = "LLM_JUDGE"
ASSESSMENT_AI_JUDGE = "AI_JUDGE"

# log_feedback assessment names.
FEEDBACK_ASSESSMENT_NAME = "field_feedback"
JUDGE_ASSESSMENT_NAME = "judge_accuracy"

VERIFIED_MLFLOW_VERSION = "3.10.1"
DECLARED_MLFLOW_VERSION = "3.2.0"
