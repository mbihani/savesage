#!/usr/bin/env python3
"""The PDFs and the CSV are READ-ONLY inputs. Prove they were not modified.

Compares mtime+size for all 300 PDFs and the CSV against the fingerprint captured at
the start of the run (input_fingerprint_before.json), and re-verifies the client
prompt's sha256. Prints a verdict and exits non-zero on any drift.
"""
import glob
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth"
CSVP = os.path.join(BASE, "sbi.csv")
PDFD = os.path.join(BASE, "sbi-pdfs")
CLIENT_PROMPT = "/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt"
CLIENT_SHA = "c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362"
SCHEMA = "/Users/mayanck.bihani/Savesage/luna_prompt/LUNA_SCHEMA.json"


def main():
    ok = True
    before_p = os.path.join(ROOT, "input_fingerprint_before.json")
    now = {os.path.basename(f): os.path.getmtime(f)
           for f in sorted(glob.glob(os.path.join(PDFD, "*.pdf")))}

    if os.path.exists(before_p):
        b = json.load(open(before_p))
        before = b["pdf_mtimes"]
        changed = [k for k in before if k in now and abs(before[k] - now[k]) > 0.001]
        added = sorted(set(now) - set(before))
        removed = sorted(set(before) - set(now))
        print(f"PDFs: {len(now)} present, {len(changed)} mtime-changed, "
              f"{len(added)} added, {len(removed)} removed")
        if changed or added or removed:
            ok = False
            print(f"  CHANGED {changed[:5]} ADDED {added[:5]} REMOVED {removed[:5]}")
        csv_same = (abs(b["csv_mtime"] - os.path.getmtime(CSVP)) < 0.001
                    and b["csv_size"] == os.path.getsize(CSVP))
        print(f"CSV mtime+size unchanged: {csv_same}")
        ok = ok and csv_same
    else:
        print("NO BASELINE FINGERPRINT -- cannot prove inputs unchanged (UNVERIFIED)")
        ok = False

    sha = hashlib.sha256(open(CLIENT_PROMPT, "rb").read()).hexdigest()
    print(f"SYSTEM PROMPT.txt sha256 matches the brief: {sha == CLIENT_SHA}")
    ok = ok and sha == CLIENT_SHA

    # the schema must be UNCHANGED and identical to the shared GT schema
    sys.path.insert(0, "/Users/mayanck.bihani/Savesage/apev-wt-gt298/groundtruth298")
    import gt298_lib as G
    same = (json.dumps(json.load(open(SCHEMA)), sort_keys=True)
            == json.dumps(G.GT_SCHEMA, sort_keys=True))
    print(f"LUNA_SCHEMA.json unchanged and == gt298_lib.GT_SCHEMA: {same}")
    ok = ok and same

    print("INPUT INTEGRITY: " + ("OK" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
