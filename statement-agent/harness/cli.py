"""One-off invocation shell: parse one credit-card statement PDF.

Wires the real Luna extraction adapter with default ports and runs a single PDF
through the graph. Judge/trace are optional: if the relevant ports are not
wired, the graph degrades gracefully (trace skipped, judge skipped). This CLI
is for local/ops use; ``app/main.py`` (WS6) is the production HTTP entry point.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contracts.models import Bank, ParseRequest
from graph.nodes import NodeDeps
from graph.state import GraphState


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse one credit-card statement")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--bank", choices=[b.value for b in Bank], required=True)
    parser.add_argument("--request-id", required=True)
    return parser.parse_args(argv)


def build_deps() -> NodeDeps:
    """Build the default production deps: real Luna adapter, no judge/trace.

    Keeping judge/trace None here is intentional -- the CLI is for an
    extraction-only smoke check. Full production wiring lives in ``app/main.py``
    (WS6), which injects the MLflow trace sink and Opus judge.
    """
    from harness.extraction_adapter import LunaExtractionAdapter

    return NodeDeps(extraction=LunaExtractionAdapter())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    request = ParseRequest(args.pdf, args.pdf.name, Bank(args.bank), args.request_id)
    deps = build_deps()
    try:
        from graph.graph import run_graph
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    state = GraphState(request=request)
    final = run_graph(deps, state)
    summary = final.as_summary()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if final.outcome and final.outcome.value == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
