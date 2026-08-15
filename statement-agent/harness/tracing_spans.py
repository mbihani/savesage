"""Pure span-tree assembly from TraceEvent records (stdlib-only, no mlflow).

The parse pipeline emits completed :class:`TraceEvent` records — each carries
``started_at``/``ended_at`` plus optional ``span_id``/``parent_span_id`` that WS1
added specifically so this module can express a nested span hierarchy. Events are
recorded as each phase completes, so the outer *parse* span (``parent_span_id`` is
None) is recorded LAST. This builder buffers per ``request_id`` and flushes the
whole tree when its root arrives, producing a pre-order span-op list whose parent
always precedes its children — exactly what MLflow's explicit span API needs.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any, Mapping

from contracts.models import TraceEvent

from .tracing_keys import (
    SPAN_TYPE_CHAIN,
    SPAN_TYPE_EVALUATOR,
    SPAN_TYPE_GUARDRAIL,
    SPAN_TYPE_LLM,
    SPAN_TYPE_TOOL,
    SPAN_TYPE_UNKNOWN,
)

# Defensive PII scrubbing for span attributes. Keys containing any of these
# substrings are replaced with "[REDACTED]" before reaching MLflow. Guided by
# rules/pii.py: card numbers, cardholder names, full transaction descriptions,
# raw PDF/payload bytes, and statement identifiers must never be traced.
_PII_KEY_SUBSTRINGS = (
    "card_number",
    "cardnumber",
    "cardholder",
    "carddisplayname",
    "card_display_name",
    "description",
    "filename",
    "pdf",
    "payload",
    "raw_response",
    "raw_text",
    "statement_id",
    "account_number",
)
# A loose card-number-shaped sequence (13-19 digits, optional spaces/dashes).
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_MAX_STR = 200


def to_ns(dt: datetime) -> int:
    """Convert a datetime to epoch nanoseconds (assume UTC when tz-naive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def span_type_for(name: str) -> str:
    """Map a span name to an MLflow SpanType string by phase convention."""
    n = name.lower()
    if "extract" in n:
        return SPAN_TYPE_LLM
    if "judg" in n:  # covers "judge" and "judging"
        return SPAN_TYPE_EVALUATOR
    if "valid" in n:
        return SPAN_TYPE_GUARDRAIL
    if "persist" in n:
        return SPAN_TYPE_TOOL
    if "parse" in n or n == "parse":
        return SPAN_TYPE_CHAIN
    return SPAN_TYPE_UNKNOWN


def redact_telemetry_attributes(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """Defensive PII scrubber for span attributes.

    Logs counts, hashes, field paths and booleans in preference to raw values
    (per the workstream 4 PII brief). Drops/scrubs anything card-number-shaped and
    any attribute whose key names PII; truncates long strings.
    """
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        lk = str(key).lower()
        if any(sub in lk for sub in _PII_KEY_SUBSTRINGS):
            out[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            value = _CARD_RE.sub("[REDACTED_CARD]", value)
            if len(value) > _MAX_STR:
                value = value[:_MAX_STR] + "...[truncated]"
        out[key] = value
    return out


@dataclass
class SpanOp:
    """One node in the assembled span tree."""

    event: TraceEvent
    is_root: bool
    children: list["SpanOp"] = field(default_factory=list)


class SpanTreeBuilder:
    """Buffer TraceEvents per request_id and flush an ordered span tree on root."""

    def __init__(self) -> None:
        self._buffer: dict[str, list[TraceEvent]] = defaultdict(list)
        self._flushed: set[str] = set()

    def feed(self, event: TraceEvent) -> list[SpanOp] | None:
        """Buffer an event.

        When the trace root (``parent_span_id`` is None) arrives, build and return
        the pre-order span-op list for the whole tree (parent before children) and
        mark the request flushed. Late arrivals after a flush return None; the
        adapter logs them and drops them (MLflow cannot attach spans to an ended
        trace). Non-root events return None while buffered.
        """
        rid = event.request_id
        if rid in self._flushed:
            return None
        self._buffer[rid].append(event)
        if event.parent_span_id is None:
            tree = self._build(rid)
            self._flushed.add(rid)
            self._buffer.pop(rid, None)
            return tree
        return None

    def _build(self, rid: str) -> list[SpanOp]:
        events = self._buffer.get(rid, [])
        children_of: dict[str | None, list[TraceEvent]] = defaultdict(list)
        for e in events:
            children_of[e.parent_span_id].append(e)
        roots = children_of.get(None, [])
        if not roots:
            return []  # malformed: no root recorded
        root_event = min(roots, key=lambda x: x.started_at)
        root_op = SpanOp(event=root_event, is_root=True)

        def add_children(op: SpanOp) -> None:
            # A span with no span_id cannot be referenced by children.
            if op.event.span_id is None:
                return
            kids = sorted(children_of.get(op.event.span_id, []), key=lambda x: x.started_at)
            for child in kids:
                cop = SpanOp(event=child, is_root=False)
                op.children.append(cop)
                add_children(cop)

        add_children(root_op)

        flat: list[SpanOp] = []

        def walk(op: SpanOp) -> None:
            flat.append(op)
            for child in op.children:
                walk(child)

        walk(root_op)
        return flat

    # Test/debug helper: buffered-but-unflushed request ids (for assertions).
    def pending(self) -> set[str]:
        return {rid for rid, evs in self._buffer.items() if evs and rid not in self._flushed}
