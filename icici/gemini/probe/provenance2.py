#!/usr/bin/env python3
"""Step 0 (corrected): luna_refined records are run ENVELOPES; compare parsed_json."""
import glob, os, re, json

REPO = "/Users/mayanck.bihani/Savesage/bank_eval/icici"
DLJ = "/Users/mayanck.bihani/Downloads/output/ICICI/JSON"

dl = {}
for f in glob.glob(DLJ + "/*.json"):
    dl[re.match(r"decrypt_(\d+)_", os.path.basename(f)).group(1)] = f
lr = {os.path.basename(f)[:-5]: f for f in glob.glob(REPO + "/luna_refined/json/*.json")}

same = diff = 0
print(f"{'id':<12} {'equal?':<7} {'arm':<14} {'outcome':<9} {'finish':<8} promptsha256[:12]")
shas = set()
for i in sorted(dl):
    a = json.load(open(dl[i]))
    env = json.load(open(lr[i]))
    pj = env.get("parsed_json")
    eq = a == pj
    same += eq
    diff += not eq
    shas.add(env.get("prompt_sha256"))
    print(
        f"{i:<12} {str(eq):<7} {str(env.get('arm')):<14} {str(env.get('outcome')):<9} "
        f"{str(env.get('finish_reason')):<8} {str(env.get('prompt_sha256'))[:12]}"
    )
print(f"\nparsed_json equal to Downloads JSON: {same}/{len(dl)}  differs={diff}")
print("distinct prompt_sha256 in luna_refined:", shas)

# sha256 of our committed prompt, for direct attribution
import hashlib

for p in [REPO + "/ICICI_PROMPT.txt", "/Users/mayanck.bihani/Downloads/ICICI_PROMPT.txt",
          REPO + "/GENERIC_PROMPT.txt"]:
    b = open(p, "rb").read()
    print(f"sha256 {hashlib.sha256(b).hexdigest()[:16]}  md5 {hashlib.md5(b).hexdigest()[:16]}  {p}")
