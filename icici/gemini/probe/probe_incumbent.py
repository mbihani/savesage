#!/usr/bin/env python3
"""Which incumbent reference covers these 11 statements, and what does it say
about the six flagged fields? Also: what parser produced it (detectionSource/modelName)?"""
import csv
import glob
import json
import os
import re
import sys

CSV = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici.csv"
XLSX = "/Users/mayanck.bihani/Downloads/serving-test/xlsx-by-bank/ICICI.xlsx"
IDS = sorted({re.match(r"decrypt_(\d+)_", os.path.basename(p)).group(1)
              for p in glob.glob("/Users/mayanck.bihani/Downloads/output/ICICI/PDF/*.pdf")})
print("target ids:", IDS)

csv.field_size_limit(10 ** 9)

print("\n" + "=" * 90)
print("CSV:", CSV)
print("=" * 90)
with open(CSV, newline="", encoding="utf-8", errors="replace") as f:
    rd = csv.DictReader(f)
    cols = rd.fieldnames
    print("columns:", cols)
    rows = list(rd)
print("n_rows:", len(rows))

# find the id column
idcol = None
for c in cols:
    vals = [str(r.get(c, "")) for r in rows[:400]]
    if sum(any(i in v for i in IDS) for v in vals):
        idcol = c
        break
print("id-bearing column:", idcol)


def find_rows(rows, cols):
    hit = {}
    for r in rows:
        blob = " ".join(str(v) for v in r.values())
        for i in IDS:
            if i in blob:
                hit.setdefault(i, []).append(r)
    return hit


hits = find_rows(rows, cols)
print("ids covered by CSV:", sorted(hits), f"({len(hits)}/11)")

for c in cols:
    if re.search(r"detectionSource|modelName|model|source|parser", c, re.I):
        vs = {}
        for r in rows:
            vs[str(r.get(c))] = vs.get(str(r.get(c)), 0) + 1
        print(f"  {c}: {dict(list(sorted(vs.items(), key=lambda kv: -kv[1]))[:6])}")

SIX = ["cardDisplayName", "lastFourDigit", "network", "closingPoints", "programType",
       "pointsRedeemedThisCycle"]
print("\ncolumns matching the six flagged fields:")
for c in cols:
    if any(s.lower() in c.lower() for s in SIX):
        print("   ", c)

print("\n=== per-id incumbent values for the six fields ===")
for i in sorted(hits):
    for r in hits[i][:3]:
        out = {}
        for c in cols:
            if any(s.lower() in c.lower() for s in SIX):
                out[c] = r.get(c)
        print(f"  {i}: {out}")

print("\n" + "=" * 90)
print("XLSX:", XLSX)
print("=" * 90)
try:
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    for ws in wb.worksheets:
        print(f"  sheet={ws.title!r} dims={ws.calculate_dimension()}")
        hdr = None
        found = set()
        for n, row in enumerate(ws.iter_rows(values_only=True)):
            if n == 0:
                hdr = [str(c) for c in row]
                print("   header:", hdr[:28])
                continue
            blob = " ".join("" if c is None else str(c) for c in row)
            for i in IDS:
                if i in blob:
                    found.add(i)
        print(f"   ids covered: {sorted(found)} ({len(found)}/11)")
except Exception as e:
    print("  openpyxl failed:", type(e).__name__, e)
