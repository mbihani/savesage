"""Pure field-wise client feedback payload construction (stdlib-only, no mlflow).

The client accepts or corrects INDIVIDUAL fields. Each corrected/accepted field
becomes one ``mlflow.log_feedback`` assessment carrying: the canonical field path
(from contracts.paths), the original value, the corrected value, the accepted
bool, and the actor. Field paths use contracts.paths.canonical_feedback_path /
is_valid_feedback_path — never a bespoke notation (WS3 persists and WS6 sends the
same format; three notations would silently break the loop).

PII decision (visible, not buried): by default NO raw PII reaches MLflow. The
tiered redaction policy, guided by rules/pii.py:

- ``cardDisplayName`` and transaction ``description`` -> SHA-256 hash string.
  These are cardholder names / free-text descriptions = client PII.
- ``lastFourDigit`` -> kept as-is. It is already the PII-safe redacted form by
  definition (last four only); logging it is harmless and useful for card identity.
- ``amount``, ``date``, ``rewards.pointsEarnedThisCycle``,
  ``rewards.closingPoints`` -> kept raw. Amounts and dates are NOT cardholder-
  identifying per rules/pii.py and are essential to correction analytics; hashing
  them would destroy the feedback's meaning. (A date+amount *pair* could in
  principle re-identify, but they are logged only within a single-field feedback
  assessment, never joined back to a cardholder by this code.)
- any other leaf -> hashed defensively.

This keeps the feedback meaningful for accept/correct analytics while never
tracing cardholder names, card numbers, or full transaction descriptions.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from contracts.models import FieldFeedback

from .tracing_keys import ASSESSMENT_HUMAN, FEEDBACK_ASSESSMENT_NAME

# Field-path leaves that ARE client PII -> hash.
_PII_LEAVES = frozenset({"cardDisplayName", "description"})
# Field-path leaves that are NOT PII and essential to analytics -> keep raw.
_KEEP_LEAVES = frozenset(
    {"lastFourDigit", "amount", "date", "pointsEarnedThisCycle", "closingPoints"}
)


def _leaf(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _to_primitive(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def redact_feedback_value(
    field_path: str,
    value: Any,
    *,
    redact_pii: bool = True,
    log_nonpii: bool = True,
) -> Any:
    """Tiered PII redaction of one feedback value -> a JSON primitive.

    See module docstring for the policy. Returns a value suitable for
    ``mlflow.log_feedback`` metadata (str/int/float/bool/None).
    """
    leaf = _leaf(field_path)
    raw = _to_primitive(value)
    if not redact_pii:
        # Operator disabled redaction -> raw values (the documented exception).
        return raw
    if leaf in _KEEP_LEAVES:
        return raw if log_nonpii else None
    if leaf in _PII_LEAVES:
        return "sha256:" + hashlib.sha256(repr(value).encode()).hexdigest()[:16]
    # Unknown leaf: defensive hash.
    return "sha256:" + hashlib.sha256(repr(value).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class FeedbackPayload:
    """A single field-wise feedback assessment ready for ``mlflow.log_feedback``."""

    name: str
    value: bool
    source_type: str  # ASSESSMENT_HUMAN
    source_id: str  # actor
    rationale: str
    metadata: dict[str, Any]


def build_feedback_payload(
    feedback: FieldFeedback,
    *,
    redact_pii: bool = True,
    log_nonpii: bool = True,
) -> FeedbackPayload:
    """Build the log_feedback payload for one accepted/corrected field.

    The canonical ``field_path`` is carried verbatim (it is not PII); the original
    and corrected values are redacted per the tiered policy above.
    """
    disposition = "ACCEPT" if feedback.accepted else "CORRECT"
    original = redact_feedback_value(
        feedback.field_path, feedback.original_value, redact_pii=redact_pii, log_nonpii=log_nonpii
    )
    corrected = redact_feedback_value(
        feedback.field_path, feedback.corrected_value, redact_pii=redact_pii, log_nonpii=log_nonpii
    )
    return FeedbackPayload(
        name=FEEDBACK_ASSESSMENT_NAME,
        value=feedback.accepted,
        source_type=ASSESSMENT_HUMAN,
        source_id=feedback.actor,
        rationale=f"{disposition}: {feedback.field_path}",
        metadata={
            "field_path": feedback.field_path,
            "disposition": disposition,
            "accepted": feedback.accepted,
            "actor": feedback.actor,
            "original_value": original,
            "corrected_value": corrected,
            "redacted": redact_pii,
        },
    )
