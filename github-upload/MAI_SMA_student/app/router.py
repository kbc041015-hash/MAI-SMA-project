# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/router.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · route_rule(규칙 라우터): 바꾸지 않았다
#   · route_llm: config.json 의 실행기(llm_client)로 보낸다
#   · 평가셋: 벤치 폴더 대신 eval/questions(집계형 100문항 × 4문형 + 절차형 100문항 × 한·영), 결과는 logs/
#   · 기본 모드: all 대신 rule(LLM 호출 없음)
"""질의 라우터 — 들어온 질문을 집계형(SQL)과 절차형(RAG) 중 어디로 보낼지 정한다.
   세 가지 방식을 같은 평가셋으로 비교한다.
     rule  : 규칙 기반. LLM 없음. 0초.
     llm   : 로컬 모델 분류.
     hybrid: 규칙이 확신하면 규칙, 애매하면 모델.
   사용(패키지 폴더에서): python app/router.py [rule|llm|hybrid|all]
   학생 이식판: 평가셋은 eval/questions 의 200문항(DB 의 qa_aggregate · qa_procedural 과 같다),
   llm 분류는 config.json 의 실행기.
"""
import sys, os, json, re, time, urllib.request



def _bench():
    """결과를 남길 폴더. 학생 이식판: 패키지의 logs/ (원 시연판은 벤치 폴더)."""
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(d, exist_ok=True)
    return d

# ── 규칙 기반 ─────────────────────────────────────────────
# 집계형 신호: 수량·비교·집합·목록을 묻는 표현
AGG = [r'몇\s*(건|개|명|시간|번|척|종류|퍼센트|가지|%)', r'개수', r'건수', r'행\s*수',
       r'수는\s*\??$', r'수를?\s*(보여|알려|세)', r'총\s*\w*\s*(수|합)',
       r'평균', r'합계', r'합은', r'최대', r'최소', r'상위', r'순위', r'비율', r'통계',
       r'(가장|제일)\s*(많|적|긴|짧|오래|최근|자주|높|낮)', r'많은\s*순', r'많이\s*(쓴|한|담당)',
       r'별로?\s*(세|보여|집계|나눠)', r'별\s*(건수|개수|수|문서)', r'세어\s*(줘|서)',
       r'목록', r'나열', r'보여줘', r'서로\s*다른', r'언제(야|인가)', r'누구인가',
       r'\b(count|how many|how much|average|total|sum|top\s*\d|list)\b',
       # 영어 집계 신호 보강. 화면의 추천 질문을 영어로 바꾸면서 "what
       # percentage …" 같은 꼴이 어디에도 걸리지 않아 절차형으로 샜다.
       # 한국어 질문에는 나타나지 않는 낱말이라 기존 판정은 그대로다.
       r'\b(what\s+(percentage|percent|proportion|fraction|share))\b',
       r'\b(per\s+(vessel|ship|unit|month|year|department)|by\s+(vessel|unit|type|rank|month|year))\b',
       r'\b(distinct|breakdown|group(ed)?\s+by|show\s+the\s+\w+\s+count)\b']
# 절차형 신호: 방법·기준·소견을 묻는 표현
PROC = [r'어떻게', r'어떤\s*(순서|절차|방법|점)', r'절차', r'하는\s*법', r'어느\s*정도로',
        r'무엇을\s*(확인|점검|주의|봐)', r'뭘\s*(확인|봐|점검|주의)', r'확인해야', r'봐야',
        r'점검\s*(항목|사항|할)', r'주의\s*(할|사항|점)',
        r'기준(값|치)', r'규정값', r'얼마\s*(이상|이하|여야|나\s*되어야)', r'정상\s*범위',
        r'왜\s*(그런|발생|이런)', r'원인', r'조치', r'대처', r'한계가',
        r'\b(how (do|to|is|are)|what should|procedure|steps?|check(list)?|criteria)\b']

Q_AGG = re.compile('|'.join(AGG), re.I)
Q_PROC = re.compile('|'.join(PROC), re.I)


def route_rule(q):
    a, p = len(Q_AGG.findall(q)), len(Q_PROC.findall(q))
    if a and not p:
        return 'AGG', 1.0
    if p and not a:
        return 'PROC', 1.0
    if a > p:
        return 'AGG', 0.5
    if p > a:
        return 'PROC', 0.5
    return 'PROC', 0.0          # 둘 다 없거나 동점 → 확신 없음


PROMPT = """선박 정비 데이터 질의 시스템의 라우터다. 질문을 두 갈래 중 하나로 보낸다.

AGG  = 데이터베이스를 집계해서 숫자·목록으로 답할 질문.
       (몇 건인가, 평균이 얼마인가, 배별로 세어줘, 가장 많은 것은, 목록을 보여줘)
PROC = 과거 정비 기록 본문을 찾아 읽고 절차·기준·소견으로 답할 질문.
       (어떻게 하나, 무엇을 점검하나, 기준값이 얼마인가, 어떤 순서로 하나, 왜 그런가)

한 단어만 출력한다: AGG 또는 PROC

질문: {q}
답:"""


def route_llm(q):
    """로컬 모델 분류. 학생 이식판: config.json 의 실행기로 보낸다(llm_client)."""
    import llm_client
    t = time.time()
    out, _ = llm_client.ask(PROMPT.format(q=q), mx=6)
    lab = 'AGG' if 'AGG' in out.upper() else ('PROC' if 'PROC' in out.upper() else '?')
    return lab, time.time() - t


def route_hybrid(q):
    lab, conf = route_rule(q)
    if conf >= 1.0:
        return lab, 0.0
    return route_llm(q)


def build_eval():
    """평가셋. 학생 이식판: eval/questions 의 200문항을 쓴다.
       집계형 100문항 × 문형 4(orig·L2·L1·NL) → AGG, 절차형 100문항 × 한·영 → PROC."""
    qd = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'eval', 'questions')
    ev = []
    with open(os.path.join(qd, 'aggregate100.jsonl'), encoding='utf-8') as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                for form, q in r['questions'].items():
                    ev.append((q, 'AGG', f'집계 {form}'))
    with open(os.path.join(qd, 'procedural100.jsonl'), encoding='utf-8') as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                ev.append((r['question_ko'], 'PROC', '절차 ko'))
                ev.append((r['question_en'], 'PROC', '절차 en'))
    return ev


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    mode = sys.argv[1] if len(sys.argv) > 1 else 'rule'
    ev = build_eval()
    n_agg = sum(1 for _, g, _ in ev if g == 'AGG')
    print(f'평가셋 {len(ev)}문항 (집계형 {n_agg} / 절차형 {len(ev)-n_agg})', flush=True)
    modes = ['rule', 'llm', 'hybrid'] if mode == 'all' else [mode]
    fn = {'rule': lambda q: route_rule(q)[0], 'llm': lambda q: route_llm(q)[0],
          'hybrid': lambda q: route_hybrid(q)[0]}
    res = {}
    for m in modes:
        t0 = time.time()
        rows = []
        for q, gold, src in ev:
            pred = fn[m](q)
            rows.append((q, gold, pred, src))
        ok = sum(1 for _, g, p, _ in rows if g == p)
        # 절차형을 집계형으로 잘못 보내면 SQL이 헛돌고, 반대는 검색이 헛돈다
        fp = sum(1 for _, g, p, _ in rows if g == 'PROC' and p == 'AGG')
        fn_ = sum(1 for _, g, p, _ in rows if g == 'AGG' and p == 'PROC')
        by = {}
        for _, g, p, s in rows:
            a, b = by.get(s, (0, 0))
            by[s] = (a + (g == p), b + 1)
        dt = time.time() - t0
        res[m] = rows
        print(f'\n[{m}] 정확도 {ok}/{len(ev)} ({ok/len(ev)*100:.1f}%) · '
              f'절차→집계 오배송 {fp} · 집계→절차 오배송 {fn_} · {dt:.1f}초 '
              f'(문항당 {dt/len(ev)*1000:.0f}ms)')
        print('   ' + ' · '.join(f'{k} {a}/{b}' for k, (a, b) in by.items()))
        wrong = [(q, g, p) for q, g, p, _ in rows if g != p]
        for q, g, p in wrong[:6]:
            print(f'     X [{g}→{p}] {q[:62]}')
        if len(wrong) > 6:
            print(f'     ... 외 {len(wrong)-6}건')
    json.dump({m: [dict(q=q, gold=g, pred=p, src=s) for q, g, p, s in rows]
               for m, rows in res.items()},
              open(f'{_bench()}/router_eval.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
