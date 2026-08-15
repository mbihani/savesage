#!/usr/bin/env python3
"""Extract the six flagged fields from the incumbent CSV `data` JSON column and
compare against (i) our Luna v2 output and (ii) the PDF measurements."""
import csv
import glob
import json
import os
import re

CSV = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici.csv"
DLJ = "/Users/mayanck.bihani/Downloads/output/ICICI/JSON"
PROBE = os.path.dirname(os.path.abspath(__file__))
csv.field_size_limit(10 ** 9)

IDS = sorted({re.match(r"decrypt_(\d+)_", os.path.basename(p)).group(1)
              for p in glob.glob("/Users/mayanck.bihani/Downloads/output/ICICI/PDF/*.pdf")})

rows = {}
with open(CSV, newline="", encoding="utf-8", errors="replace") as f:
    for r in csv.DictReader(f):
        blob = str(r.get("link", "")) + " " + str(r.get("id", ""))
        for i in IDS:
            if i in blob:
                rows.setdefault(i, r)

ours = {}
for p in glob.glob(DLJ + "/*.json"):
    ours[re.match(r"decrypt_(\d+)_", os.path.basename(p)).group(1)] = json.load(open(p))

netm = json.load(open(os.path.join(PROBE, "net_l4_v2.json")))

print("=" * 108)
print("INCUMBENT (client Gemini parser) vs OURS (Luna v2) vs PDF   -- the six flagged fields")
print("=" * 108)
summary = {}
for i in IDS:
    r = rows.get(i)
    d = {}
    if r and r.get("data"):
        try:
            d = json.loads(r["data"])
        except Exception as e:
            d = {"__parse_error__": str(e)}
    o = ours.get(i, {})
    m = netm.get(i, {})
    print(f"\n{'='*100}\n### {i}   incumbent modelName={r.get('modelName') if r else None}")
    # ---- cards ----
    inc_cards = d.get("cards") or []
    our_cards = o.get("cards") or []
    print(f"  PDF printed card headings (reading order): {m.get('card_last4_reading_order')}")
    print(f"  PDF BIN per heading: {[(c['last4'], c['bin4'], c['bin_network']) for c in m.get('card_headings', [])]}")
    for lbl, cl in (("INCUMBENT", inc_cards), ("OURS", our_cards)):
        vals = [{"name": (c.get("cardMeta") or {}).get("cardDisplayName"),
                 "l4": (c.get("cardMeta") or {}).get("lastFourDigit"),
                 "net": (c.get("cardMeta") or {}).get("network")} for c in cl]
        print(f"  {lbl:<9} cards({len(cl)}): {json.dumps(vals, ensure_ascii=False)}")
    # ---- rewards ----
    ir = d.get("rewards") or {}
    orw = o.get("rewards") or {}
    keys = ["programType", "openingPoints", "pointsEarnedThisCycle",
            "pointsRedeemedThisCycle", "closingPoints"]
    print(f"  INCUMBENT rewards: { {k: ir.get(k) for k in keys} }")
    print(f"  OURS      rewards: { {k: orw.get(k) for k in keys} }")
    summary[i] = {
        "inc_networks": [(c.get("cardMeta") or {}).get("network") for c in inc_cards],
        "our_networks": [(c.get("cardMeta") or {}).get("network") for c in our_cards],
        "inc_names": [(c.get("cardMeta") or {}).get("cardDisplayName") for c in inc_cards],
        "our_names": [(c.get("cardMeta") or {}).get("cardDisplayName") for c in our_cards],
        "inc_l4": [(c.get("cardMeta") or {}).get("lastFourDigit") for c in inc_cards],
        "our_l4": [(c.get("cardMeta") or {}).get("lastFourDigit") for c in our_cards],
        "pdf_l4": m.get("card_last4_reading_order"),
        "pdf_bins": {c["last4"]: c["bin_network"] for c in m.get("card_headings", [])},
        "inc_closing": ir.get("closingPoints"), "our_closing": orw.get("closingPoints"),
        "inc_prog": ir.get("programType"), "our_prog": orw.get("programType"),
        "inc_redeem": ir.get("pointsRedeemedThisCycle"),
        "our_redeem": orw.get("pointsRedeemedThisCycle"),
        "inc_earn": ir.get("pointsEarnedThisCycle"), "our_earn": orw.get("pointsEarnedThisCycle"),
    }

json.dump(summary, open(os.path.join(PROBE, "incumbent_vs_ours.json"), "w"), indent=1)

print("\n\n" + "=" * 108)
print("NETWORK: incumbent asserted values vs PDF evidence (PDF prints NO card-own network anywhere)")
print("=" * 108)
fab = 0
for i, s in summary.items():
    inc = [n for n in s["inc_networks"] if n]
    if inc:
        fab += len(inc)
        print(f"  {i}: incumbent={s['inc_networks']}  ours={s['our_networks']}  "
              f"PDF BINs={s['pdf_bins']}")
print(f"\n  incumbent non-null network values across the 11: {fab}")
print(f"  ours non-null network values across the 11: "
      f"{sum(1 for s in summary.values() for n in s['our_networks'] if n)}")

print("\n" + "=" * 108)
print("CLOSING POINTS: incumbent vs ours (PDF has NO points balance in any of the 4 layouts)")
print("=" * 108)
for i, s in summary.items():
    print(f"  {i}: incumbent={s['inc_closing']!r:<12} ours={s['our_closing']!r:<8} "
          f"earn(inc/our)={s['inc_earn']}/{s['our_earn']}  redeem(inc/our)={s['inc_redeem']}/{s['our_redeem']}")

print("\n" + "=" * 108)
print("PROGRAM TYPE: incumbent vs ours")
print("=" * 108)
for i, s in summary.items():
    print(f"  {i}: incumbent={s['inc_prog']!r:<26} ours={s['our_prog']!r}")

print("\n" + "=" * 108)
print("cardDisplayName: incumbent vs ours")
print("=" * 108)
for i, s in summary.items():
    print(f"  {i}:\n     incumbent={s['inc_names']}\n     ours     ={s['our_names']}")

print("\n" + "=" * 108)
print("lastFourDigit: incumbent vs ours vs PDF")
print("=" * 108)
for i, s in summary.items():
    print(f"  {i}: PDF={s['pdf_l4']}  incumbent={s['inc_l4']}  ours={s['our_l4']}")
