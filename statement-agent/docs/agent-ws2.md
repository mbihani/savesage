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

Outcomes:
- `SUCCESS` — clean run, schema + rules both pass.
- `PARTIAL` — extraction succeeded and was persisted + judged, but validation
  flagged schema/rule violations (the UI shows a partial parse).
- `EXTRACTION_FAILED` — the extract node failed terminally; persist + judge
  are skipped.
- `JUDGE_FAILED` — extraction + validation + persist succeeded, but the judge
  raised; the extraction is still available.

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
