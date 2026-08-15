"""NEGATIVE TEST for assert_schema.py -- prove the guard actually fails.

A contract guard that cannot fail is worthless. This injects one defect at a time
into a COPY of GEMINI_SCHEMA.json, runs assert_schema.py against it, and requires a
NONZERO exit for every defect, plus a ZERO exit for the untouched schema.

Both directions of the nullability biconditional are tested:
    null in type  and  null NOT in enum   -> must fail
    null NOT in type  and  null in enum    -> must fail
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def at(schema, path):
    node = schema
    for k in path:
        if node.get("type") == "array":
            node = node["items"]
        node = node["properties"][k]
    return node


def parent(schema, path):
    node = schema
    for k in path[:-1]:
        if node.get("type") == "array":
            node = node["items"]
        node = node["properties"][k]
    if node.get("type") == "array":
        node = node["items"]
    return node


DEFECTS = {}


def defect(name):
    def deco(fn):
        DEFECTS[name] = fn
        return fn
    return deco


@defect("enum omits null on a NULLABLE leaf (the dangerous direction)")
def _d1(s):
    leaf = at(s, ("transactions", "direction"))
    leaf["enum"] = [m for m in leaf["enum"] if m is not None]


@defect("enum contains null on a NON-nullable leaf")
def _d2(s):
    leaf = at(s, ("transactions", "direction"))
    leaf["type"] = ["string"]


@defect("27th leaf added")
def _d3(s):
    p = parent(s, ("rewards", "closingPoints"))
    p["properties"]["utilisationPercent"] = {"type": ["number", "null"]}
    p["required"].append("utilisationPercent")


@defect("leaf removed")
def _d4(s):
    p = parent(s, ("rewards", "closingPoints"))
    del p["properties"]["closingPoints"]
    p["required"].remove("closingPoints")


@defect("leaf retyped (number -> string)")
def _d5(s):
    at(s, ("rewards", "closingPoints"))["type"] = ["string", "null"]


@defect("disallowed key added to a leaf")
def _d6(s):
    at(s, ("rewards", "closingPoints"))["minimum"] = 0


@defect("enum member type-inconsistent with declared type")
def _d7(s):
    leaf = at(s, ("transactions", "txnType"))
    leaf["enum"] = leaf["enum"] + [42]


@defect("leaf renamed")
def _d8(s):
    p = parent(s, ("cards", "cardMeta", "network"))
    p["properties"]["cardNetwork"] = p["properties"].pop("network")
    p["required"][p["required"].index("network")] = "cardNetwork"


def run_in(tmp):
    return subprocess.run([sys.executable, os.path.join(tmp, "assert_schema.py")],
                          capture_output=True, text=True)


def main():
    base = json.load(open(os.path.join(HERE, "GEMINI_SCHEMA.json")))
    fails = []

    # --- control: untouched schema MUST pass ---
    with tempfile.TemporaryDirectory() as tmp:
        for f in ("assert_schema.py", "GEMINI_SCHEMA.json", "GEMINI_SCHEMA_PROVENANCE.json"):
            shutil.copy(os.path.join(HERE, f), tmp)
        r = run_in(tmp)
        if r.returncode != 0:
            fails.append(f"CONTROL: untouched schema FAILED the guard\n{r.stdout}{r.stderr}")
            print("  control (untouched)                                         -> FAIL (unexpected)")
        else:
            print("  control (untouched)                                         -> exit 0  OK")

    # --- each defect MUST be caught ---
    for name, mutate in DEFECTS.items():
        with tempfile.TemporaryDirectory() as tmp:
            for f in ("assert_schema.py", "GEMINI_SCHEMA_PROVENANCE.json"):
                shutil.copy(os.path.join(HERE, f), tmp)
            s = copy.deepcopy(base)
            mutate(s)
            json.dump(s, open(os.path.join(tmp, "GEMINI_SCHEMA.json"), "w"), indent=2)
            r = run_in(tmp)
            ok = r.returncode != 0
            print(f"  {name:<60}-> exit {r.returncode}  {'OK' if ok else 'NOT CAUGHT'}")
            if not ok:
                fails.append(f"{name}: guard did NOT fail (exit 0)\n{r.stdout}")
            else:
                why = [l for l in r.stdout.splitlines() if l.strip().startswith("- ")]
                for w in why[:2]:
                    print(f"        {w.strip()}")

    print()
    if fails:
        print("NEGATIVE TEST FAILED")
        for f in fails:
            print(" -", f)
        sys.exit(1)
    print(f"NEGATIVE TEST PASSED -- guard rejects all {len(DEFECTS)} injected defects "
          f"and accepts the real schema.")


if __name__ == "__main__":
    main()
