"""STEP 1 deliverable: diff the converted Gemini schema against the repo's GT_SCHEMA.

Both directions, mechanically, so the table in the report cannot drift from the files.
Leaf paths are compared, plus the per-leaf TYPE and any enum constraint, because a
field present in both schemas but constrained differently is also a real difference
(transactions.txnType is exactly that case).
"""
import json
import os
import sys

sys.path.insert(0, "/Users/mayanck.bihani/Savesage/apev-wt-gt298/groundtruth298")
import gt298_lib as G  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def leaves(node, path=()):
    out = {}
    t = node.get("type")
    if t == "object":
        for k, v in node.get("properties", {}).items():
            out.update(leaves(v, path + (k,)))
    elif t == "array":
        out.update(leaves(node["items"], path))
    else:
        out[".".join(path)] = node
    return out


def describe(n):
    t = n.get("type")
    base = "/".join(x for x in (t if isinstance(t, list) else [t]) if x != "null")
    return base + (f" enum[{len(n['enum'])}]" if "enum" in n else "")


def main():
    gem = json.load(open(os.path.join(HERE, "GEMINI_SCHEMA.json")))
    gl, tl = leaves(gem), leaves(G.GT_SCHEMA)

    only_gem = sorted(set(gl) - set(tl))
    only_gt = sorted(set(tl) - set(gl))
    both = sorted(set(gl) & set(tl))
    type_diff = [(k, describe(gl[k]), describe(tl[k])) for k in both
                 if describe(gl[k]) != describe(tl[k])]

    lines = []
    lines.append("## Schema diff — converted Gemini schema vs repo `GT_SCHEMA`\n")
    lines.append(f"- Gemini leaf fields: **{len(gl)}**")
    lines.append(f"- GT_SCHEMA leaf fields: **{len(tl)}**")
    lines.append(f"- Shared: **{len(both)}**\n")

    lines.append("### Present in GT_SCHEMA, ABSENT from the Gemini schema\n")
    lines.append("| # | leaf path | GT_SCHEMA type |")
    lines.append("|---|---|---|")
    for i, k in enumerate(only_gt, 1):
        lines.append(f"| {i} | `{k}` | {describe(tl[k])} |")
    lines.append("")

    lines.append("### Present in the Gemini schema, ABSENT from GT_SCHEMA\n")
    if only_gem:
        lines.append("| # | leaf path | Gemini type |")
        lines.append("|---|---|---|")
        for i, k in enumerate(only_gem, 1):
            lines.append(f"| {i} | `{k}` | {describe(gl[k])} |")
    else:
        lines.append("**None.** The Gemini schema is a strict SUBSET of GT_SCHEMA's leaf set.")
    lines.append("")

    lines.append("### Shared leaf, DIFFERENT constraint\n")
    if type_diff:
        lines.append("| leaf path | Gemini | GT_SCHEMA |")
        lines.append("|---|---|---|")
        for k, a, b in type_diff:
            lines.append(f"| `{k}` | {a} | {b} |")
    else:
        lines.append("None.")
    lines.append("")

    # the brief's specific claim about utilisationPercent
    lines.append("### `statementLevelSummary.utilisationPercent`\n")
    in_gem = any("utilisation" in k.lower() for k in gl)
    in_gt = any("utilisation" in k.lower() for k in tl)
    lines.append(f"- in converted Gemini schema: **{in_gem}**")
    lines.append(f"- in repo `GT_SCHEMA`: **{in_gt}**")
    lines.append("")

    out = os.path.join(HERE, "SCHEMA_DIFF.md")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
