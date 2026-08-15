# Workstream 2 — LangGraph parse agent design notes

## Graph shape

Linear pipeline: `route -> extract -> validate -> persist -> judge -> finalize`.

Every node takes and returns the same mutable `GraphState` instance. There are
no conditional edges: a node that hits a terminal failure sets
`state.outcome` and downstream nodes no-op on a terminal state. This keeps the
graph inspectable (six unconditional edges) and the in-memory test fakes
simple.

## Ports consumed (DI only — no concrete implementations imported)

`graph/nodes.py` depends ONLY on the ABCs in `contracts/ports.py`:

| Port             | Required | When absent                         |
|------------------|----------|-------------------------------------|
| `ExtractionAdapter` | yes   | graph cannot run                    |
| `ResultStore`    | no       | persistence skipped                 |
| `TraceSink`      | no       | no trace events recorded            |
| `JudgeAdapter`   | no       | judge stage skipped (not a failure) |
| `FeedbackStore`  | no       | not used by core path (WS3 wiring)  |

No `psycopg`, `mlgraph`, `langchain`, or `OpusJudgeAdapter` import appears
anywhere in `graph/`, `harness/extraction_adapter.py`, `skills/extract_statement.py`,
or `harness/cli.py`. WS3/WS4/WS5 hand in their concrete ports at integration.

## Validation short-circuit decision

**A validation failure does NOT short-circuit the judge.** Rationale:

The judge compares extraction fields against PDF ground truth *independently* of
schema/rule conformance. A partial-but-schema-invalid extraction is exactly the
output that benefits most from judging — you want to know whether the model
read the PDF correctly even when it shaped the answer wrong. Only a hard
`EXTRACTION_FAILED` outcome skips the judge (there is nothing to judge).

### Judge per-section structural gate (NB2 / NB round 3)

The judge grades sections **independently**: `cards[].cardMeta.*` (scalar per
card), `transactions[].*` (per row), and `rewards.*` (scalar). WS5's judge (PR
#13) returns `ABSENT_IN_PDF` for null truth rather than erroring, so a payload
that malforms ONE section can still usefully grade the others.

The `judge_node` gates **per-section**, not on the whole payload. A section is
judgeable when its payload value has the type the judge adapter can serialise:
`cards`/`transactions` must be lists (the judge iterates rows); `rewards` must
be a dict (scalar fields). The judge is invoked if **at least one** section is
judgeable. A payload missing or malforming one section still gets the surviving
sections graded instead of suppressing the whole verdict — this preserves
gradeable signal on exactly the partially-broken parses where the judge is most
informative. The judge is skipped only when NO section is structurally
judgeable (or on `EXTRACTION_FAILED` / no judge wired), and a clear
`judge_skipped_reason` is recorded.

The point is to distinguish "invalid but judgeable" from "structurally
unusable": a schema-invalid-but-structurally-usable payload IS still judged;
a payload with no judgeable section at all is not.

### Persistence-failure outcome (BLOCKING 4)

A run that failed to persist (e.g. `ResultStore.save_extraction` raised) must
never report SUCCESS. `finalize_node` treats any real stage error (in
`state.errors`, which excludes trace failures routed to `state.trace_errors`)
as at least PARTIAL. A user must never be told their statement was saved when
it was not.

### schema_valid propagation (BLOCKING 2)

The `validate_node` propagates the validated `schema_valid` into the frozen
`ExtractionResult` via `dataclasses.replace` *before* persistence, so the object
handed to `ResultStore.save_extraction` carries the validated value — not the
adapter's initial `False`.

Outcomes:
- `SUCCESS` — clean run, schema + rules both pass, no stage errors.
- `PARTIAL` — extraction succeeded and was persisted + judged, but validation
  flagged schema/rule violations OR a real stage (e.g. persistence) failed without
  short-circuiting (the UI shows a partial parse; never SUCCESS when unsaved).
- `EXTRACTION_FAILED` — the extract node failed terminally; persist + judge
  are skipped.
- `JUDGE_FAILED` — extraction + validation + persist succeeded, but the judge
  raised; the extraction is still available.

### Non-finite numbers (BLOCKING 1)

`json.loads` accepts `NaN`/`Infinity` literals, and `float('nan')` is a `float`.
The schema conformance checker requires `math.isfinite(value)` for the `"number"`
type, and the GT rules' `_is_number` helper requires it too. A `NaN`
`totalAmountDue` is reported `schema_valid=False`, not silently accepted.

### Structural never-raises guarantee (round 4)

`validate_payload`'s non-raising contract holds STRUCTURALLY, not by exhaustive
inspection of every coercion site. The entire body is wrapped in a broad
`try/except Exception` so that any unexpected exception (e.g. a numeric-coercion
overflow introduced by a future edit) is converted into a `ValidationReport`
with `schema_valid=False` and a DISTINCT `internal_error` message, rather than
propagating and crashing the graph on a customer's parse.

This is a SAFETY NET, not a substitute for correct checking. An internal error
is recorded separately from an ordinary validation failure (`internal_error`
field vs `schema_errors`/`rule_errors`) so genuine logic bugs are visible in
telemetry and not silently masked as "bad payload".

`BaseException` is deliberately NOT caught: `KeyboardInterrupt` and
`SystemExit` must propagate so a user can interrupt a run and the process can
be shut down cleanly.

### Closing-points arithmetic magnitude guard

The closing-points reconciliation (`closing == opening + earned + bonus -
redeemed`) uses native Python arithmetic. Mixed `int + float` arithmetic
coerces the int to float, which overflows for huge ints (e.g. `10**10000 +
1.0`). The rule is SKIPPED when any of the five values has a magnitude above
`10**18` — no real rewards points are that large. The structural safety net
above is the backstop for any case this guard does not cover.

### Truncation and refusal (BLOCKING 3)

`map_response` checks the first choice's `finish_reason` and any `message.refusal`
before parsing. For a synchronous invocation of this endpoint there is no
legitimate reason for `finish_reason` to be absent or `None`, so only an exact
`"stop"` is a clean completion. Anything else (absent, `None`, `"length"` for
truncation, `"content_filter"` for refusal) raises `ExtractionError`. A
parseable-but-clipped JSON is treated as an extraction failure, not a success
that silently drops transactions.

**Deploy-time verification needed:** the earlier live Luna call's raw response
was not preserved, so we cannot independently confirm that a real success
response carries `finish_reason: "stop"`. Our fixtures assume it does, which is
consistent with synchronous OpenAI-compatible responses — but if real responses
ever omit it, our stricter rule would reject LEGITIMATE extractions, which is
worse than the truncation bug we fixed. At first deploy on `fevm-stable`,
capture one raw success response and check the field. If real responses omit
`finish_reason`, relax to accept absent/`None` as clean.

### Single timeout source (BLOCKING 5)

The request timeout comes from `RetryPolicy.timeout_seconds` only — not from
`settings.request_timeout_seconds`. `RetryPolicy` is the single source of both
retry behaviour and the timeout.

## langgraph import discipline

`langgraph` is imported function-locally inside `graph.graph.build_graph`. The
whole `graph/` package imports cleanly with stdlib only, so the pre-existing
stdlib gate runs untouched. The graph-level e2e test is `skipUnless(langgraph)`;
the node/routing/validation/payload/result-mapping logic has real non-skipped
stdlib tests.

## Live verification

A single live Luna call was made against
`databricks-gpt-5-6-luna` on the `fevm-stable` workspace using a synthetic
empty-content PDF stub (no PII). Result: HTTP 200, `schema_valid=True`, 0 rule
errors, 0 transactions (correct for a contentless PDF). This proves the full
transport path: `transports.extraction_payload` -> `auth.acquire_token` ->
stdlib `urllib` -> Luna -> `map_response` -> `validate_payload`.
