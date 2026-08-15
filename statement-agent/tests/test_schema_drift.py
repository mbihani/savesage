"""Genuine schema drift check without importing legacy modules."""

import ast
import json
from pathlib import Path
import unittest

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_ROOT.parent


def _eval(node: ast.AST, names: dict[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(x, names) for x in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(x, names) for x in node.elts)
    if isinstance(node, ast.Dict):
        return {_eval(k, names): _eval(v, names) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _eval(node.left, names) + _eval(node.right, names)  # type: ignore[operator]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_s":
        return {"type": [_eval(node.args[0], names), "null"]}
    raise ValueError(f"unsupported schema expression: {ast.dump(node)}")


def extract_repo_schema() -> dict[str, object]:
    hdfc_tree = ast.parse((REPO_ROOT / "hdfc/hdfc_lib.py").read_text(encoding="utf-8"))
    references_gt_schema = any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "G"
        and node.attr == "GT_SCHEMA"
        for node in ast.walk(hdfc_tree)
    )
    if not references_gt_schema:
        raise AssertionError("HDFC response format no longer references G.GT_SCHEMA")

    tree = ast.parse((REPO_ROOT / "sbi/gt298_lib.py").read_text(encoding="utf-8"))
    names: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"TXN_TYPES", "GT_SCHEMA"}:
                names[name] = _eval(node.value, names)
    schema = names.get("GT_SCHEMA")
    if not isinstance(schema, dict):
        raise AssertionError("canonical GT_SCHEMA assignment not found")
    return schema


class SchemaDriftTest(unittest.TestCase):
    def test_vendored_schema_matches_repository_canonical_schema(self) -> None:
        vendored = json.loads((AGENT_ROOT / "schema/gt_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(extract_repo_schema(), vendored)


if __name__ == "__main__":
    unittest.main()
