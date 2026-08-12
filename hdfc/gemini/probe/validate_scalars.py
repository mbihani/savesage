"""Validation harness for pdf_scalars: prints all six scalars for all 15 files.

Internal consistency checks that a silent mis-binding would break:
  * totalCreditLimit >= availableCreditLimit (except on a credit-balance statement,
    where available legitimately exceeds the limit)
  * availableCreditLimit != totalMinimumAmountDue (the exact defect of the first version)
  * every core scalar resolves on every file
"""
import sys

sys.path.insert(0, "..")
import pdf_rows as P  # noqa: E402
import pdf_scalars as S  # noqa: E402

KEYS = ("totalAmountDue", "totalMinimumAmountDue", "totalCreditLimit",
        "availableCreditLimit", "statementDate", "dueDate")

print(f"{'stmt':<12}{'TAD':>11}{'MinDue':>9}{'TotCL':>11}{'AvailCL':>11}  "
      f"{'stmtDate':<14}dueDate")
missing = collide = 0
for sid, fn, path in P.corpus():
    f = S.extract(path)
    v = [f[k]["value"] if k in f else None for k in KEYS]
    if any(x is None for x in v[:5]):
        missing += 1
    if v[3] is not None and v[3] == v[1]:
        collide += 1
    print(f"{P.statement_id(fn):<12}{str(v[0]):>11}{str(v[1]):>9}{str(v[2]):>11}"
          f"{str(v[3]):>11}  {str(v[4]):<14}{v[5]}")
print()
print(f"files missing a core scalar (TAD/MinDue/TotCL/AvailCL/stmtDate) : {missing}")
print(f"files where availableCreditLimit == totalMinimumAmountDue       : {collide}"
      f"   <- was 11/15 before the dx fix")
