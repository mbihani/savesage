"""Contract guard for GEMINI_SCHEMA.json. Run before every sweep.

THE CLIENT'S 26-LEAF CONTRACT IS INVIOLABLE. The enum/description work is a
CONSTRAINT-TIGHTENING change: it may add `enum` and `description` keys to existing
leaves and nothing else. This script fails loudly on any drift.

It checks, against GEMINI_SCHEMA_PROVENANCE.json (the record of the client type-map
this schema was converted from):

  1. leaf count is EXACTLY 26
  2. the 26 leaf PATHS are unchanged -- no field added, removed or renamed
  3. every leaf's `type` is unchanged from the converted original
  4. the only keys ever added to a leaf are `enum` and `description`
  5. NULLABILITY vs ENUM COHERENCE -- the load-bearing check. This schema is sent
     with strict:True. Nullability here is expressed as a TYPE ARRAY
     (`"type": ["string","null"]`), not anyOf. Under strict mode an enum on a
     nullable leaf MUST list null as a member, or a correct null becomes
     unrepresentable and the model is forced to invent a non-null value. So:
     null in `type`  <=>  null in `enum`.
  6. every non-null enum member is type-consistent with the leaf's declared type

WHY convert_schema.py IS NOT RE-RUN: it regenerates this file from the client source
and would silently discard the enums and descriptions. It is provenance, not a build
step. This script is the check that replaces it.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "GEMINI_SCHEMA.json")
PROV_PATH = os.path.join(HERE, "GEMINI_SCHEMA_PROVENANCE.json")

EXPECTED_LEAF_COUNT = 26
ALLOWED_ADDED_KEYS = {"enum", "description"}

JSON_TYPE_OF = {str: "string", bool: "boolean", int: "number", float: "number"}


def leaves(node, path=()):
    """Walk to the leaves. Arrays are transparent -- items share the parent path,
    matching the client type-map's own convention (cards[].cardMeta.network)."""
    out = {}
    if node.get("type") == "object":
        for k, v in node["properties"].items():
            out.update(leaves(v, path + (k,)))
    elif node.get("type") == "array":
        out.update(leaves(node["items"], path))
    else:
        out[".".join(path)] = node
    return out


def main():
    schema = json.load(open(SCHEMA_PATH))
    prov = json.load(open(PROV_PATH))
    got = leaves(schema)
    errors = []

    # 1 -- leaf count
    if len(got) != EXPECTED_LEAF_COUNT:
        errors.append(f"leaf count is {len(got)}, expected {EXPECTED_LEAF_COUNT}")

    # 2 -- leaf paths unchanged vs the client type-map
    expected_paths = set(prov["leaves"])
    added = sorted(set(got) - expected_paths)
    removed = sorted(expected_paths - set(got))
    if added:
        errors.append(f"FIELDS ADDED (forbidden): {added}")
    if removed:
        errors.append(f"FIELDS REMOVED/RENAMED (forbidden): {removed}")

    for path in sorted(set(got) & expected_paths):
        leaf = got[path]

        # 3 -- type unchanged. Provenance stores the client's 'string|null' spelling.
        src = prov["leaves"][path]
        want = []
        for tok in str(src).split("|"):
            tok = tok.strip()
            want.append({"str": "string", "string": "string", "int": "number",
                         "float": "number", "number": "number", "bool": "boolean",
                         "boolean": "boolean", "null": "null", "None": "null"}.get(tok, tok))
        if sorted(leaf.get("type") or []) != sorted(want):
            errors.append(f"{path}: type changed -- schema={leaf.get('type')} "
                          f"provenance={src} (normalised {want})")

        # 4 -- only enum/description may have been added
        extra = set(leaf) - {"type"} - ALLOWED_ADDED_KEYS
        if extra:
            errors.append(f"{path}: unexpected keys {sorted(extra)}")

        # 5 -- nullability/enum coherence under strict mode
        if "enum" in leaf:
            types = leaf.get("type") or []
            null_ok = "null" in types
            null_in_enum = any(m is None for m in leaf["enum"])
            if null_ok and not null_in_enum:
                errors.append(
                    f"{path}: NULLABLE but enum omits null -- under strict:True a "
                    f"correct null becomes unrepresentable. Add null to the enum.")
            if null_in_enum and not null_ok:
                errors.append(f"{path}: enum contains null but type does not allow null")

            # 6 -- enum members type-consistent
            for m in leaf["enum"]:
                if m is None:
                    continue
                jt = JSON_TYPE_OF.get(type(m))
                if jt not in types:
                    errors.append(f"{path}: enum member {m!r} is {jt}, "
                                  f"not in declared type {types}")

    print(f"schema        : {SCHEMA_PATH}")
    print(f"leaf count    : {len(got)}  (required {EXPECTED_LEAF_COUNT})")
    enum_leaves = sorted(p for p, l in got.items() if "enum" in l)
    desc_leaves = sorted(p for p, l in got.items() if "description" in l)
    print(f"enums on      : {enum_leaves}")
    for p in enum_leaves:
        print(f"    {p:44s} type={got[p]['type']} enum={got[p]['enum']}")
    print(f"descriptions  : {desc_leaves}")

    if errors:
        print("\nFAIL")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("\nOK -- 26-leaf contract intact; enums are null-coherent under strict mode.")


if __name__ == "__main__":
    main()
