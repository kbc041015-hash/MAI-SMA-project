# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/rag.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · LLM 호출(llm): 고정 주소 대신 config.json 의 실행기(llm_client)
#   · 색인: data/bm25_1000.pkl = 정비 문서 기록(master 서술 300자 이상) + 참고 기록(qa_proc_context, 본문 중복 제거)
#   · build_index(db, out) · index_rows(con) — 토큰화·가중·BM25 식은 원판 그대로. 참고 기록은 meta[0] = QA_REF
#   · DB 연결: harness.get_con 과 같은 외부 접근 차단 설정
#   · __main__: 실제 DB 를 쓰던 --eval 대신 --search(검색만)
"""선박 정비기록 질의 시스템 — 종단간(end-to-end). 사람이 넣는 검색어 없음.

  한국어 질문 → [로컬 LLM] 영어 번역 → [BM25] 기록 검색 → [로컬 LLM] 절차 생성
             → [문자열 대조] 인용 검증 → 근거 있는 문장만 출력

외부 서비스 없음. 인터넷 없음. 모델은 config.json 에 적은 이 PC(또는 사내망)의 LLM 실행기
(Ollama · LM Studio · llama.cpp 등 OpenAI 호환 서버).

사용(패키지 폴더에서. LLM 연결은 config.json):
  python app/rag.py "청정기 조립할 때 대형 락너트는 몇 토크로 조이나?"
  python app/rag.py --search "purifier lock nut torque"   검색만(LLM 없음)
옵션 환경변수: K(가져올 기록 수, 기본 4)
"""
import sys, os, json, re, math, time, pickle, urllib.request
from collections import Counter
import duckdb

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__)).replace(chr(92), '/')


def _pick(env, *names):
    """환경변수 > 스크립트와 같은 폴더 순으로 파일을 찾는다.
    포터블 패키지에서는 시연용 파일만 들어 있을 수 있다."""
    v = _os.environ.get(env)
    if v and _os.path.exists(v):
        return v
    for n in names:
        p = f'{_HERE}/{n}'
        if _os.path.exists(p):
            return p
    return f'{_HERE}/{names[0]}'


B = _HERE
K = int(os.environ.get('K', '4'))
IDX = _pick('BM25_IDX', '../data/bm25_1000.pkl')   # 학생 이식판: 학생용 DB 로 만든 색인(정비 문서 + 참고 기록)

STOP = set("""a an the of to in on for and or is are was were be been being do does did how what
which when where who why with from by at as it its this that these those i you we they my our
please tell me show us also can could should would will shall need want carry out procedure step
steps way ways done make made get got give given""".split())


# ───────────────────────── BM25 검색 (LLM 없음) ─────────────────────────
def tok(s):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9/\.\-]*", (s or '').lower())
            if w not in STOP and len(w) > 2]


REF = 'QA_REF'     # 학생 이식판: 색인에서 참고 기록(qa_proc_context)을 가리키는 표지(meta[0]). meta[1] = '<문항ID>-<기록번호>'


def _connect(db):
    """읽기 전용 + 외부 접근 차단. harness.get_con 과 같은 설정으로 연다(학생 이식판)."""
    try:
        con = duckdb.connect(db, read_only=True, config={'enable_external_access': False})
    except Exception:
        con = duckdb.connect(db, read_only=True)
    try:
        con.execute('SET lock_configuration = true')
    except Exception:
        pass
    return con


def index_rows(con):
    """색인에 넣을 기록 (VESSELCODE, DOC_NO, TITLE, MACH_DESC, EQUIP_DESC, WORK_DATE, WORK_REMARK) 목록.

    학생 이식판에서 나눠 쓴 함수다.
      ① 정비 문서 기록 — master 에서 서술 300자 이상(원 rag.py · make_demo_db.py 와 같은 대상)
      ② 참고 기록 — qa_proc_context(절차형 100문항의 고정 검색 문맥, 실제 정비기록 원문을 가명처리한 것).
         본문이 같은 기록은 하나만 넣고, 제목·기기·장비·날짜는 처음 나온 것(문항ID · 기록번호 순)을 쓴다.
         VESSELCODE 자리에 'QA_REF', DOC_NO 자리에 '<문항ID>-<기록번호>' 를 둔다. 작업일은 정비 문서 기록과
         같은 형(datetime)으로 바꾼다. qa_proc_context 가 없는 DB(원 키트 DB 등)면 ①만 넣는다.
    """
    import datetime as _dt
    rows = con.execute("""SELECT VESSELCODE, DOC_NO, TITLE, MACH_DESC, EQUIP_DESC,
                                 WORK_DATE, WORK_REMARK
                          FROM master WHERE length(WORK_REMARK) >= 300""").fetchall()
    have = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if 'qa_proc_context' not in have:
        return rows
    seen = set()
    for qid, no, title, mach, equip, day, body in con.execute(
            "SELECT qid, rec_no, title, machine, equipment, work_date, content "
            "FROM qa_proc_context ORDER BY qid, rec_no").fetchall():
        if not body or body in seen:
            continue
        seen.add(body)
        try:
            day = _dt.datetime.strptime(str(day)[:10], '%Y-%m-%d')
        except ValueError:
            pass
        rows.append((REF, f'{qid}-{no}', title, mach, equip, day, body))
    return rows


def build_index(db=None, out=None):
    """TITLE(가중 3배) + 본문으로 BM25 인덱스를 만든다. 한 번 만들고 파일로 재사용.

    학생 이식판: 학생용 DB 에서 정비 문서 기록과 참고 기록을 함께 색인한다(index_rows).
    토큰화·가중·BM25 식은 원 rag.py · make_demo_db.py 의 색인 생성과 같다.
    db·out 을 주지 않으면 data/ 의 기본 파일.
    """
    db = db or _pick('MAINT_DB', '../data/MAI_SMA_학생용DB_1000.duckdb')
    out = out or IDX
    con = _connect(db)
    rows = index_rows(con)
    con.close()
    docs, df = [], Counter()
    for r in rows:
        terms = tok(r[2]) * 3 + tok(r[6]) + tok(r[3]) + tok(r[4])
        tf = Counter(terms)
        docs.append(dict(meta=r, tf=tf, ln=len(terms)))
        df.update(tf.keys())
    N = len(docs)
    idf = {w: math.log(1 + (N - c + 0.5) / (c + 0.5)) for w, c in df.items()}
    avg = sum(d['ln'] for d in docs) / N
    idx = dict(docs=docs, idf=idf, avg=avg, N=N)
    with open(out, 'wb') as f:
        pickle.dump(idx, f)
    return idx


def load_index():
    if os.path.exists(IDX):
        return pickle.load(open(IDX, 'rb'))
    return build_index()


def bm25(idx, query, k=K, k1=1.5, b=0.75):
    qs = tok(query)
    if not qs:
        return []
    scored = []
    for d in idx['docs']:
        s = 0.0
        for w in qs:
            f = d['tf'].get(w)
            if not f:
                continue
            s += idx['idf'].get(w, 0.0) * f * (k1 + 1) / (
                f + k1 * (1 - b + b * d['ln'] / idx['avg']))
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


# ───────────────────────── 로컬 LLM 호출 ─────────────────────────
def llm(prompt, mx=2200):
    """로컬 LLM 호출. 학생 이식판: config.json 의 실행기로 보낸다(llm_client)."""
    import llm_client
    r = llm_client.default_client().chat(prompt, mx)
    if r['error']:
        raise RuntimeError(r['error'])
    return r['content'].strip(), r['sec']


TRANS = """Translate the following question into English.
This is about ship engine room maintenance. Use standard marine engineering terms
as they would appear in a ship's maintenance log written by an engineer.
Output only the English sentence. No explanation, no quotes.

{q}
"""

ANSWER = """You answer using ONLY the ship maintenance records below.

Output one JSON object. No markdown, no explanation.
{{"steps":[{{"step":"one step of the procedure, in Korean","record":<record number>,"quote":"the exact English sentence from that record, copied character for character"}}],
 "missing":"what the records do not cover; empty string if nothing"}}

Rules:
- "quote" MUST be copied verbatim from the record. Do not summarise, translate or edit it.
- If you cannot find a supporting sentence, do not write that step at all.
- When you state a number (clearance, pressure, time, load), the quote must contain that number.

[RECORDS]
{ctx}

[QUESTION]
{q}

[JSON]
"""


def sq(s):
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def answer(question, idx, translate=True, verbose=True):
    t0 = time.time()
    # 1) 번역 — 로컬 모델. 이미 영어면 건너뛴다.
    q_en, t_tr = question, 0.0
    if translate and re.search(r'[가-힣]', question):
        q_en, t_tr = llm(TRANS.format(q=question), mx=120)
        q_en = q_en.strip().strip('"').split('\n')[0]

    # 2) 검색 — BM25. LLM 관여 없음.
    hits = bm25(idx, q_en)
    if not hits:
        return dict(q=question, q_en=q_en, hits=[], steps=[], verified=[],
                    note='검색 결과 없음', sec=time.time() - t0)
    bodies = {i: h[1]['meta'][6] for i, h in enumerate(hits, 1)}
    ctx = '\n\n'.join(
        f"Record {i}. Title: {h[1]['meta'][2]}\nMachine: {h[1]['meta'][3]} / "
        f"Equipment: {h[1]['meta'][4]} / Date: {str(h[1]['meta'][5])[:10]}\n"
        f"Content: {h[1]['meta'][6].strip()}" for i, h in enumerate(hits, 1))

    # 3) 생성
    raw, t_gen = llm(ANSWER.format(ctx=ctx, q=q_en))
    txt = re.sub(r'```\w*', '', raw).strip()
    m = re.search(r'\{.*\}', txt, re.S)
    try:
        obj = json.loads(m.group(0)) if m else {}
    except Exception:
        obj = {}
    steps = obj.get('steps', []) if isinstance(obj, dict) else []
    if not steps:
        for mm in re.finditer(r'\{[^{}]*?"quote"\s*:\s*"(?:[^"\\]|\\.)*"[^{}]*?\}', txt):
            try:
                steps.append(json.loads(mm.group(0)))
            except Exception:
                pass

    # 4) 인용 검증 — 근거 없는 단계는 버린다.
    verified, dropped = [], []
    for s in steps:
        if not isinstance(s, dict):
            continue
        rec, quote = s.get('record'), s.get('quote', '')
        try:                      # 모델이 "1" 처럼 문자열로 낼 수 있다
            rec = int(str(rec).strip())
        except (TypeError, ValueError):
            rec = None
        s['record'] = rec
        ok = rec in bodies and sq(quote) and sq(quote) in sq(bodies[rec])
        (verified if ok else dropped).append(s)

    return dict(q=question, q_en=q_en, hits=[h[1]['meta'][2] for h in hits],
                scores=[round(h[0], 1) for h in hits], steps=steps,
                verified=verified, dropped=dropped,
                sec=round(time.time() - t0, 1), t_tr=round(t_tr, 1), t_gen=round(t_gen, 1))


def show(r):
    print(f"\n질문: {r['q']}")
    if r['q_en'] != r['q']:
        print(f"번역: {r['q_en']}")
    print(f"검색된 기록 (BM25 점수):")
    for t, s in zip(r['hits'], r.get('scores', [])):
        print(f"   [{s:>5}] {t[:74]}")
    if not r['verified']:
        print("   → 근거 검증을 통과한 단계 없음")
    for i, s in enumerate(r['verified'], 1):
        print(f"  {i}. {s.get('step','')}")
        print(f"     └ 기록{s.get('record')}: \"{(s.get('quote') or '')[:88]}\"")
    if r.get('dropped'):
        print(f"   (근거 미확인으로 버린 단계 {len(r['dropped'])}개)")
    print(f"   [{r['sec']}초 · 번역 {r.get('t_tr')}초 · 생성 {r.get('t_gen')}초]")



if __name__ == '__main__':
    # 학생 이식판: 원판의 --eval(실제 DB 기준 정답)은 뺐다. 검색만 하거나 질문 하나에 답한다.
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.path.insert(0, _HERE)
    idx = load_index()
    n_ref = sum(1 for d in idx['docs'] if d['meta'][0] == REF)
    print(f"BM25 인덱스: 기록 {idx['N']:,}건(정비 문서 {idx['N'] - n_ref:,} + 참고 {n_ref:,}) · "
          f"어휘 {len(idx['idf']):,}종", flush=True)
    args = sys.argv[1:]
    if args and args[0] == '--search':
        for s_, d in bm25(idx, ' '.join(args[1:]), k=K):
            kind = '참고' if d['meta'][0] == REF else '문서'
            print(f"  [{s_:6.2f}] {kind} {d['meta'][0]} {d['meta'][1]}  {str(d['meta'][2])[:70]}")
    elif args:
        show(answer(' '.join(args), idx))
    else:
        print(__doc__)
