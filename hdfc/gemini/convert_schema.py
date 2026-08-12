"""Convert the client's ORIGINAL Gemini 3.0 Flash `SCHEMA` into a strict JSON Schema.

WHY A CONVERTER AND NOT A HAND-TYPED FILE
-----------------------------------------
Line 64 of the source file is a single-quoted PYTHON STRING holding a pseudo-JSON
TYPE MAP -- `{"statementMeta":{"issuerName":"string|null",...}}`. Every leaf is a
STRING naming a type, not a JSON Schema node. It therefore cannot be handed to
`response_format` as-is: the serving endpoint would reject it (no "type" keyword at
the root) or, worse, silently accept a schema that constrains nothing.

Hand-transcribing it would put a human in the loop between the client's baseline and
the schema we actually send, which is exactly the thing that must not be in doubt --
the whole point of adopting the client's schema is comparability with their Gemini
run. So the leaf set is PARSED from the source and the JSON Schema is GENERATED. The
generated leaf inventory is asserted against the source leaf inventory at build time;
any addition or omission is a hard failure, not a diff to be eyeballed.

The type map's `"string|null"` / `"number|null"` / `"boolean|null"` vocabulary maps
onto the repo's existing `_s(t)` convention (`{"type": [t, "null"]}`) so the emitted
schema is structurally identical in style to gt298_lib.GT_SCHEMA.

NO ENUMS ARE ADDED -- DELIBERATELY, AND THIS IS A JUDGEMENT CALL
---------------------------------------------------------------
GT_SCHEMA pins `transactions.direction` to ["DEBIT","CREDIT",null] and
`transactions.txnType` to an 11-value closed list. The client's type map pins NEITHER
(both are bare "string|null"), and the original generic prompt names no closed list
for txnType at all. Two options existed:

  (a) port GT_SCHEMA's enums across, or
  (b) keep the client's unconstrained strings and let the PROMPT supply the vocabulary.

This converter does (b), for one reason that outweighs the convenience of (a): an enum
in `response_format` is enforced by the DECODER, so it does not merely encourage the
right vocabulary, it makes the wrong vocabulary unrepresentable. If the model would
otherwise have emitted "Credit", "CR", or "+" as a direction, an enum HIDES that and
the measured conformance rate becomes an artefact of the schema rather than a fact
about the model. Since the point of adopting the client's schema is comparability with
their Gemini baseline, a constraint their baseline never had would silently improve our
numbers relative to theirs.

Consequence, accepted knowingly: off-vocabulary values are now POSSIBLE, and the
per-field analysis therefore MEASURES and reports vocabulary conformance for
`direction` and `txnType` instead of assuming it. That is strictly more informative.
The HDFC prompt carries the vocabularies (and had to be edited to stop saying "the
schema's closed list", which is no longer true -- see PROMPT_CHANGELOG entry C7).
"""

import ast
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "/Users/mayanck.bihani/Downloads/gemini-3-flash--prompt-shcema.txt"

# --------------------------------------------------------------- source extraction


def read_source():
    with open(SRC, "rb") as fh:
        raw = fh.read()
    return raw, hashlib.sha256(raw).hexdigest()


def extract_assignments(src_text):
    """Return (SYSTEM_PROMPT, SCHEMA_type_map) via AST, never regex/slicing.

    ast.literal_eval yields exactly the string Python itself would build, so an
    embedded quote sequence cannot silently corrupt the extraction. Same technique
    hdfc_lib.baseline_prompt() already uses on the client's other prompt file.
    """
    out = {}
    for node in ast.parse(src_text).body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in ("SYSTEM_PROMPT", "SCHEMA"):
                out[t.id] = ast.literal_eval(node.value)
    for k in ("SYSTEM_PROMPT", "SCHEMA"):
        if k not in out:
            raise RuntimeError(f"{k} assignment not found in {SRC}")
        if not isinstance(out[k], str):
            raise RuntimeError(f"{k} is not a string literal")
    return out["SYSTEM_PROMPT"], out["SCHEMA"]


def leaf_inventory(node, path=()):
    """Walk the parsed type map -> {dotted.path: "type|null"}.

    A list in the type map means "array of this one item shape"; the `[]` is dropped
    from the path so `cards[].cardMeta.network` reads as `cards.cardMeta.network`.
    """
    leaves = {}
    if isinstance(node, dict):
        for k, v in node.items():
            leaves.update(leaf_inventory(v, path + (k,)))
    elif isinstance(node, list):
        if len(node) != 1:
            raise RuntimeError(f"array at {'.'.join(path)} has {len(node)} item shapes, expected 1")
        leaves.update(leaf_inventory(node[0], path))
    elif isinstance(node, str):
        leaves[".".join(path)] = node
    else:
        raise RuntimeError(f"unexpected node type {type(node)} at {'.'.join(path)}")
    return leaves


# --------------------------------------------------------------- schema generation

_TYPEWORD = {"string": "string", "number": "number", "boolean": "boolean"}


def _leaf_schema(typespec, dotted):
    """"string|null" -> {"type": ["string", "null"]}. No enums -- see module docstring."""
    parts = [p.strip() for p in typespec.split("|")]
    if "null" not in parts:
        raise RuntimeError(f"leaf {dotted} is not nullable ({typespec!r}); the prompt "
                           f"says 'use null where data is missing' for every field")
    base = [p for p in parts if p != "null"]
    if len(base) != 1 or base[0] not in _TYPEWORD:
        raise RuntimeError(f"leaf {dotted} has unsupported type {typespec!r}")
    return {"type": [_TYPEWORD[base[0]], "null"]}


def build_schema(type_map):
    """Generate the strict JSON Schema. Every object gets additionalProperties:false
    and `required` listing ALL of its keys -- OpenAI-dialect strict mode demands that
    every property be required, and the prompt independently says "Always output all
    fields; use null where data is missing", so nullable-and-required is the correct
    encoding of the client's intent."""

    def walk(node, path=()):
        if isinstance(node, dict):
            props = {k: walk(v, path + (k,)) for k, v in node.items()}
            return {
                "type": "object",
                "additionalProperties": False,
                "required": list(node.keys()),
                "properties": props,
            }
        if isinstance(node, list):
            return {"type": "array", "items": walk(node[0], path)}
        return _leaf_schema(node, ".".join(path))

    return walk(json.loads(type_map))


def main():
    raw, sha = read_source()
    prompt, schema_str = extract_assignments(raw.decode("utf-8"))
    type_map = json.loads(schema_str)

    src_leaves = leaf_inventory(type_map)
    schema = build_schema(schema_str)

    # ---- the load-bearing assertion: generated leaves == source leaves, exactly.
    def schema_leaves(node, path=()):
        out = {}
        if node.get("type") == "object":
            for k, v in node["properties"].items():
                out.update(schema_leaves(v, path + (k,)))
        elif node.get("type") == "array":
            out.update(schema_leaves(node["items"], path))
        else:
            out[".".join(path)] = node
        return out

    gen = schema_leaves(schema)
    missing = sorted(set(src_leaves) - set(gen))
    added = sorted(set(gen) - set(src_leaves))
    if missing or added:
        raise SystemExit(f"LEAF MISMATCH  missing={missing}  added={added}")

    outdir = HERE
    with open(os.path.join(outdir, "GEMINI_SCHEMA.json"), "w") as fh:
        json.dump(schema, fh, indent=2)
        fh.write("\n")
    with open(os.path.join(outdir, "GEMINI_GENERIC_PROMPT.txt"), "w") as fh:
        fh.write(prompt)

    inv = {
        "source_file": SRC,
        "source_sha256": sha,
        "source_line_for_schema": 64,
        "source_line_for_prompt": "1-61 (SYSTEM_PROMPT triple-quoted string)",
        "extraction_method": "ast.literal_eval of the Python assignment, not regex",
        "leaf_count": len(src_leaves),
        "leaves": {k: src_leaves[k] for k in sorted(src_leaves)},
        "generic_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "generated_schema_sha256": hashlib.sha256(
            json.dumps(schema, indent=2).encode() + b"\n").hexdigest(),
    }
    with open(os.path.join(outdir, "GEMINI_SCHEMA_PROVENANCE.json"), "w") as fh:
        json.dump(inv, fh, indent=2)
        fh.write("\n")

    print(f"source sha256 : {sha}")
    print(f"leaf count    : {len(src_leaves)}")
    for k in sorted(src_leaves):
        print(f"  {k:52s} {src_leaves[k]}")


if __name__ == "__main__":
    main()
