#!/usr/bin/env python3
"""Step 0: establish provenance of Downloads/output/ICICI/JSON vs repo runs."""
import glob, os, re, json, sys

REPO = "/Users/mayanck.bihani/Savesage/bank_eval/icici"
DLJ = "/Users/mayanck.bihani/Downloads/output/ICICI/JSON"


def leaves(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from leaves(v, p + "." + k if p else k)
    elif isinstance(o, list):
        if o:
            yield from leaves(o[0], p + "[]")
        else:
            yield (p + "[]", None)
    else:
        yield (p, o)


dl = {}
for f in glob.glob(DLJ + "/*.json"):
    b = os.path.basename(f)
    dl[re.match(r"decrypt_(\d+)_", b).group(1)] = f
lr = {os.path.basename(f)[:-5]: f for f in glob.glob(REPO + "/luna_refined/json/*.json")}

ids = sorted(dl)
print("== leaf path sets across the 11 (Downloads) ==")
pa_all, pc_all = set(), set()
for i in ids:
    pa_all |= {k for k, _ in leaves(json.load(open(dl[i])))}
    pc_all |= {k for k, _ in leaves(json.load(open(lr[i])))}
print("Downloads leaf paths:", len(pa_all))
print("luna_refined leaf paths:", len(pc_all))
print("DL-only:", sorted(pa_all - pc_all))
print("LR-only:", sorted(pc_all - pa_all))

print("\n== per-statement comparison ==")
hdr = f"{'id':<12} {'txnDL':>5} {'txnLR':>5} {'cardsDL':>7} {'cardsLR':>7}  netDL/LR  cpDL/LR  prDL/LR"
print(hdr)
for i in ids:
    a = json.load(open(dl[i]))
    c = json.load(open(lr[i]))
    na = [x["cardMeta"].get("network") for x in a.get("cards") or []]
    nc = [x["cardMeta"].get("network") for x in c.get("cards") or []]
    ra, rc = a.get("rewards") or {}, c.get("rewards") or {}
    print(
        f"{i:<12} {len(a.get('transactions') or []):>5} {len(c.get('transactions') or []):>5} "
        f"{len(a.get('cards') or []):>7} {len(c.get('cards') or []):>7}  {na}/{nc}  "
        f"{ra.get('closingPoints')}/{rc.get('closingPoints')}  "
        f"{ra.get('pointsRedeemedThisCycle')}/{rc.get('pointsRedeemedThisCycle')}"
    )

print("\n== the six flagged fields, Downloads arm ==")
for i in ids:
    a = json.load(open(dl[i]))
    r = a.get("rewards") or {}
    for ci, cd in enumerate(a.get("cards") or []):
        m = cd["cardMeta"]
        print(
            f"{i:<12} card{ci} name={m.get('cardDisplayName')!r:<42} l4={m.get('lastFourDigit')!r:<8} "
            f"net={m.get('network')!r:<6} fam={m.get('productFamily')!r}"
        )
    print(
        f"{'':<12}      rewards: programType={r.get('programType')!r:<16} open={r.get('openingPoints')} "
        f"earn={r.get('pointsEarnedThisCycle')} redeem={r.get('pointsRedeemedThisCycle')} close={r.get('closingPoints')}"
    )
