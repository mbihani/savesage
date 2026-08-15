#!/usr/bin/env python3
"""Which prompt version produced the Downloads JSONs? Characterise the 5 diffs.

v1 = sha 2ba79095 (11885B, commit b2cf196a)  -> the luna_refined 304-corpus run
v2 = sha 79325334 (14995B, commit a22d794a)  -> current committed prompt == Downloads/ICICI_PROMPT.txt
v2 claims: repair lastFourDigit mask slicing + narration fidelity
"""
import glob, os, re, json

REPO = "/Users/mayanck.bihani/Savesage/bank_eval/icici"
DLJ = "/Users/mayanck.bihani/Downloads/output/ICICI/JSON"

dl = {}
for f in glob.glob(DLJ + "/*.json"):
    dl[re.match(r"decrypt_(\d+)_", os.path.basename(f)).group(1)] = f
lr = {os.path.basename(f)[:-5]: f for f in glob.glob(REPO + "/luna_refined/json/*.json")}


def flat(o, p=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flat(v, f"{p}.{k}" if p else k))
    elif isinstance(o, list):
        for n, v in enumerate(o):
            out.update(flat(v, f"{p}[{n}]"))
    else:
        out[p] = o
    return out


print("=== field-level diffs, Downloads(DL) vs luna_refined-v1(LR) ===")
for i in sorted(dl):
    a, b = flat(json.load(open(dl[i]))), flat(json.load(open(lr[i])).get("parsed_json") or {})
    keys = sorted(set(a) | set(b))
    ds = [(k, a.get(k, "<absent>"), b.get(k, "<absent>")) for k in keys if a.get(k, "<absent>") != b.get(k, "<absent>")]
    if not ds:
        continue
    print(f"\n--- {i}  ({len(ds)} differing leaves) ---")
    for k, va, vb in ds[:18]:
        print(f"  {k}\n      DL={va!r}\n      LR={vb!r}")
    if len(ds) > 18:
        print(f"  ... +{len(ds)-18} more")

# Does lastFourDigit discriminate? v1 bug produced 'XX02'-style values.
print("\n=== lastFourDigit 'X' contamination across the FULL v1 corpus (304) ===")
badx = []
for sid, f in lr.items():
    pj = json.load(open(f)).get("parsed_json") or {}
    for c in pj.get("cards") or []:
        v = (c.get("cardMeta") or {}).get("lastFourDigit")
        if isinstance(v, str) and ("X" in v.upper()):
            badx.append((sid, v))
print(f"v1 corpus cards with 'X' in lastFourDigit: {len(badx)}")
print("  sample:", badx[:12])
print("of the 11 sample ids, v1 X-contaminated:", [x for x in badx if x[0] in dl])
print("\nDownloads(11) cards with 'X' in lastFourDigit:",
      [(i, (c.get('cardMeta') or {}).get('lastFourDigit'))
       for i in dl for c in (json.load(open(dl[i])).get('cards') or [])
       if 'X' in str((c.get('cardMeta') or {}).get('lastFourDigit')).upper()])
