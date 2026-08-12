"""Persist compact fitz geometry for disputed directions and EMI badge columns."""
import glob, json, os, re, sys
import fitz
sys.path.insert(0, os.path.dirname(__file__))
import pdf_rows as P

HERE=os.path.dirname(__file__)
def main():
    recs={}
    for f in glob.glob(HERE+'/json_hdfc/*.json'):
        x=json.load(open(f)); recs[x['statement_id']]=x
    out={"direction":[],"emi_columns":[]}
    for _,fn,path in P.corpus():
        sid=P.statement_id(fn); ext=P.extract(path); model=recs[sid]['parsed_json']['transactions']
        if sid in ('567125239','1723515293'):
            for m,p in zip(model,ext['rows']):
                if m.get('direction') != p['direction']:
                    out['direction'].append({"statement":sid,"page":p['page'],"description":p['description'],
                        "model":m.get('direction'),"pdf":p['direction'],"plus":p['has_plus'],
                        "amount_color":p['amount_color'],"date_bbox":p['date_bbox'],"amount_bbox":p['amount_bbox']})
        if sid in ('567125239','853991354','1787504092','629227338'):
            doc=fitz.open(path)
            for page_no,page in enumerate(doc,1):
                spans=[s for b in page.get_text('dict').get('blocks',[]) for l in b.get('lines',[]) for s in l.get('spans',[])]
                for s in spans:
                    if re.fullmatch(r'\s*EMI\s*',s['text']) and 'Bold' in s['font']:
                        cy=(s['bbox'][1]+s['bbox'][3])/2
                        narr=[n for n in spans if abs((n['bbox'][1]+n['bbox'][3])/2-cy)<1.5
                              and n['bbox'][0]>s['bbox'][2] and 'Calibri' in n['font']]
                        if narr:
                            n=min(narr,key=lambda x:x['bbox'][0])
                            out['emi_columns'].append({"statement":sid,"page":page_no,"badge_bbox":[round(v,2) for v in s['bbox']],
                                "narration_sample":n['text'],"narration_bbox":[round(v,2) for v in n['bbox']]})
            doc.close()
    json.dump(out,open(HERE+'/geometry_adjudication.json','w'),indent=2,sort_keys=True)
if __name__=='__main__': main()
