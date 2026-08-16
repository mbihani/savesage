"""Pure span-tree assembly from TraceEvent records (stdlib-only, no mlflow).

The parse pipeline emits completed :class:`TraceEvent` records — each carries
``started_at``/``ended_at`` plus optional ``span_id``/``parent_span_id`` that WS1
added specifically so this module can express a nested span hierarchy. Events are
recorded as each phase completes, so the outer *parse* span is recorded LAST. This
builder buffers per ``request_id`` and flushes the whole tree when the root
arrives, producing a pre-order span-op list whose parent always precedes its
children — exactly what MLflow's explicit span API needs.

Root identification (review B3): the root is identified by an EXPLICIT invariant —
a declared root stage name (default ``"parse"``) — NOT merely "first event lacking
a parent". A phase event with a missing ``parent_span_id`` (a linkage bug) does NOT
trigger a flush; it is buffered as an orphan and later evicted, so the real
``parse`` root still flushes the complete tree.

Malformed graphs (review B3): duplicate span ids, self-referential ids, and
cycles are detected with a visited set; the tree build is ITERATIVE (not
recursive) so a cycle cannot cause unbounded recursion. Orphaned/disconnected
events (whose parent never arrived) are omitted from the tree and logged.

Bounded memory (review B2): ``_buffer`` (pending), ``_flushed`` (completed), are
bounded LRU structures; over-capacity pending requests are abandoned. The
trace-id map in the sink is likewise bounded. Additionally, EACH request's
event list is capped (``max_events_per_request``) so a single stuck request
whose root never arrives cannot accumulate events indefinitely — when the cap
is exceeded the request's buffer is abandoned and a warning is logged. The root
event is always allowed through even if it exceeds the cap (it triggers a flush,
so no accumulation).
"""

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any, Mapping

from contracts.models import TraceEvent

# Consume the shared PII rules (rules/pii.py is WS1-frozen; we may not edit it).
# PII_RULES are prose constraints; this module implements them mechanically. The
# WS4-specific extensions (recursive redaction, key-substring matching) live here
# and are documented as an extension of the shared rules, not a replacement.
from rules.pii import PII_RULES  # noqa: F401 - imported to centralise; re-exported for tests

from .tracing_keys import (
    SPAN_TYPE_CHAIN,
    SPAN_TYPE_EVALUATOR,
    SPAN_TYPE_GUARDRAIL,
    SPAN_TYPE_LLM,
    SPAN_TYPE_TOOL,
    SPAN_TYPE_UNKNOWN,
)

_LOGGER = logging.getLogger("statement-agent.tracing")

# Default root stage name. The parse pipeline's outer span is named "parse".
ROOT_STAGE_DEFAULT = "parse"

# Defensive PII scrubbing for span attributes. Keys containing any of these
# substrings are replaced with "[REDACTED]" before reaching MLflow. This is a
# mechanical implementation of rules/pii.py: card numbers, cardholder names, full
# transaction descriptions, raw PDF/payload bytes, and statement identifiers must
# never be traced. WS4 extension: this list is a superset of the prose rules.
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
    "statementid",  # camelCase: statementId / rawStatementId → no underscore
    "rawstatementid",  # explicit (also caught by "statementid")
    "account_number",
    "accountnumber",  # camelCase: accountNumber → no underscore
    "name",  # cardholder name / account holder name
)
# A loose card-number-shaped sequence (13-19 digits, optional spaces/dashes).
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Default cap for arbitrary string values reaching MLflow. 200 keeps traces
# small and limits how much of any one field is exposed.
_MAX_STR = 200
# Larger cap for known template-text values that are intentionally traced in
# full so they are VISIBLE in the trace view. The bank prompts are 8-27 KB of
# instruction text (no customer PII); the default 200-char cap would show only
# the title line and hide the actual instructions sent to Luna. 4000 captures
# the substantive content while staying bounded per span.
_MAX_STR_PROMPT = 4000
# Keys whose string values get the larger cap (template text, not payloads).
_LONG_VALUE_KEYS = frozenset({"prompt"})
_REDACTED = "[REDACTED]"
_REDACTED_CARD = "[REDACTED_CARD]"


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
    """RECURSIVE PII scrubber for span attributes (review B4).

    Logs counts, hashes, field paths and booleans in preference to raw values
    (per rules/pii.py). Redaction is RECURSIVE: a benign top-level key whose value
    is a nested dict/list can carry cardholder names, transaction descriptions,
    account numbers, or raw PDF text, so we recurse into every dict/list and scrub
    card-number-shaped sequences in every string at every depth. Keys naming PII
    are replaced with ``[REDACTED]`` at every level; long strings are truncated.

    Keys in :data:`_LONG_VALUE_KEYS` (e.g. ``"prompt"``) use a larger truncation
    cap so intentionally-traced template text stays VISIBLE -- the default cap
    would show only the prompt's title line and hide the instructions sent to Luna.
    """
    return _redact_value(dict(attrs), max_str=_MAX_STR)


def _redact_value(value: Any, *, max_str: int = _MAX_STR) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, val in value.items():
            lk = str(key).lower()
            if any(sub in lk for sub in _PII_KEY_SUBSTRINGS):
                out[key] = _REDACTED
            else:
                # A long-value key raises the cap for its subtree so template
                # text (the prompt) is traced substantially, not clipped to 200.
                child_max = _MAX_STR_PROMPT if lk in _LONG_VALUE_KEYS else max_str
                out[key] = _redact_value(val, max_str=child_max)
        return out
    if isinstance(value, list):
        return [_redact_value(v, max_str=max_str) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v, max_str=max_str) for v in value)
    if isinstance(value, str):
        value = _CARD_RE.sub(_REDACTED_CARD, value)
        if len(value) > max_str:
            value = value[:max_str] + "...[truncated]"
        return value
    return value


@dataclass
class SpanOp:
    """One node in the assembled span tree."""

    event: TraceEvent
    is_root: bool
    children: list["SpanOp"] = field(default_factory=list)


class SpanTreeBuilder:
    """Buffer TraceEvents per request_id and flush an ordered span tree on root.

    All collections are BOUNDED (review B2): pending requests, completed request
    ids, are capped; over-capacity pending requests are abandoned (logged).
    """

    def __init__(
        self,
        *,
        root_stage: str = ROOT_STAGE_DEFAULT,
        max_pending: int = 1024,
        max_flushed: int = 2048,
        max_events_per_request: int = 100,
    ) -> None:
        self._root_stage = root_stage
        self._max_pending = max_pending
        self._max_flushed = max_flushed
        self._max_events_per_request = max_events_per_request
        self._buffer: "OrderedDict[str, list[TraceEvent]]" = OrderedDict()
        self._flushed: "OrderedDict[str, None]" = OrderedDict()

    def feed(self, event: TraceEvent) -> list[SpanOp] | None:
        """Buffer an event.

        When the declared root (``parent_span_id`` is None AND ``name`` is the
        root stage) arrives, build and return the pre-order span-op list for the
        whole tree (parent before children) and mark the request flushed. A phase
        event with ``parent_span_id=None`` but a non-root name is an orphan: it is
        buffered but does NOT trigger a flush (review B3). Late arrivals after a
        flush return None. Non-root events return None while buffered.

        Per-request event cap (review B2): a non-root event that pushes the
        request's buffer beyond ``max_events_per_request`` causes the request to
        be abandoned (buffer dropped + warning logged). The root is always allowed
        through even if it exceeds the cap — it triggers a flush immediately, so
        no accumulation.
        """
        rid = event.request_id
        if rid in self._flushed:
            return None  # late arrival after flush — drop (trace already ended)
        buf = self._buffer.setdefault(rid, [])
        buf.append(event)
        # Flush only on the declared root (explicit invariant, review B3).
        is_root = event.parent_span_id is None and event.name == self._root_stage
        # Per-request event cap (review B2): a stuck request with no root must
        # not accumulate events indefinitely. Non-root events beyond the cap
        # cause the request to be abandoned. The root always flushes.
        if not is_root and len(buf) > self._max_events_per_request:
            self._buffer.pop(rid, None)
            self._mark_flushed(rid)  # prevent re-buffering of subsequent events
            _LOGGER.warning(
                "request %s exceeded max_events_per_request (%d > %d); abandoning buffer",
                rid, len(buf), self._max_events_per_request,
            )
            return None
        # Evict oldest pending if over capacity (abandon — root will never come).
        while len(self._buffer) > self._max_pending:
            evicted_rid, _ = self._buffer.popitem(last=False)
            _LOGGER.warning("tracing buffer full; abandoning pending request %s", evicted_rid)
        if is_root:
            tree = self._build(rid)
            self._buffer.pop(rid, None)
            self._mark_flushed(rid)
            return tree
        return None

    def _mark_flushed(self, rid: str) -> None:
        self._flushed[rid] = None
        while len(self._flushed) > self._max_flushed:
            self._flushed.popitem(last=False)

    def abandon(self, request_id: str) -> None:
        """Explicitly drop a request whose root will never arrive (e.g. crashed parse)."""
        self._buffer.pop(request_id, None)
        self._flushed.pop(request_id, None)

    def _build(self, rid: str) -> list[SpanOp]:
        events = self._buffer.get(rid, [])
        if not events:
            return []
        # Index events by span_id; detect duplicates.
        by_span_id: dict[str, TraceEvent] = {}
        duplicate_ids: set[str] = set()
        for e in events:
            if e.span_id is not None:
                if e.span_id in by_span_id:
                    duplicate_ids.add(e.span_id)
                else:
                    by_span_id[e.span_id] = e
        # Find the root: the declared-root event.
        roots = [e for e in events if e.parent_span_id is None and e.name == self._root_stage]
        if not roots:
            _LOGGER.warning("no root event (stage=%s) for request %s; %d events orphaned",
                            self._root_stage, rid, len(events))
            return []
        if len(roots) > 1:
            _LOGGER.warning("multiple root events for request %s; using earliest", rid)
        root_event = min(roots, key=lambda x: x.started_at)

        # Reject self-referential root.
        if root_event.span_id is not None and root_event.span_id == root_event.parent_span_id:
            _LOGGER.warning("self-referential root span_id for request %s", rid)
            return []

        # Group children by parent_span_id. Only events with a NON-None
        # parent_span_id are children; parent_span_id=None means "root/orphan",
        # NOT "my parent's span_id is None" (which would be ambiguous and can
        # cause infinite loops when span_id is also None).
        children_of: dict[str, list[TraceEvent]] = {}
        for e in events:
            if e is root_event:
                continue
            if e.parent_span_id is None:
                continue  # orphan (linkage bug) — not a child of anything
            # Duplicate span_id: keep the FIRST occurrence, drop the rest.
            if e.span_id is not None and e.span_id in duplicate_ids and by_span_id.get(e.span_id) is not e:
                _LOGGER.warning("duplicate span_id %s in request %s; dropping", e.span_id, rid)
                continue
            if e.span_id is not None and e.span_id == e.parent_span_id:
                _LOGGER.warning("self-referential span_id %s in request %s; dropping", e.span_id, rid)
                continue
            children_of.setdefault(e.parent_span_id, []).append(e)

        root_op = SpanOp(event=root_event, is_root=True)

        # ITERATIVE child attachment with a visited set (cycle detection, review B3).
        # Stack of (parent_op, child_event). We track visited span_ids so a cycle
        # cannot loop forever.
        visited: set[str] = set()
        if root_event.span_id is not None:
            visited.add(root_event.span_id)
        stack: list[tuple[SpanOp, TraceEvent]] = [
            (root_op, child)
            for child in sorted(children_of.get(root_event.span_id, []), key=lambda x: x.started_at)
        ] if root_event.span_id is not None else []
        attached = 1
        while stack:
            parent_op, child_event = stack.pop()
            cid = child_event.span_id
            if cid is not None and cid in visited:
                _LOGGER.warning("cycle detected at span_id %s in request %s; breaking", cid, rid)
                continue
            if cid is not None:
                visited.add(cid)
            cop = SpanOp(event=child_event, is_root=False)
            parent_op.children.append(cop)
            attached += 1
            kids = sorted(children_of.get(cid, []), key=lambda x: x.started_at)
            for k in kids:
                stack.append((cop, k))

        if attached < len(events):
            _LOGGER.warning("request %s: %d/%d events attached; %d orphaned (missing parent)",
                            rid, attached, len(events), len(events) - attached)

        # Pre-order walk (parent before children).
        flat: list[SpanOp] = []

        def walk(op: SpanOp) -> None:
            flat.append(op)
            for child in op.children:
                walk(child)

        walk(root_op)
        return flat

    # Test/debug helper: buffered-but-unflushed request ids (for assertions).
    def pending(self) -> set[str]:
        return set(self._buffer)

    def flushed_count(self) -> int:
        return len(self._flushed)
