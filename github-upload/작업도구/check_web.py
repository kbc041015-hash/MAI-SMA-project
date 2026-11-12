"""Exercise actual model-backed web routes and keep full responses locally."""
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
cases = [('aggregate', '주기관 정비를 호기별로 세어줘'),
         ('abstain', '정비 한 건에 평균 몇 명이나 투입돼?'),
         ('procedural', 'What torque is the large bowl lock nut tightened to when assembling the purifier?')]
results = []
for label, question in cases:
    req = urllib.request.Request('http://127.0.0.1:8780/api/ask',
                                 data=json.dumps({'q':question, 'rid':'actual-web-'+label}).encode('utf-8'),
                                 headers={'Content-Type':'application/json'})
    try:
        with opener.open(req, timeout=1800) as response:
            result = json.load(response)
        results.append({'case':label, 'result':result})
        print(json.dumps({'case':label, 'kind':result.get('kind'),
                          'rows':result.get('rows'), 'token':result.get('token'),
                          'steps':len(result.get('steps') or []), 'verified':result.get('verified')}, ensure_ascii=False), flush=True)
    except Exception as exc:
        results.append({'case':label, 'error':type(exc).__name__+': '+str(exc)})
        print(label, type(exc).__name__, str(exc), flush=True)
    path = ROOT / 'MAI_SMA_student/logs/actual_web_results.json'
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
