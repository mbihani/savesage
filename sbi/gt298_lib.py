"""Shared plumbing for the 298-statement Axis ground-truth pass.

Ground truth = databricks-claude-opus-5 reading the ORIGINAL PDF natively (Anthropic
`document` content block, base64), NOT pymupdf4llm OCR text. One call per statement,
strict json_schema response_format.

This is a direct descendant of bakeoff/groundtruth/gt_lib.py (the validated 94-set
builder). Two deliberate changes, both forced by the field contract on
`feat/schema-and-prompts` (schema/fields.py):

  1. SCHEMA WIDENED from 27 leaves to the full 33-leaf contract. The prior GT was
     missing 6 keys the client contract requires, and `fields.py` marks all 6
     `scoreable=False` for exactly one reason: "The Opus-5 ground truth does not
     carry this key at all". Carrying them here is what makes them scoreable:
       statementMeta.statementPeriodStart / statementPeriodEnd / rawStatementId
       cards[].bigPicture.cardCreditLimit / cardAvailableCreditLimit
       rewards.bonusPointsThisCycle
     NOTE the contract's measurements predict rawStatementId and
     bonusPointsThisCycle are null corpus-wide (0/298 PDFs print them). We still
     ASK for them — a measured null from the PDF is a real label; an absent key is
     not. The prompt is explicit that null is correct, so this does not invite
     invention.

  2. `utilisationPercent` is NOT requested from the model. It is not printed in any
     of the 298 PDFs (0 matches for 'utilis|utiliz'), so it is arithmetic, not
     extraction, and is computed in code by add_utilisation298.py under the same
     formula the contract pins: round(totalAmountDue / totalCreditLimit * 100, 2).

Rate limiting: the binding workspace ceiling is OUTPUT TOKENS PER MINUTE, not QPS.
A 429 means the whole workspace minute is spent, so backoff must clear the window.

IP ACL: this machine's egress IP rotates. A 403 whose body names the IP ACL is
INFRASTRUCTURE, not a model failure, and is surfaced as its own outcome class so a
doomed run aborts early instead of burning 298 calls.

stdlib only (urllib.request) — pypi is blackholed on this machine.
"""

import base64
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- constants

WORKSPACE = "https://fevm-stable-classic-7ppxjq.cloud.databricks.com"
PROFILE = "fevm-stable"
DATABRICKS_CLI = "/usr/local/bin/databricks"  # pin v0.255.0; the v0.18 on PATH is wrong
GT_MODEL = "databricks-claude-opus-5"

PDF_ROOT = "/Users/mayanck.bihani/Downloads/statement_pdfs"
OUT_ROOT = os.path.dirname(os.path.abspath(__file__))

MAX_TOKENS = 32000  # 94-set worst case was 115 txn rows; Opus 5 cap is 128k

# Opus 5 list price (per 1M tokens). Used only for reporting a cost total.
PRICE_IN_PER_M = 5.00
PRICE_OUT_PER_M = 25.00

# ---------------------------------------------------------------- auth
# OAuth token TTL ~1h. A 298-statement batch outlives it many times over. Refresh
# proactively every 20 min AND reactively on 401/403.

_tok_lock = threading.Lock()
_tok = {"value": None, "minted": 0.0}
_TOKEN_MAX_AGE = 20 * 60


def _mint_token():
    # v0.255.0 prints a 6-line banner to stderr — parse stdout ONLY.
    p = subprocess.run(
        [DATABRICKS_CLI, "auth", "token", "-p", PROFILE],
        capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        raise RuntimeError(f"databricks auth token failed rc={p.returncode}: {p.stderr[-500:]}")
    return json.loads(p.stdout)["access_token"]


def get_token(force=False):
    with _tok_lock:
        age = time.time() - _tok["minted"]
        if force or _tok["value"] is None or age > _TOKEN_MAX_AGE:
            _tok["value"] = _mint_token()
            _tok["minted"] = time.time()
        return _tok["value"]


# ---------------------------------------------------------------- corpus
# Filenames are `decrypt_<statementId>_<hash>_Credit Card Statement.pdf`, EXCEPT one
# file carrying a doubled prefix: `decrypt_encrypt_709049979_...`. Matching only
# `decrypt_(\d+)_` silently drops that statement (298 files -> 297 ids), so the
# optional `encrypt_` group is load-bearing, not defensive.
#
# The id convention is verified against the live Delta table: savesage.predictions
# .statement_id holds the bare numeric string (e.g. '1049684717'), so these ids JOIN
# directly with no zero-padding or prefix.
_ID_RE = re.compile(r"^decrypt_(?:encrypt_)?(\d+)_")


def discover_pdfs():
    """-> list of (statement_id, filename, pdf_path), sorted by numeric id. 298 expected."""
    out, odd = [], []
    for f in sorted(os.listdir(PDF_ROOT)):
        if not f.lower().endswith(".pdf"):
            continue
        m = _ID_RE.match(f)
        if not m:
            odd.append(f)
            continue
        out.append((m.group(1), f, os.path.join(PDF_ROOT, f)))
    if odd:
        raise RuntimeError(f"{len(odd)} PDF(s) do not match the id convention: {odd}")
    ids = [t[0] for t in out]
    if len(set(ids)) != len(ids):
        from collections import Counter
        dup = [k for k, v in Counter(ids).items() if v > 1]
        raise RuntimeError(f"duplicate statement ids among PDFs: {dup}")
    out.sort(key=lambda t: int(t[0]))
    return out


# ---------------------------------------------------------------- schema

def _s(t):
    return {"type": [t, "null"]}


TXN_TYPES = ["PURCHASE", "PAYMENT", "REFUND", "REVERSAL", "CASHBACK", "FEE",
             "TAX", "INTEREST", "EMI", "CASH_ADVANCE", "UPI"]

GT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statementMeta", "statementLevelSummary", "cards", "transactions", "rewards"],
    "properties": {
        "statementMeta": {
            "type": "object", "additionalProperties": False,
            "required": ["issuerName", "statementDate", "dueDate",
                         "statementPeriodStart", "statementPeriodEnd", "rawStatementId"],
            "properties": {
                "issuerName": _s("string"),
                "statementDate": _s("string"),
                "dueDate": _s("string"),
                "statementPeriodStart": _s("string"),
                "statementPeriodEnd": _s("string"),
                "rawStatementId": _s("string"),
            },
        },
        "statementLevelSummary": {
            "type": "object", "additionalProperties": False,
            "required": ["totalAmountDue", "totalMinimumAmountDue",
                         "totalCreditLimit", "availableCreditLimit"],
            "properties": {
                "totalAmountDue": _s("number"),
                "totalMinimumAmountDue": _s("number"),
                "totalCreditLimit": _s("number"),
                "availableCreditLimit": _s("number"),
            },
        },
        "cards": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["cardMeta", "bigPicture"],
                "properties": {
                    "cardMeta": {
                        "type": "object", "additionalProperties": False,
                        "required": ["cardDisplayName", "productFamily", "lastFourDigit",
                                     "network", "isPrimaryCard"],
                        "properties": {
                            "cardDisplayName": _s("string"),
                            "productFamily": _s("string"),
                            "lastFourDigit": _s("string"),
                            "network": _s("string"),
                            "isPrimaryCard": _s("boolean"),
                        },
                    },
                    "bigPicture": {
                        "type": "object", "additionalProperties": False,
                        "required": ["cardCreditLimit", "cardAvailableCreditLimit"],
                        "properties": {
                            "cardCreditLimit": _s("number"),
                            "cardAvailableCreditLimit": _s("number"),
                        },
                    },
                },
            },
        },
        "transactions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["date", "description", "amount", "direction", "txnType",
                             "rewardPointsOnThisTransaction", "currency"],
                "properties": {
                    "date": _s("string"),
                    "description": _s("string"),
                    "amount": _s("number"),
                    "direction": {"type": ["string", "null"], "enum": ["DEBIT", "CREDIT", None]},
                    "txnType": {"type": ["string", "null"], "enum": TXN_TYPES + [None]},
                    "rewardPointsOnThisTransaction": _s("number"),
                    "currency": _s("string"),
                },
            },
        },
        "rewards": {
            "type": "object", "additionalProperties": False,
            "required": ["programType", "openingPoints", "pointsEarnedThisCycle",
                         "pointsRedeemedThisCycle", "closingPoints",
                         "pointsExpiringNext30Days", "pointsExpiringNext60Days",
                         "bonusPointsThisCycle"],
            "properties": {
                "programType": _s("string"),
                "openingPoints": _s("number"),
                "pointsEarnedThisCycle": _s("number"),
                "pointsRedeemedThisCycle": _s("number"),
                "closingPoints": _s("number"),
                "pointsExpiringNext30Days": _s("number"),
                "pointsExpiringNext60Days": _s("number"),
                "bonusPointsThisCycle": _s("number"),
            },
        },
    },
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "credit_card_statement", "strict": True, "schema": GT_SCHEMA},
}

# ---------------------------------------------------------------- prompt
# Rules 1-14 are VERBATIM from the validated 94-set prompt so the two GT sets are
# directly comparable. Rules 15-17 cover only the 6 newly-requested keys.

GT_PROMPT = """You are producing the GROUND-TRUTH structured extraction for an Indian credit-card statement. The attached PDF is the ONLY authoritative source — read it directly, including its table layout. Return one JSON object matching the schema exactly. Accuracy matters more than speed; this output will be used to score other models.

RULES

1. issuerName — the ISSUING BANK, i.e. the bank whose licence the card is issued on. For this corpus that is "Axis Bank". Do NOT return the co-brand partner or the card product name. A card branded "NEO", "Flipkart", "Airtel", "IndianOil", "Magnus", "MY ZONE", "ACE", "SpiceJet", "Vistara", "Privilege", "SELECT", "Rewards" etc. is still ISSUED BY Axis Bank — return "Axis Bank". Only return a different bank if that other bank's name appears as the issuer throughout the statement (letterhead, footer, regulatory text), not merely as a co-brand or a payee.

2. Dates — always DD/MM/YYYY, zero-padded, 4-digit year. Convert any other printed format. statementDate = the statement generation date. dueDate = the payment due date.

3. Amounts — plain JSON numbers. No currency symbol, no thousands separators. Never a string.

4. transactions[].amount — ALWAYS POSITIVE (the magnitude). NEVER negate credits, refunds, reversals or payments. Sign information lives only in `direction`. Example: printed "1,531.00 Cr" -> amount 1531.0, direction "CREDIT". Printed "200.00 Dr" -> amount 200.0, direction "DEBIT".

5. transactions[].direction — exactly "DEBIT" or "CREDIT". On Axis statements the suffix "Dr" means DEBIT and "Cr" means CREDIT; trust that suffix over your own reading of the description. If a row carries no Dr/Cr marker, use DEBIT for spend/fees/taxes/interest/EMI and CREDIT for payments received, refunds, reversals and cashback.

6. transactions[].txnType — exactly ONE value from this closed list, or null if genuinely unclear:
   PURCHASE, PAYMENT, REFUND, REVERSAL, CASHBACK, FEE, TAX, INTEREST, EMI, CASH_ADVANCE, UPI
   Mapping guidance:
   - description begins "UPI/" or is plainly a UPI/VPA transfer -> UPI
   - "PAYMENT RECEIVED", "BBPS PAYMENT", "NEFT", "IMPS", "AUTO DEBIT" repayment of the card -> PAYMENT
   - "GST", "IGST", "CGST", "SGST", "SERVICE TAX" -> TAX
   - any "...FEE", "CHARGES", "ANNUAL FEE", "JOINING FEE", "LATE PAYMENT", "OVERLIMIT", "SURCHARGE", "MARKUP" -> FEE
   - "INTEREST", "FINANCE CHARGE" -> INTEREST
   - EMI conversion / EMI instalment / EMI principal or interest lines -> EMI
   - "CASH WITHDRAWAL", "ATM", cash advance -> CASH_ADVANCE
   - explicit reversal wording -> REVERSAL ; explicit refund/chargeback wording -> REFUND
   - cashback credited -> CASHBACK
   - ordinary card spend at a merchant -> PURCHASE
   Note: an EMI processing fee is FEE, not EMI. GST on a fee is TAX, not FEE.

7. transactions[].description — copy the narration EXACTLY as printed, character for character: same case, same commas, slashes, hyphens, "REF#" digits, and the statement's own truncation (Axis truncates long UPI narrations mid-word — keep it truncated exactly as shown). You may trim leading/trailing whitespace, nothing else. NEVER shorten, expand, normalise, re-case, translate or summarise. Do NOT append the MERCHANT CATEGORY column value.

8. transactions — include EVERY row of EVERY transaction table on EVERY page, for ALL cards, in printed order. That includes fees, taxes, interest, EMI lines, payments received, reversals and reward-related debits. EXCLUDE anything that is not a transaction row: the "Previous Balance - Payments - Credits + Purchase ..." arithmetic strip, Account Summary / Payment Summary figures, opening/closing balances, reward-point summary tables, EMI amortisation schedules that merely forecast future instalments, and marketing/offer text. If the statement genuinely lists no transactions, return [].

9. transactions[].currency — "INR" for rupee amounts (the default). Only use another ISO 4217 code if the amount you are reporting is itself in that foreign currency. If the statement shows a foreign spend converted to rupees, report the rupee amount with "INR".

10. transactions[].rewardPointsOnThisTransaction — null unless the statement prints reward points against that individual transaction row. Do not compute or estimate.

11. cards — one entry per card account on the statement (primary plus any add-on/supplementary cards). lastFourDigit = last four digits only ("653047******9826" -> "9826"). cardDisplayName and productFamily = the card product name as printed, e.g. "NEO CREDIT CARD", "MY ZONE", "FLIPKART AXIS BANK CREDIT CARD"; if only one name is printed use it for both. network = "VISA", "MASTERCARD", "RUPAY", "AMEX" or "DINERS" only when the statement actually shows the network name or logo label; otherwise null — do NOT infer it from the card BIN. isPrimaryCard = true if the statement has exactly one card, or for the primary/main cardholder's card; false for a clearly-marked add-on/supplementary card; null only if the statement genuinely does not distinguish.

12. statementLevelSummary — the whole-statement (all cards combined) figures from the PAYMENT SUMMARY / Account Summary block:
    - totalAmountDue = "Total Payment Due"
    - totalMinimumAmountDue = "Minimum Payment Due"
    - totalCreditLimit = "Credit Limit" (the total sanctioned limit, NOT the cash limit)
    - availableCreditLimit = "Available Credit Limit" (NOT "Available Cash Limit")
    Report a Dr (customer owes) figure as a POSITIVE number. Report a Cr figure (credit balance, bank owes the customer) as a NEGATIVE number.

13. rewards — from the reward-points summary block only. programType = the program name exactly as printed (e.g. "eDGE REWARD POINTS", "EDGE REWARDS"). openingPoints, pointsEarnedThisCycle, pointsRedeemedThisCycle, closingPoints, pointsExpiringNext30Days, pointsExpiringNext60Days as printed. These are POINT COUNTS, not rupees. If the statement prints only one total/closing/available reward-point figure, that figure is closingPoints and the rest are null. Never derive a missing field by arithmetic.

14. NEVER FABRICATE. Any value not genuinely present in the PDF must be null (or [] for a list). Do not guess, do not infer from convention, do not copy across cards, do not compute totals. A null is correct; an invented value is a scoring error.

15. statementMeta.statementPeriodStart / statementPeriodEnd — from the "Statement Period" cell, normally printed as a single range "DD/MM/YYYY - DD/MM/YYYY". statementPeriodStart is the LEFT date, statementPeriodEnd is the RIGHT date. Output each in DD/MM/YYYY exactly as rule 2 requires. If only one date is printed, or the label is absent, set the missing side to null rather than guessing it from the statement date or the cycle length.

16. statementMeta.rawStatementId — the statement's OWN printed identifier, i.e. a value printed against a label such as "Statement No", "Statement Number", "Statement ID", "Invoice No" or "Document No". This is almost certainly NOT present on an Axis statement: if no such label appears, the correct answer is null. Do NOT repurpose the card number, the customer/account number, a reference number from a transaction row, the cycle dates, or a filename. A value here that is not printed against one of those labels is a fabrication.

17. Per-card limits and bonus points — read only, never derive:
    - cards[].bigPicture.cardCreditLimit = the credit limit applying to THAT card, and cards[].bigPicture.cardAvailableCreditLimit = the available credit limit applying to THAT card. Axis normally prints ONE limit for the whole statement rather than one per card. When a single shared limit is printed and the statement has one card, that figure is this card's limit. When the statement has several cards sharing one printed limit, put the figure on the PRIMARY card and leave the add-on cards null. NEVER split, apportion or divide a shared limit between cards, and never use the "Available Cash Limit".
    - rewards.bonusPointsThisCycle = a separately-labelled BONUS points figure (e.g. "Bonus Points"). If the statement prints no separate bonus line, the answer is null. Do NOT reclassify total earned points, closing points, or a promotional-offer mention as bonus points.

Return only the JSON object."""

# ---------------------------------------------------------------- invoke

_RETRYABLE = {408, 429, 500, 502, 503, 504}

# The real FMAPI ceiling on this workspace is an OUTPUT-TOKENS-PER-MINUTE limit, not a
# QPS one. A 429 therefore means "the whole workspace minute is spent" — a sub-minute
# retry is guaranteed to fail again, so back off past the window with jitter to avoid a
# thundering herd of workers all retrying together.
_RL_BACKOFF = [70, 95, 125, 160, 200, 240, 300, 300, 300, 300]

IP_ACL_MARKERS = ("blocked by databricks ip acl", "ip acl", "ip access list")


def is_ip_acl(status, body):
    """A 403 naming the IP ACL is INFRASTRUCTURE (egress IP not allowlisted), not auth
    expiry and not a model failure. Distinguishing it matters because the response is
    to abort the run and allowlist the IP, whereas a plain 403 is fixed by re-minting
    the token."""
    if status != 403 or not body:
        return False
    b = str(body).lower()
    return any(m in b for m in IP_ACL_MARKERS)


def build_payload(pdf_b64, effort="medium", prompt=GT_PROMPT, response_format=RESPONSE_FORMAT,
                  max_tokens=MAX_TOKENS):
    """Identical request shape to the validated 94-set builder.

    `thinking: adaptive` + `output_config.effort` are the params that pass on Claude;
    `reasoning_effort` is a hard 400 on this endpoint. Opus 5 returned ZERO reasoning
    tokens at every effort level across 12 probe calls in the prior round, so effort is
    kept at the prior value rather than tuned — this is not a high-reasoning pass.
    """
    p = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }
    if response_format:
        p["response_format"] = response_format
    return p


def invoke(payload, model=GT_MODEL, max_attempts=12, timeout=900, on_ip_acl=None):
    """POST /serving-endpoints/<model>/invocations.

    Returns (obj, meta). meta = {http_status, attempts, latency_ms, error, rate_limited,
    ip_acl}. obj is None on failure. Refreshes the OAuth token on 401/403.

    `rate_limited` records that the call saw at least one 429 even if it eventually
    succeeded, so 429s can be reported as infrastructure pressure rather than being
    silently absorbed. `on_ip_acl` is a callback that may raise to abort the whole run.
    """
    url = f"{WORKSPACE}/serving-endpoints/{model}/invocations"
    body = json.dumps(payload).encode()
    t0 = time.time()
    last_err = None
    last_status = None
    rate_limited = 0
    ip_acl = False

    for attempt in range(1, max_attempts + 1):
        tok = get_token()
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
            return json.loads(raw), {
                "http_status": 200, "attempts": attempt,
                "latency_ms": int((time.time() - t0) * 1000), "error": None,
                "rate_limited": rate_limited, "ip_acl": ip_acl,
            }
        except urllib.error.HTTPError as e:
            last_status = e.code
            try:
                last_err = e.read().decode()[:2000]
            except Exception:
                last_err = str(e)

            if is_ip_acl(e.code, last_err):
                ip_acl = True
                if on_ip_acl is not None:
                    on_ip_acl()          # may raise AbortRun to kill the batch
                break                    # never retry a blocked IP — it will not clear

            if e.code in (401, 403):
                get_token(force=True)    # expired token -> re-mint, re-issue
                if attempt < max_attempts:
                    continue
            elif e.code == 429 and attempt < max_attempts:
                # output-TPM limit: must wait out the minute window, not milliseconds
                rate_limited += 1
                base = _RL_BACKOFF[min(attempt - 1, len(_RL_BACKOFF) - 1)]
                time.sleep(base + random.uniform(0, 30))
                continue
            elif e.code in _RETRYABLE and attempt < max_attempts:
                time.sleep(min(60, 2 ** attempt) + (attempt * 0.37))
                continue
            break                        # 400 etc. -> not retryable, fail loud
        except Exception as e:            # timeout / socket reset
            last_status = None
            last_err = f"{type(e).__name__}: {e}"
            if attempt < max_attempts:
                time.sleep(min(60, 2 ** attempt))
                continue
            break

    return None, {
        "http_status": last_status, "attempts": attempt,
        "latency_ms": int((time.time() - t0) * 1000), "error": last_err,
        "rate_limited": rate_limited, "ip_acl": ip_acl,
    }


class AbortRun(RuntimeError):
    """Raised to stop the batch early (consecutive IP-ACL 403s)."""


def extract_text(resp):
    """`content` may be a str OR a list of blocks (thinking/reasoning first)."""
    msg = resp["choices"][0]["message"]
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c
                       if isinstance(b, dict) and b.get("type") == "text")
    return "" if c is None else str(c)


def parse_json_strict(text):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


def usage_row(resp):
    u = (resp or {}).get("usage") or {}
    d = u.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": u.get("prompt_tokens"),
        "completion_tokens": u.get("completion_tokens"),
        "total_tokens": u.get("total_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
        "reasoning_tokens": u.get("reasoning_tokens", d.get("reasoning_tokens")),
    }


def cost_usd(prompt_tokens, completion_tokens):
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    return pt / 1e6 * PRICE_IN_PER_M + ct / 1e6 * PRICE_OUT_PER_M


def pdf_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def atomic_write_json(path, obj, indent=1, sort_keys=True):
    """Write via a temp file + os.replace so a crash or a kill mid-write can never
    leave a truncated JSON that a resumed run would treat as complete. Three runs
    died mid-flight in this project already."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent, sort_keys=sort_keys)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def utilisation_percent(total_amount_due, total_credit_limit):
    """Computed, not extracted — no Axis PDF prints a utilisation figure.

    Mirrors schema/fields.py::utilisation_percent exactly so GT and scorer agree.
    """
    if not isinstance(total_amount_due, (int, float)) or isinstance(total_amount_due, bool):
        return None
    if not isinstance(total_credit_limit, (int, float)) or isinstance(total_credit_limit, bool):
        return None
    if total_credit_limit == 0:
        return None
    return round(total_amount_due / total_credit_limit * 100, 2)
