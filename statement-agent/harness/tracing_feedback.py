"""Pure field-wise client feedback payload construction (stdlib-only, no mlflow).

The client accepts or corrects INDIVIDUAL fields. Each corrected/accepted field
becomes one ``mlflow.log_feedback`` assessment carrying: the canonical field path
(from contracts.paths), the original value, the corrected value, the accepted
bool, and the actor. Field paths use contracts.paths.canonical_feedback_path /
is_valid_feedback_path — never a bespoke notation (WS3 persists and WS6 sends the
same format; three notations would silently break the loop).

PII decision (review B4 — corrected, visible, not buried):

By default NO raw PII reaches MLflow. The redaction policy centralises on
``rules/pii.py`` (WS1-frozen; imported, not edited) and adds WS4-specific
mechanical extensions here:

- ``cardDisplayName`` and transaction ``description`` -> KEYED HMAC (SHA-256)
  using ``TracingConfig.feedback_hmac_key``. These are cardholder names / free-
  text descriptions = client PII. An UNSALTED truncated SHA-256 of a low-entropy
  value (e.g. "UPI-Amazon") is dictionary-reversible, so we use a keyed HMAC. When
  no HMAC key is configured, these values are OMITTED entirely (sent as None)
  rather than risk a reversible digest.
- ``lastFourDigit`` -> kept as-is. It is already the PII-safe redacted form.
- ``amount``, ``date``, ``rewards.pointsEarnedThisCycle``,
  ``rewards.closingPoints`` -> kept raw. These are NOT individually cardholder-
  identifying. CORRECTION to a prior inaccurate claim: they DO share the same
  trace as ``lastFourDigit`` and CAN be correlated within a single trace. This
  is a deliberate, documented trade-off — hashing amounts/dates would destroy the
  feedback's meaning for correction analytics. The residual re-identification
  risk (amount+date+last4 within one trace) is accepted because (a) it requires
  the attacker to already have the trace, and (b) the alternative makes feedback
  analytics useless. ``lastFourDigit`` can be suppressed via ``log_nonpii=False``.
- ``actor`` -> PSEUDONYMISED with the same keyed HMAC. Actors can be personal
  names or email addresses. When no HMAC key is configured, the actor is sent as
  ``"redacted"`` (no pseudonym). No raw actor form is ever retained in telemetry.
- any other leaf -> omitted (None) when no HMAC key; keyed HMAC when configured.

What we would want added to shared ``rules/pii.py`` (noted for WS1, not edited
here): a machine-readable PII key registry (the ``_PII_KEY_SUBSTRINGS`` list in
tracing_spans.py) and a ``redact(value, *, hmac_key)`` helper, so WS4 does not
maintain a parallel implementation that can drift from the prose rules.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from contracts.models import FieldFeedback

from .tracing_keys import ASSESSMENT_HUMAN, FEEDBACK_ASSESSMENT_NAME

# Field-path leaves that ARE client PII -> HMAC or omit.
_PII_LEAVES = frozenset({"cardDisplayName", "description"})
# Field-path leaves that are NOT individually PII and essential to analytics.
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


def _hmac(key: bytes, value: Any) -> str:
    """Keyed HMAC-SHA256 (16 hex chars). Not reversible without the key."""
    return "hmac:" + hmac.new(key, repr(value).encode(), hashlib.sha256).hexdigest()[:16]


def pseudonymise_actor(actor: str, hmac_key: bytes) -> str:
    """Pseudonymise a feedback actor (name/email) via keyed HMAC.

    Returns ``"redacted"`` when no key is configured (no raw actor is ever sent).
    """
    if not hmac_key:
        return "redacted"
    return _hmac(hmac_key, actor)


def redact_feedback_value(
    field_path: str,
    value: Any,
    *,
    redact_pii: bool = True,
    log_nonpii: bool = True,
    hmac_key: bytes = b"",
) -> Any:
    """Tiered PII redaction of one feedback value -> a JSON primitive or None.

    See module docstring for the policy. Returns a value suitable for
    ``mlflow.log_feedback`` metadata (str/int/float/bool/None).
    """
    leaf = _leaf(field_path)
    raw = _to_primitive(value)
    if not redact_pii:
        # Operator disabled redaction -> raw values (documented exception).
        return raw
    if leaf in _KEEP_LEAVES:
        return raw if log_nonpii else None
    if leaf in _PII_LEAVES:
        if not hmac_key:
            return None  # omit rather than send a reversible unsalted digest
        return _hmac(hmac_key, value)
    # Unknown leaf: defensive omit (or HMAC if key present).
    if not hmac_key:
        return None
    return _hmac(hmac_key, value)


@dataclass(frozen=True)
class FeedbackPayload:
    """A single field-wise feedback assessment ready for ``mlflow.log_feedback``."""

    name: str
    value: bool
    source_type: str  # ASSESSMENT_HUMAN
    source_id: str  # pseudonymised actor (never raw)
    rationale: str
    metadata: dict[str, Any]


def build_feedback_payload(
    feedback: FieldFeedback,
    *,
    redact_pii: bool = True,
    log_nonpii: bool = True,
    hmac_key: bytes = b"",
) -> FeedbackPayload:
    """Build the log_feedback payload for one accepted/corrected field.

    The canonical ``field_path`` is carried verbatim (it is not PII); the original
    and corrected values are redacted per the tiered policy; the actor is
    pseudonymised (never sent raw).
    """
    disposition = "ACCEPT" if feedback.accepted else "CORRECT"
    original = redact_feedback_value(
        feedback.field_path, feedback.original_value,
        redact_pii=redact_pii, log_nonpii=log_nonpii, hmac_key=hmac_key,
    )
    corrected = redact_feedback_value(
        feedback.field_path, feedback.corrected_value,
        redact_pii=redact_pii, log_nonpii=log_nonpii, hmac_key=hmac_key,
    )
    actor_pseudo = pseudonymise_actor(feedback.actor, hmac_key) if redact_pii else feedback.actor
    return FeedbackPayload(
        name=FEEDBACK_ASSESSMENT_NAME,
        value=feedback.accepted,
        source_type=ASSESSMENT_HUMAN,
        source_id=actor_pseudo,
        rationale=f"{disposition}: {feedback.field_path}",
        metadata={
            "field_path": feedback.field_path,
            "disposition": disposition,
            "accepted": feedback.accepted,
            "actor": actor_pseudo,  # pseudonymised; raw actor never sent
            "original_value": original,
            "corrected_value": corrected,
            "redacted": redact_pii,
        },
    )
