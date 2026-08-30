#!/usr/bin/env python3
"""Forge-compatible entrypoint for building ``data/golden_set.jsonl``.

================================================================
PII WARNING - DO NOT COMMIT THE OUTPUT FILE
================================================================
``data/golden_set.jsonl`` contains cardholder transaction data extracted
from REAL credit-card statements (masked card numbers, merchant names,
transaction amounts, dates, balances). This is cardholder PII.

This repository is PUBLIC. The golden set is gitignored (see
``statement-agent/.gitignore``, which ignores both ``golden_set.jsonl`` and
``data/golden_set.jsonl``) and MUST NEVER be committed. Build it locally and
keep it on your machine only - it exists so the eval harness has ground truth
to score extraction rounds against.

This script embeds NO customer data. It only documents and orchestrates the
build; the data is read from local caches on the machine that runs it.

================================================================

Overview
--------
The ANVIL forge eval harness reads its ground truth from
``statement-agent/data/golden_set.jsonl``. Each line is one JSON object with
at minimum::

    {"example_id": "<sid>", "query": "<sid>", ...}

Per-field ground truth for the deterministic scorer lives under
``expected_parsed_json`` and is shaped by the per-bank schema definitions in
``statement-agent/schema/*.json``:

    schema/gt_schema.json  - the canonical ground-truth field schema every
                             row's ``expected_parsed_json`` conforms to.
    schema/icici.json      - ICICI-specific extraction schema.
    schema/hdfc.json       - HDFC-specific extraction schema.
    schema/axis.json       - Axis-specific extraction schema.
    schema/sbi.json        - SBI-specific extraction schema.

How to build
------------
The concrete builder lives beside this file as
``build_savesage_golden_set.py``. It walks a local cache of cached
ground-truth (GT) extractions plus their source PDFs and emits one JSONL row
per statement that has both. This wrapper simply delegates to it so that the
forge-standard path (``scripts/build_golden_set.py``) works out of the box::

    uv run python statement-agent/scripts/build_golden_set.py
    uv run python statement-agent/scripts/build_golden_set.py --out data/golden_set.jsonl

Sources are configured via env vars / CLI flags on the underlying builder
(``--gt-dir`` / ``--pdf-dir``); see ``build_savesage_golden_set.py`` for the
defaults and the full per-bank extension notes. The full golden set is the
union of per-bank rows built the same way (one GT cache per bank).

The default output path is ``statement-agent/data/golden_set.jsonl`` - the
path the eval harness reads from and the path gitignored in this repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

# statement-agent/scripts/build_golden_set.py -> statement-agent/
STATEMENT_AGENT_DIR = Path(__file__).resolve().parent.parent
SCHEMA_DIR = STATEMENT_AGENT_DIR / "schema"


def _describe_schema() -> None:
    """Print the schema definitions the golden set is built against."""
    if not SCHEMA_DIR.is_dir():
        print(f"(schema dir not found at {SCHEMA_DIR})")
        return
    print("Golden-set rows conform to these schema definitions:")
    for schema_file in sorted(SCHEMA_DIR.glob("*.json")):
        print(f"  - schema/{schema_file.name}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _describe_schema()
    print()
    # Delegate to the concrete per-bank builder so the forge-standard path
    # (scripts/build_golden_set.py) produces the same data/golden_set.jsonl.
    try:
        from build_savesage_golden_set import main as _build_main  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from build_savesage_golden_set import main as _build_main  # type: ignore
    return _build_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
