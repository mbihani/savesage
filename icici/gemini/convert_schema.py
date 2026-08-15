"""Convert the client's SCHEMA type-map string into a strict JSON Schema.

PROVENANCE, not a build step. This regenerates the BARE schema from the client
source; it deliberately knows nothing about enums or descriptions and would
discard them. Run it once, then never again -- assert_schema.py is the guard that
replaces it (same discipline as hdfc/ and sbi/).

Source: ~/Downloads/gemini-3-flash--prompt-shcema.txt
        line 1  : SYSTEM_PROMPT = \"\"\"...\"\"\"   (the client's generic prompt)
        line 64 : SCHEMA = '{...}'               (the type-map, ONE line)

The type-map is the authority on WHICH fields exist. It has exactly 26 leaves.
Notably ABSENT, and left absent on purpose:
    statementLevelSummary.utilisationPercent
    rewards.bonusPointsThisCycle
    statementMeta.rawStatementId
    statementMeta.statementPeriodStart / statementPeriodEnd
    cards[].bigPicture.*
Measured across four banks, essentially no model ever emits utilisationPercent
(~1 emission in 2,636 calls) and no PDF prints a utilisation figure -- it belongs
in code, not in an extraction contract.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_FILE = os.path.expanduser("~/Downloads/gemini-3-flash--prompt-shcema.txt")
SCHEMA_OUT = os.path.join(HERE, "GEMINI_SCHEMA.json")
PROV_OUT = os.path.join(HERE, "GEMINI_SCHEMA_PROVENANCE.json")
GENERIC_OUT = os.path.join(HERE, "GEMINI_GENERIC_PROMPT.txt")

TYPE_MAP = {"string": "string", "number": "number", "boolean": "boolean", "null": "null"}


def parse_client(path):
    raw = open(path, encoding="utf-8").read()
    m = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', raw, re.S)
    if not m:
        sys.exit("could not find SYSTEM_PROMPT triple-quoted block")
    prompt = m.group(1)
    m2 = re.search(r"SCHEMA\s*=\s*'(\{.*\})'\s*$", raw, re.S | re.M)
    if not m2:
        sys.exit("could not find SCHEMA = '{...}' type-map line")
    tmap = json.loads(m2.group(1))
    schema_line = raw[: m2.start()].count("\n") + 1
    return prompt, tmap, schema_line


def build(node):
    """Recursively turn a client type-map node into strict JSON Schema."""
    if isinstance(node, dict):
        props = {k: build(v) for k, v in node.items()}
        return {"type": "object", "additionalProperties": False,
                "required": list(node.keys()), "properties": props}
    if isinstance(node, list):
        return {"type": "array", "items": build(node[0])}
    # leaf: a 'string|null' style spec
    toks = [t.strip() for t in str(node).split("|")]
    types = [TYPE_MAP[t] for t in toks]
    return {"type": types}


def leaves(node, path=()):
    out = {}
    if node.get("type") == "object":
        for k, v in node["properties"].items():
            out.update(leaves(v, path + (k,)))
    elif node.get("type") == "array":
        out.update(leaves(node["items"], path))
    else:
        out[".".join(path)] = node
    return out


def src_leaves(node, path=()):
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(src_leaves(v, path + (k,)))
    elif isinstance(node, list):
        out.update(src_leaves(node[0], path))
    else:
        out[".".join(path)] = node
    return out


def main():
    prompt, tmap, schema_line = parse_client(CLIENT_FILE)
    schema = build(tmap)
    got = leaves(schema)
    src = src_leaves(tmap)
    assert set(got) == set(src), (set(got) ^ set(src))
    if len(got) != 26:
        sys.exit(f"expected 26 leaves, got {len(got)}")

    if os.path.exists(SCHEMA_OUT) and "--force" not in sys.argv:
        sys.exit(f"{SCHEMA_OUT} exists; refusing to clobber enums/descriptions. "
                 f"Pass --force only if you really mean to regenerate the BARE schema.")

    json.dump(schema, open(SCHEMA_OUT, "w"), indent=2)
    json.dump({
        "source_file": CLIENT_FILE,
        "source_schema_line": schema_line,
        "note": ("Leaf types are the client's own 'string|null' spellings, verbatim. "
                 "Nullability is expressed in the built schema as a TYPE ARRAY "
                 "(['string','null']), not anyOf -- so any enum added later MUST "
                 "include null on a nullable leaf or a correct null becomes "
                 "unrepresentable under strict:true."),
        "leaf_count": len(src),
        "leaves": src,
        "absent_by_design": [
            "statementLevelSummary.utilisationPercent",
            "rewards.bonusPointsThisCycle",
            "statementMeta.rawStatementId",
            "statementMeta.statementPeriodStart",
            "statementMeta.statementPeriodEnd",
            "cards[].bigPicture.cardCreditLimit",
            "cards[].bigPicture.cardAvailableCreditLimit",
        ],
    }, open(PROV_OUT, "w"), indent=2)
    open(GENERIC_OUT, "w", encoding="utf-8").write(prompt)

    print(f"leaves: {len(got)}")
    for p in sorted(got):
        print(f"  {p:52s} {src[p]:16s} -> {got[p]['type']}")
    print(f"\nwrote {SCHEMA_OUT}\nwrote {PROV_OUT}\nwrote {GENERIC_OUT} ({len(prompt)} chars)")


if __name__ == "__main__":
    main()
