#!/usr/bin/env python3
"""Build the Savesage statement-agent golden set from the local PDF corpus.

================================================================
⚠️  PII WARNING — DO NOT COMMIT THE OUTPUT FILE
================================================================
``data/golden_set.jsonl`` contains cardholder transaction data extracted
from REAL credit-card statements: masked card numbers, merchant names,
transaction amounts, dates, and balances. This is cardholder PII.

This repository is PUBLIC. The golden set is gitignored (see
``statement-agent/.gitignore``) and MUST NEVER be committed. Build it
locally and keep it on your machine only — it exists so the eval harness
has ground truth to score extraction rounds against.

If you have ever committed this file, force-push the branch to drop the
commit AND contact GitHub support to purge the commit from their object
store (a force-push removes it from the branch tip but NOT from GitHub's
cache or any closed PR that referenced it).

================================================================

What this script does
---------------------
For each statement that has BOTH a cached ground-truth (GT) extraction
and its source PDF on disk, emit one JSONL row::

    {"example_id": "<sid>", "query": "<sid>", "category": "<card-type>",
     "pdf_path": "<abs path to the statement PDF>",
     "expected_parsed_json": { ...cached GT extraction... }}

``expected_parsed_json`` is the ground truth the deterministic scorer
diffs each eval round against. ``category`` is the co-brand / card type
parsed from the PDF filename, used for per-bucket reporting.

This is a simplified, single-bank (ICICI) builder that documents the
process. The full production golden set is the union of per-bank rows
built the same way (one builder per bank GT cache); extend by pointing
``--gt-dir`` / ``--pdf-dir`` at each bank's caches in turn.

Sources (override via env)
-------------------------
  SAVESAGE_ICICI_GT_DIR   default ~/Savesage/bank_eval/icici/opus_gt/json
  SAVESAGE_ICICI_PDF_DIR  default "~/Downloads/remaining pdfs ground truth/icici-pdfs"

Usage
-----
    uv run python statement-agent/scripts/build_savesage_golden_set.py
    uv run python statement-agent/scripts/build_savesage_golden_set.py --out data/golden_set.jsonl

The default output is ``statement-agent/data/golden_set.jsonl`` — the path
the eval harness reads from and the path gitignored in this repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

# statement-agent/scripts/build_savesage_golden_set.py  ->  statement-agent/
STATEMENT_AGENT_DIR = Path(__file__).resolve().parent.parent

_DEFAULT_GT_DIR = Path.home() / "Savesage" / "bank_eval" / "icici" / "opus_gt" / "json"
_DEFAULT_PDF_DIR = Path.home() / "Downloads" / "remaining pdfs ground truth" / "icici-pdfs"

# "..._Retail_<TYPE>_NORM.pdf" / "..._Retail_<TYPE>_NEW_NORM.pdf" -> <TYPE>
_CATEGORY_RE = re.compile(r"_Retail_(.+?)(?:_NEW)?_NORM\.pdf$", re.IGNORECASE)


def _category(pdf_name: str) -> str:
    m = _CATEGORY_RE.search(pdf_name)
    return m.group(1).lower() if m else "other"


def build_rows(gt_dir: Path, pdf_dir: Path) -> tuple[list[dict], list[str]]:
    """One row per statement that has both a cached GT extraction and its PDF."""
    rows: list[dict] = []
    skipped: list[str] = []
    for gt_file in sorted(gt_dir.glob("*.json")):
        sid = gt_file.stem
        try:
            record = json.loads(gt_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            skipped.append(f"{sid}: unreadable GT ({exc})")
            continue
        parsed = record.get("parsed_json")
        pdf_name = record.get("pdf")
        if not isinstance(parsed, dict) or not pdf_name:
            skipped.append(f"{sid}: no parsed_json/pdf field (outcome={record.get('outcome')})")
            continue
        pdf_path = pdf_dir / pdf_name
        if not pdf_path.is_file():
            skipped.append(f"{sid}: PDF missing at {pdf_path}")
            continue
        rows.append(
            {
                "example_id": sid,
                "query": sid,
                "category": _category(pdf_name),
                "pdf_path": str(pdf_path),
                "expected_parsed_json": parsed,
            }
        )
    return rows, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--gt-dir", default=os.environ.get("SAVESAGE_ICICI_GT_DIR", str(_DEFAULT_GT_DIR))
    )
    ap.add_argument(
        "--pdf-dir", default=os.environ.get("SAVESAGE_ICICI_PDF_DIR", str(_DEFAULT_PDF_DIR))
    )
    ap.add_argument("--out", default=str(STATEMENT_AGENT_DIR / "data" / "golden_set.jsonl"))
    args = ap.parse_args(argv)

    gt_dir, pdf_dir = Path(args.gt_dir), Path(args.pdf_dir)
    if not gt_dir.is_dir():
        raise SystemExit(f"GT dir not found: {gt_dir}")
    if not pdf_dir.is_dir():
        raise SystemExit(f"PDF dir not found: {pdf_dir}")

    rows, skipped = build_rows(gt_dir, pdf_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    cats: dict[str, int] = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(f"wrote {len(rows)} rows to {out_path} ({len(skipped)} skipped)")
    print("categories:", json.dumps(dict(sorted(cats.items())), indent=0))
    for s in skipped[:10]:
        print("  skip:", s)
    print("\nRemember: this file contains cardholder PII — do NOT commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
