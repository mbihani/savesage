# SaveSage statement agent

Contract-first scaffold for four-bank credit-card statement extraction.

```text
PDF + Bank
    |
    v
LangGraph (WS2) -> Luna extraction -> GT_SCHEMA validation
    |                    |
    |                    +-> Lakebase + CDF (WS3)
    |                    +-> MLflow traces (WS4)
    v
Opus-5 judge (WS5) -> seven-field verdict -> FastAPI/UI feedback (WS6)
```

## Six workstreams

1. This scaffold: contracts, schema, prompts, rules, memory, and harness.
2. LangGraph orchestration and `ExtractionAdapter`.
3. Lakebase/CDF, `ResultStore`, and `FeedbackStore`.
4. MLflow instrumentation and `TraceSink`.
5. Opus-5 adjudication and `JudgeAdapter`.
6. FastAPI API and no-build static frontend.

## YOU CANNOT RUN THIS LOCALLY

This machine blackholes pypi.org and registry.npmjs.org, and local Python 3.14 is
newer than the Databricks Apps runtime. Do not run pip, uv, or npm install. Only
the stdlib contract tests run locally. Databricks Apps installs pinned
`requirements.txt` dependencies and end-to-end verification happens on
`fevm-stable-classic-7ppxjq`.

## Deploy to fevm-stable

From an authenticated Databricks CLI, create or update the app from this source
directory, bind the workstream-3/4 resources in `app.yaml`, grant the app service
principal endpoint/resource access, and deploy. Runtime values are environment
variables described in `MANIFESTO.md`; secrets are never committed.
