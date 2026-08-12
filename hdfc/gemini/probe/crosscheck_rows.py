"""Probe self-audit: is the geometric row extractor dropping or inventing rows?

Two independent cross-checks, because a probe that silently drops rows would produce
FALSE 'model dropped transactions' accusations later:
  1. Count date anchors in the raw TEXT LAYER per file and compare to the geometric
     row count. A mismatch localises which rows the geometry missed.
  2. Hunt for a trailing 'Cr'/'CR' suffix near an amount. The HDFC prompt lists it as
     a legal credit marker; if any row uses it, the geometric probe (which reads only
     '+' and green) would under-count CREDIT.
"""
import re
import sys

sys.path.insert(0, "..")
import pdf_rows as P  # noqa: E402

A_TXT = re.compile(r"\b\d{2}/\d{2}/\d{4}\s*\|")
B_TXT = re.compile(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4},\s*\d{2}:\d{2}")
CR_SUFFIX = re.compile(r"[\d][\d,]*\.\d{2}\s*(?:Cr|CR)\b")

print(f"{'stmt_id':<12}{'lay':<4}{'geom':>6}{'text':>6}{'delta':>7}  CrSuffix")
tg = tt = 0
for sid, fn, path in P.corpus():
    ex = P.extract(path)
    txt = P.full_text(path)
    n_txt = len(A_TXT.findall(txt)) if ex["layout"] == "A" else len(B_TXT.findall(txt))
    n_geom = len(ex["rows"])
    crs = CR_SUFFIX.findall(txt)
    tg += n_geom
    tt += n_txt
    flag = "" if n_geom == n_txt else "  <-- MISMATCH"
    print(f"{P.statement_id(fn) or '-':<12}{ex['layout']:<4}{n_geom:>6}{n_txt:>6}"
          f"{n_geom - n_txt:>7}  {len(crs)}{flag}")
print(f"{'TOTAL':<12}{'':<4}{tg:>6}{tt:>6}{tg - tt:>7}")
