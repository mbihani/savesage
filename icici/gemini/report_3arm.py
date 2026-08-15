"""Render the 3-arm comparison table from analysis_3arm.json."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
A = json.load(open(os.path.join(HERE, "analysis_3arm.json")))
ORACLE = json.load(open(os.path.join(HERE, "pdf_oracle.json")))
ARMS = ["A", "B", "C"]
NAME = {"A": "A NEW refined", "B": "B PREV refined", "C": "C client generic"}


def pct(a, b):
    return f"{100.0*a/b:5.1f}%" if b else "   n/a"


print("=" * 100)
print("ARMS  (same 11 PDFs, same 26-leaf schema, same model/effort; ONLY the prompt differs)")
print("=" * 100)
for a in ARMS:
    d = A[a]
    print(f"  {NAME[a]:<18} {d['prompt_path']:<24} sha={d['prompt_sha256'][:12]} "
          f"n={d['n_statements']} outcomes={d['outcomes']}")

print("\n" + "=" * 100)
print("A. CORRECTNESS-SCORED  (a PDF oracle exists -> right/wrong is meaningful)")
print("=" * 100)
hdr = f"{'field':<40}" + "".join(f"{NAME[a]:>20}" for a in ARMS)
print(hdr)
print("-" * 100)

rows = []
# lastFourDigit
rows.append(("cards[].lastFourDigit (per card)",
             [f"{A[a]['scalars']['lastFourDigit']['cards_correct']}/"
              f"{A[a]['scalars']['lastFourDigit']['cards_total']}" for a in ARMS]))
rows.append(("  statements with exact card set",
             [f"{A[a]['scalars']['lastFourDigit']['statements_set_exact']}/11" for a in ARMS]))
rows.append(("  values containing 'X' (defect)",
             [str(A[a]['scalars']['lastFourDigit']['values_containing_X']) for a in ARMS]))
rows.append(("statementMeta.issuerName",
             [f"{A[a]['scalars']['issuerName']['ok']}/{A[a]['scalars']['issuerName']['n']}"
              for a in ARMS]))
for k in ["programType", "pointsEarnedThisCycle", "pointsRedeemedThisCycle"]:
    rows.append((f"rewards.{k}",
                 [f"{A[a]['scalars']['rewards'][k]['ok']}/{A[a]['scalars']['rewards'][k]['n']}"
                  for a in ARMS]))
for lbl, ok, n in [("transactions[].description EXACT", "desc_exact", "matched"),
                   ("transactions[].description >=0.95", "desc_sim95", "matched"),
                   ("transactions[].date", "date_ok", "date_n"),
                   ("transactions[].amount", "amount_ok", "amount_n"),
                   ("transactions[].direction", "dir_ok", "dir_n")]:
    rows.append((lbl, [f"{A[a]['transactions'][ok]}/{A[a]['transactions'][n]}"
                       f" {pct(A[a]['transactions'][ok], A[a]['transactions'][n])}"
                       for a in ARMS]))
for lbl, vals in rows:
    print(f"{lbl:<40}" + "".join(f"{v:>20}" for v in vals))

print("\n" + "=" * 100)
print("B. NON-DISCRIMINATING  (oracle says NULL on all 11 -> cannot separate arms;")
print("   reported as fabrication counts, NEVER as an accuracy percentage)")
print("=" * 100)
print(f"{'field':<40}" + "".join(f"{NAME[a]:>20}" for a in ARMS))
print("-" * 100)
print(f"{'cards[].network  non-null (fabrications)':<40}"
      + "".join(f"{A[a]['scalars']['network']['non_null_values']:>20}" for a in ARMS))
for k in ["closingPoints", "openingPoints", "pointsExpiringNext30Days",
          "pointsExpiringNext60Days"]:
    vals = []
    for a in ARMS:
        d = A[a]['scalars']['rewards'][k]
        vals.append(f"{d['n']-d['ok']} wrong")
    print(f"{'rewards.'+k+'  (oracle null x11)':<40}" + "".join(f"{v:>20}" for v in vals))

print("\n  network fabrication detail:")
for a in ARMS:
    d = A[a]['scalars']['network']['detail']
    print(f"    {NAME[a]:<18} {d if d else 'none'}")

print("\n" + "=" * 100)
print("C. DUPLICATION INVARIANT  closingPoints == pointsEarnedThisCycle")
print("=" * 100)
for a in ARMS:
    d = A[a]["scalars"]["duplication_invariant"]
    print(f"  {NAME[a]:<18} equal={d['closingPoints_equals_pointsEarned']} "
          f"BACKED={d['BACKED']} UNBACKED={d['UNBACKED']}")
    for x in d["detail"]:
        print(f"      {x['sid']} value={x['value']} backed={x['backed_by_printed_balance']}")

print("\n" + "=" * 100)
print("D. TRANSACTION ROW RECOVERY  (PDF row truth = 172 rows across the 11)")
print("=" * 100)
for a in ARMS:
    t = A[a]["transactions"]
    print(f"  {NAME[a]:<18} pdf={t['pdf_rows']} model={t['model_rows']} "
          f"matched={t['matched']}  missing={t['pdf_rows']-t['matched']}  "
          f"extra={t['model_rows']-t['matched']}")
print("\n  per-statement (pdf/model/matched) where model != pdf:")
for a in ARMS:
    bad = {s: v for s, v in A[a]["per_statement_txn"].items() if v["pdf"] != v["model"]}
    print(f"    {NAME[a]:<18} {bad if bad else 'all statements row-exact'}")

print("\n" + "=" * 100)
print("E. POPULATED-ONLY  (no PDF oracle -> fill rate, NOT accuracy)")
print("=" * 100)
print(f"{'leaf':<48}" + "".join(f"{NAME[a]:>17}" for a in ARMS))
print("-" * 100)
scored = {"cards[].cardMeta.lastFourDigit", "cards[].cardMeta.network",
          "statementMeta.issuerName", "rewards.programType",
          "rewards.pointsEarnedThisCycle", "rewards.pointsRedeemedThisCycle",
          "rewards.closingPoints", "rewards.openingPoints",
          "rewards.pointsExpiringNext30Days", "rewards.pointsExpiringNext60Days",
          "transactions[].description", "transactions[].date", "transactions[].amount",
          "transactions[].direction"}
for leaf in A["A"]["populated"]:
    if leaf in scored:
        continue
    vals = [f"{A[a]['populated'][leaf]['non_null']}/{A[a]['populated'][leaf]['values']}"
            for a in ARMS]
    print(f"{leaf:<48}" + "".join(f"{v:>17}" for v in vals))

print("\n" + "=" * 100)
print("F. TOKENS  (COUNTS ONLY -- Luna's price is unpublished, no dollar figures)")
print("=" * 100)
for a in ARMS:
    t = A[a]["tokens"]
    print(f"  {NAME[a]:<18} prompt={t['prompt_tokens']:>8} completion={t['completion_tokens']:>7} "
          f"total={t['total_tokens']:>8}  identity_holds={t['identity_holds']} "
          f"violations={len(t['violations'])}")
tot = sum(A[a]["tokens"]["total_tokens"] for a in ARMS)
print(f"  {'ALL THREE ARMS':<18} total={tot}")

print("\n" + "=" * 100)
print("G. WRONG-VALUE DETAIL for correctness-scored rewards fields")
print("=" * 100)
for a in ARMS:
    print(f"\n  {NAME[a]}")
    for k, d in A[a]["scalars"]["rewards"].items():
        if d["wrong"]:
            print(f"    {k}: {d['ok']}/{d['n']}")
            for sid, got, exp in d["wrong"][:12]:
                print(f"       {sid}: got={got!r} oracle={exp!r}")
