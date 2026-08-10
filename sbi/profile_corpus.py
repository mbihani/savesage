"""Local PDF profiling to drive a structurally-DIVERSE Phase-1 sample. No API calls."""
import csv, fitz, hashlib, json, os, re, statistics
csv.field_size_limit(10**9)
PDF_DIR = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi-pdfs"
CSVP = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi.csv"

rows = list(csv.DictReader(open(CSVP, newline='', encoding='utf-8')))
bykey = {}
for r in rows:
    k = os.path.basename((r.get('link') or '').split('?')[0])
    if k: bykey[k] = r

out = []
for f in sorted(os.listdir(PDF_DIR)):
    if not f.lower().endswith('.pdf'): continue
    p = os.path.join(PDF_DIR, f)
    d = fitz.open(p)
    txt = "".join(d[i].get_text() for i in range(d.page_count))
    d.close()
    U = txt.upper()
    r = bykey.get(f)
    ntx = None
    if r:
        try: ntx = len(json.loads(r['data'])['transactions'])
        except Exception: ntx = -1
    # layout signature markers
    sig = "".join('1' if m in U else '0' for m in [
        'ACCOUNT SUMMARY','PLACE OF SUPPLY','FLEXIPAY','ENCASH','REWARD POINTS','CASHBACK',
        'MERCHANT EMI','TRANSACTIONS FOR','UPI-','FUEL SURCHARGE WAIVER','GST','FOREIGN',
        'AIR INDIA','CASHBACK SBI','SIMPLYCLICK','PHONEPE','TATA NEU','BPCL','IRCTC','ELITE',
        'PRIME','PULSE','MINIMUM AMOUNT DUE','AVAILABLE CREDIT LIMIT','UTILIS','UTILIZ',
        'VISA','MASTERCARD','RUPAY','AMERICAN EXPRESS','DINERS'])
    # product name guess
    prod = None
    if r:
        try: prod = (json.loads(r['data'])['cards'][0]['cardMeta'] or {}).get('cardDisplayName')
        except Exception: pass
    out.append(dict(pdf=f, pages=d.page_count if False else None, ntx_csv=ntx, sig=sig,
                    chars=len(txt), bytes=os.path.getsize(p), product=prod,
                    has_rp='REWARD POINTS' in U, has_cb='CASHBACK' in U,
                    has_flexi='FLEXIPAY' in U, has_fx=('FOREIGN' in U or 'USD' in U),
                    n_txn_for='TRANSACTIONS FOR' in U))
json.dump(out, open('corpus_profile.json','w'), indent=1)
from collections import Counter
print("n:", len(out))
print("distinct layout sigs:", len(Counter(o['sig'] for o in out)))
print("top sigs:", Counter(o['sig'] for o in out).most_common(8))
print("products:", Counter(o['product'] for o in out).most_common(20))
print("chars: mean %.0f median %.0f max %d" % (statistics.mean([o['chars'] for o in out]),
      statistics.median([o['chars'] for o in out]), max(o['chars'] for o in out)))
print("has_rp", sum(o['has_rp'] for o in out), "has_cb", sum(o['has_cb'] for o in out),
      "flexi", sum(o['has_flexi'] for o in out), "fx", sum(o['has_fx'] for o in out),
      "txn_for", sum(o['n_txn_for'] for o in out))
print("utilis printed:", sum(1 for o in out if o['sig'][25]=='1' or o['sig'][26]=='1'))
