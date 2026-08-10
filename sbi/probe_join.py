import csv, json, os, re, sys
csv.field_size_limit(10**9)
PDF_DIR="/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi-pdfs"
CSV="/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi.csv"
pdfs=sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
print("pdf files:",len(pdfs))
rows=list(csv.DictReader(open(CSV,newline='',encoding='utf-8')))
print("csv rows:",len(rows))
keys=[os.path.basename((r.get('link') or '').split('?')[0]) for r in rows]
from collections import Counter
ck=Counter(k for k in keys if k)
print("csv dup keys:",[k for k,v in ck.items() if v>1][:5], "n_dup:",sum(1 for v in ck.values() if v>1))
pset=set(pdfs)
matched=[k for k in keys if k in pset]
print("csv rows matching a pdf:",len(matched),"unique:",len(set(matched)))
print("csv rows with no pdf:",len([k for k in keys if k not in pset]))
print("pdfs with no csv row:",len(pset-set(keys)))
for k in sorted(pset-set(keys)): print("  PDF_NO_CSV",k)
unm=[k for k in keys if k not in pset]
for k in unm[:20]: print("  CSV_NO_PDF",k)
# id extraction
ID=re.compile(r"^decrypt_(?:encrypt_)?(\d+)_")
bad=[f for f in pdfs if not ID.match(f)]
print("pdfs failing id regex:",bad)
ids=[ID.match(f).group(1) for f in pdfs if ID.match(f)]
print("unique ids:",len(set(ids)))
# data blob size + txn counts
sizes=[]
for r in rows:
    k=os.path.basename((r.get('link') or '').split('?')[0])
    if k not in pset: continue
    d=r.get('data') or ''
    try: j=json.loads(d); n=len(j.get('transactions') or [])
    except Exception: n=-1
    sizes.append((n,len(d),k))
sizes.sort(reverse=True)
print("txn counts: max",sizes[0][:2],"min",sizes[-1][:2])
import statistics
ns=[s[0] for s in sizes if s[0]>=0]
print("n_txn mean %.1f median %s max %d  |  data bytes mean %.0f"%(statistics.mean(ns),statistics.median(ns),max(ns),statistics.mean([s[1] for s in sizes])))
print("top10 by txn:",[(s[2][:28],s[0]) for s in sizes[:10]])
print("detectionSource:",Counter(r.get('detectionSource') for r in rows))
print("modelName:",Counter(r.get('modelName') for r in rows))
