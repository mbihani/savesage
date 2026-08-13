"""ISOLATION EXPERIMENT: which part of the refined SBI prompt costs the dropped row?

Statement 1707857175 prints 71 transaction rows (PDF-derived, see pdf_rowtruth.py). The
client's short generic prompt emits 71. Both refined prompts emit 70, reproducibly. This
script decides WHY by re-running that ONE statement under prompt variants that differ
from the current refined prompt by exactly one excision or one addition.

VARIANTS
  base        the current refined prompt, unmodified                 (= arm D, control)
  nodate      minus the statement-period / transaction-date sanity-check rules
  noband      minus the leading-band + "TRANSACTIONS FOR <NAME>" rules
  norewards   minus the whole REWARDS_RULES block
              -- THIS IS THE LENGTH PROBE. Nothing in REWARDS_RULES can legitimately
              affect whether a transaction ROW is emitted, so if deleting it restores the
              row, the mechanism is instruction-bulk dilution rather than any specific
              rule, and a targeted rule cannot be trusted to fix it.
  fix         base + the new row-for-row completeness rule           (candidate fix)

TRANSPORT IS NOT REINVENTED. runner.py is imported and used UNCHANGED: its build_payload,
run_one, corpus and the gt298_lib invoke/backoff/atomic-write path all apply. The only
thing injected is the prompt text, registered into runner.PROMPTS so run_one's
rec["prompt_path"] lookup resolves. Concurrency stays at runner's cap of 2.

Every variant is run N times (default 3) because the signal is ONE row and the control's
own repeats were 70/69/70. A single sample cannot separate a fix from run variance.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner as R      # noqa: E402  -- used UNCHANGED

SID = "1707857175"

# ---------------------------------------------------------------- excision fragments
# Each is a VERBATIM slice of SBI_PROMPT.txt. remove() asserts it was present, so a
# prompt edit that invalidates a fragment fails loudly instead of silently ablating
# nothing and producing a meaningless "no effect" result.

FRAG_DATE_1 = """- SBI prints the sentence "for Statement Period: <start> to <end>" above the transaction
  table. That period is NOT an output field; use it only as an internal reference for the
  transaction-date sanity check below.
"""

FRAG_DATE_2 = """  - Sanity check before output: a transaction date whose day/month reading is ambiguous
    should be checked against the statement period and swapped if that resolves it.
    THIS CHECK MAY ONLY CORRECT A DATE — IT MUST NEVER DELETE A TRANSACTION. If a printed
    row's date still falls outside the statement period after checking, keep the row and
    report the date exactly as printed. Statement-level rows in the leading band, and
    late-posted transactions, legitimately fall outside the printed period.
"""

FRAG_BAND = """- The transaction table on page 1 begins with a SHORT LEADING BAND of statement-level
  rows (typically "PAYMENT RECEIVED ..." and "FUEL SURCHARGE WAIVER EXCL TAX") printed
  ABOVE the "TRANSACTIONS FOR <CARDHOLDER NAME>" header. Those leading rows ARE
  transactions and MUST be extracted. They are frequently dated LATER than the rows
  that follow them, so the table is NOT in date order.
- "TRANSACTIONS FOR <NAME>" is a section header, not a transaction, and the cardholder
  name in it is NOT part of any transaction description.
"""

REWARDS_START = "REWARDS_RULES (AUTHORITATIVE):"
REWARDS_END = "MISSING_DATA_RULE:"

# The candidate fix. Positive, SBI-scoped, and sized to the three failure modes actually
# measured on this statement: a dropped duplicate, a dropped neighbour, and an amount
# carried across a row boundary. It is inserted immediately after the existing
# COMPLETENESS bullet, which is where a reader would look for it.
FIX_ANCHOR = """  EXCLUDE only things that are not transaction rows: the ACCOUNT SUMMARY figures, the
  "Previous Balance / Payments / Purchases" arithmetic strip, the reward-point summary
  strip, and marketing or terms-and-conditions text.
"""

FIX_TEXT = """- ONE OUTPUT ROW PER PRINTED ROW, INCLUDING REPEATS. These statements print long runs
  of small UPI payments in which the same payee recurs for the same amount on the same
  date many times over. Two or more CONSECUTIVE rows that are identical in date, amount
  and description are SEPARATE genuine payments, not one row printed twice: emit every
  one of them and never merge or de-duplicate them. This holds when the repeat spans a
  PAGE BREAK, i.e. the last row of one page and the first row of the next are identical
  — that is two printed rows and both MUST be emitted. Keep each amount bound to the
  description printed on ITS OWN line; never carry an amount up or down from a
  neighbouring row. Count the printed transaction rows and emit exactly that many.
"""


def remove(text, frag, label):
    if frag not in text:
        raise SystemExit(f"ABLATION FRAGMENT NOT FOUND ({label}) -- prompt has changed; "
                         f"update ablate_rowcount.py rather than trusting a null result")
    return text.replace(frag, "", 1)


def insert_after(text, anchor, addition, label):
    if anchor not in text:
        raise SystemExit(f"INSERT ANCHOR NOT FOUND ({label})")
    return text.replace(anchor, anchor + addition, 1)


def build_variants():
    base = open(os.path.join(R.SBI_DIR, "SBI_PROMPT.txt"), encoding="utf-8").read()
    v = {"base": base}

    t = remove(base, FRAG_DATE_1, "date-1")
    v["nodate"] = remove(t, FRAG_DATE_2, "date-2")

    v["noband"] = remove(base, FRAG_BAND, "leading-band")

    i, j = base.find(REWARDS_START), base.find(REWARDS_END)
    if i < 0 or j < 0 or j <= i:
        raise SystemExit("REWARDS_RULES block not locatable")
    v["norewards"] = base[:i] + base[j:]

    v["fix"] = insert_after(base, FIX_ANCHOR, FIX_TEXT, "fix")

    for name, txt in v.items():
        assert txt.strip(), name
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="base,nodate,noband,norewards,fix")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=2)
    a = ap.parse_args()

    variants = build_variants()
    want = [w.strip() for w in a.variants.split(",") if w.strip()]
    for w in want:
        if w not in variants:
            raise SystemExit(f"unknown variant {w}")

    sid, fn, path = [t for t in R.corpus() if t[0] == SID][0]

    # Register each variant so run_one's PROMPTS[arm] lookup resolves. Injecting DATA
    # into the runner module keeps runner.py itself untouched.
    jobs = []
    manifest = {}
    for w in want:
        txt = variants[w]
        sha = hashlib.sha256(txt.encode()).hexdigest()
        manifest[w] = {"sha256": sha, "chars": len(txt), "lines": txt.count("\n") + 1}
        for rep in range(1, a.reps + 1):
            arm = f"E_{w}_r{rep}"
            R.PROMPTS[arm] = f"<ablation:{w}>"
            jobs.append((arm, txt, sha, w, rep))

    print(json.dumps(manifest, indent=1))
    log = os.path.join(HERE, "logs", "ablate_rowcount.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)

    def emit(m):
        line = f"{time.strftime('%H:%M:%S')} {m}"
        open(log, "a").write(line + "\n")
        print(line, flush=True)

    def work(j):
        arm, txt, sha, w, rep = j
        try:
            rec, cached = R.run_one(arm, sid, fn, path, txt, sha)
        except Exception as e:
            emit(f"EXC {arm} {type(e).__name__}: {e}")
            return None
        u = rec.get("usage_raw") or {}
        emit(f"{w:>10s} rep{rep} n={rec.get('n_transactions')} "
             f"fr={rec.get('finish_reason')} outcome={rec.get('outcome')} "
             f"pt={u.get('prompt_tokens')} ct={u.get('completion_tokens')} "
             f"{'CACHED' if cached else ''}")
        return {"variant": w, "rep": rep, "arm": arm,
                "n": rec.get("n_transactions"), "outcome": rec.get("outcome"),
                "finish_reason": rec.get("finish_reason"),
                "failure_class": rec.get("failure_class"), "usage": u}

    with ThreadPoolExecutor(max_workers=min(a.concurrency, 2)) as ex:
        res = [r for r in ex.map(work, jobs) if r]

    dest = os.path.join(HERE, "ablate_rowcount.json")
    json.dump({"sid": SID, "pdf_printed_rows": 71,
               "manifest": manifest, "results": res}, open(dest, "w"), indent=1)
    emit(f"wrote {dest}")
    for w in want:
        ns = [r["n"] for r in res if r["variant"] == w]
        emit(f"SUMMARY {w:>10s} n={ns}")


if __name__ == "__main__":
    main()
