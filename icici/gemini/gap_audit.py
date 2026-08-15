"""STEP 2 gap audit: which schema fields get guidance in our refined ICICI prompt
vs the client's generic prompt?

MANDATORY TRAP, handled explicitly: the client's file carries the whole SCHEMA
type-map on ONE line (line 64). Every field name appears there. A field whose only
"hit" is that line has NO prompt guidance. On both HDFC and SBI four fields looked
like gaps until it turned out every hit was on the schema line and the true PORT_IN
list was EMPTY. So this script reports, per field, the line numbers of every hit and
separates SCHEMA-LINE hits from real PROSE hits.

Also checks the reverse case found on SBI: a positive instruction present in the
client's prompt that our refined prompt paraphrased away, keeping only a prohibition.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICICI_PROMPT = os.path.join(HERE, "..", "ICICI_PROMPT.txt")
CLIENT_FILE = os.path.expanduser("~/Downloads/gemini-3-flash--prompt-shcema.txt")
PROV = os.path.join(HERE, "GEMINI_SCHEMA_PROVENANCE.json")

# leaf name -> the last path segment, which is what prose actually says
prov = json.load(open(PROV))
FIELDS = sorted({p.split(".")[-1] for p in prov["leaves"]})


def hits(path, field):
    out = []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        for m in re.finditer(re.escape(field), line, re.I):
            out.append(n)
            break
    return out


def schema_line_of(path):
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if re.match(r"\s*SCHEMA\s*=", line):
            return n
    return None


def main():
    sl = schema_line_of(CLIENT_FILE)
    print(f"client file        : {CLIENT_FILE}")
    print(f"SCHEMA type-map on : line {sl}   <-- hits on this line are NOT guidance\n")

    rows = []
    for f in FIELDS:
        ch = hits(CLIENT_FILE, f)
        ch_schema = [n for n in ch if n == sl]
        ch_prose = [n for n in ch if n != sl]
        oh = hits(ICICI_PROMPT, f)
        rows.append((f, ch_prose, ch_schema, oh))

    w = max(len(f) for f in FIELDS) + 1
    print(f"{'field':<{w}} {'client PROSE lines':<26} {'client schema-line':<19} ours (ICICI_PROMPT lines)")
    print("-" * 118)
    for f, cp, cs, oh in rows:
        print(f"{f:<{w}} {str(cp)[:25]:<26} {str(cs):<19} {str(oh)[:52]}")

    print("\n" + "=" * 96)
    print("PORT_IN candidates: guidance in the client's PROSE that our prompt lacks")
    print("=" * 96)
    port_in = [(f, cp) for f, cp, cs, oh in rows if cp and not oh]
    if not port_in:
        print("  PORT_IN LIST IS EMPTY.")
        print("  Every field the client's file mentions is either already covered by")
        print("  ICICI_PROMPT.txt, or its only hit was the one-line SCHEMA type-map.")
    for f, cp in port_in:
        print(f"  {f}: client prose lines {cp}")

    print("\n" + "=" * 96)
    print("SCHEMA-LINE-ONLY fields (would look like gaps if the trap were not excluded)")
    print("=" * 96)
    only = [(f, cs) for f, cp, cs, oh in rows if cs and not cp]
    for f, cs in only:
        print(f"  {f:<34} client hit ONLY on line {cs[0]}  -> not guidance")
    print(f"\n  count: {len(only)} of {len(FIELDS)} fields")

    print("\n" + "=" * 96)
    print("NOT_PORTED: fields with no prose guidance anywhere (schema description territory)")
    print("=" * 96)
    for f, cp, cs, oh in rows:
        if not cp and not oh:
            print(f"  {f}")

    # ---- reverse case: positive instruction in client prose, absent from ours ----
    print("\n" + "=" * 96)
    print("REVERSE CHECK: positive client instructions our prompt may have paraphrased away")
    print("=" * 96)
    ours = open(ICICI_PROMPT, encoding="utf-8").read()
    ours_ns = re.sub(r"\s+", " ", ours).lower()
    probes = [
        ("CR/C/+ as CREDIT, DR/D/- as DEBIT",
         ["cr, c, +", "cr, c,", "c, +"], "direction marker allowlist"),
        ("negate non-transaction credit amounts",
         ["add - before that amount"], "sign convention for non-txn amounts"),
        ("amount always positive",
         ["always a positive number"], "txn amount sign"),
        ("all fields always present / null where missing",
         ["use null where data is missing", "always output all fields"], "output completeness"),
        ("single-line JSON only",
         ["single-line valid json"], "output format"),
    ]
    for label, needles, why in probes:
        found = any(n in ours_ns for n in needles)
        print(f"  [{'PRESENT' if found else 'MISSING '}] {label:<46} ({why})")


if __name__ == "__main__":
    main()
