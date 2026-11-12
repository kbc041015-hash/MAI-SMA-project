# -*- coding: utf-8 -*-
"""학생용 DuckDB 만들기 — 정비 문서 1,000건 + 질의응답 200문항(집계형 100 · 절차형 100).

  python build/build_student_db_1000.py --kit <비식별 키트 폴더> [--out-db 파일] [--out-xlsx 파일]

입력은 가명처리된 비식별 키트(deid_kit, 17만 행)다. 실제 DB 와 대응표(열쇠)는 쓰지 않는다. 키트는 학생에게
주지 않으므로 이 스크립트는 교수자 PC 에서만 돈다. 학생에게는 '이 DB 가 어떻게 만들어졌는가'의 기록이다.
  1) 문서 1,000건을 뽑아 정비 데이터 6개 테이블을 만든다(값은 키트 그대로, vessels 의 집계 열만 다시 셈).
  2) 집계형 100문항의 정답 SQL 을 이 DB 에서 다시 실행해 이 DB 기준 정답(answer_text · answer_json)을 넣는다.
     함정(naive) SQL 도 이 DB 에서 다시 돌려 함정이 살아 있는지(trap_kept)를 적는다.
  3) 절차형 100문항과 그 고정 검색 문맥(문항당 기록 4건)을 넣는다(키트 그대로). 질문(question_ko · en)은 평가
     프롬프트(eval/prompts/P_en)에 들어간 원 문안이고, 검수 때 고친 문안은 final_question_ko · en 에 따로 둔다
     (고친 7문항만 값이 있다). keep 은 키트의 true · false · null(미판정)을 그대로 두고 keep_status 에 글로 적는다.
  4) 시스템 프롬프트 원문과 표 설명(_readme)을 넣는다.
정답 JSON(answer_json)과 정답 글(answer_text)의 빈 값은 키트(gold_result · full_db_answer_json)와 같이 "NULL" 로 적는다.
산출: --out-db(기본: 패키지의 data/MAI_SMA_학생용DB_1000.duckdb), --out-xlsx 를 주면 같은 내용의 엑셀 사본
같은 키트에서 다시 돌리면 같은 1,000건 · 같은 정답이 나온다(표본 추출의 난수 씨앗과 순서를 고정했다).
"""
import argparse
import json
import os
import random
import re
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
N_DOCS, SEED = 1000, 20261110
TABLES = ['master', 'pms', 'approval', 'attachments', 'consumption']
# 테이블별 행 순서(같은 문서 안에서도 순서가 늘 같도록 줄 번호까지 정렬한다)
ORDER = {'master': 'VESSELCODE, DOC_NO', 'pms': 'VESSELCODE, DOC_NO, LINE_NO',
         'approval': 'VESSELCODE, DOC_NO, DRAFT_SIDE, SEQ', 'attachments': 'VESSELCODE, DOC_NO, ROW_SEQ',
         'consumption': 'VESSELCODE, DOC_NO, LINE_NO'}
# 초판(2026-11-10) 표본에서 고정한 문서 14건과, 그 단계가 섞은 후보 목록의 길이(난수 흐름을 초판과 같게 둔다).
FIXED_DOCS = [('V05', '25745469'), ('V05', '25139376'), ('V05', '25568010'), ('V05', '25156678'),
              ('V04', '25404183'), ('V04', '25573324'), ('V04', '25863309'), ('V04', '25411884'),
              ('V02', '25151048'), ('V02', '25646554'), ('V02', '25135635'), ('V02', '25023384'),
              ('V03', '25237793'), ('V03', '25266104')]
FIXED_SHUFFLES = [8, 5, 0, 0, 18, 2]


def main():
    ap = argparse.ArgumentParser(description='학생용 DuckDB(정비 문서 1,000건 + 200문항) 만들기')
    ap.add_argument('--kit', default=os.environ.get('SMA_KIT_DIR', ''), help='비식별 키트 폴더(deid_kit)')
    ap.add_argument('--out-db', default=os.path.join(PKG, 'data', 'MAI_SMA_학생용DB_1000.duckdb'))
    ap.add_argument('--out-xlsx', default='', help='같은 내용의 엑셀 사본(주지 않으면 만들지 않는다)')
    a = ap.parse_args()
    kit = a.kit
    src_db = os.path.join(kit, 'db', 'deid_maint.duckdb') if kit else ''
    if not kit or not os.path.exists(src_db):
        sys.exit('[중단] 비식별 키트(deid_kit)가 필요하다. --kit <키트 폴더> 로 준다. 키트는 교수자 PC 에만 있다.')
    build(kit, src_db, os.path.abspath(a.out_db), os.path.abspath(a.out_xlsx) if a.out_xlsx else '')


def build(KIT, SRC, DST, XLSX):
    rnd = random.Random(SEED)
    src = duckdb.connect(SRC, read_only=True)

    def q(s):
        return src.sql(s).fetchall()

    def pick(rows, n):
        rows = sorted(set(map(tuple, rows)))
        rnd.shuffle(rows)
        return rows[:n]

    # ── 1. 문서 1,000건 ─────────────────────────────────────
    docs = sorted(tuple(r) for r in q('SELECT VESSELCODE, DOC_NO FROM master'))
    vessels = sorted({v for v, _ in docs})
    pri = []
    shared = [d for (d,) in q('SELECT DOC_NO FROM master GROUP BY DOC_NO '
                              'HAVING count(DISTINCT VESSELCODE) >= 2 ORDER BY DOC_NO')]
    for d in pick([(x,) for x in shared], 15):          # 문서번호가 여러 배에 겹치는 문서(규칙 1 의 함정)
        pri += [x for x in docs if x[1] == d[0]]
    for n in FIXED_SHUFFLES:
        rnd.shuffle(list(range(n)))
    pri += FIXED_DOCS
    pri += pick(q('SELECT DISTINCT VESSELCODE, DOC_NO FROM consumption'), 40)
    pri += pick(q("SELECT DISTINCT VESSELCODE, DOC_NO FROM pms WHERE MACH_DESC='GENERATOR ENGINE(DIESEL)'"), 40)
    pri += pick(q("SELECT DISTINCT VESSELCODE, DOC_NO FROM pms WHERE MACH_DESC LIKE '%GENERATOR%' "
                  "AND MACH_DESC<>'GENERATOR ENGINE(DIESEL)'"), 20)
    pri += pick(q("SELECT DISTINCT VESSELCODE, DOC_NO FROM pms WHERE MACH_DESC='MAIN ENGINE(DIESEL)'"), 40)
    pri += pick(q("SELECT DISTINCT VESSELCODE, DOC_NO FROM pms WHERE CRITICAL_YN='Y'"), 40)
    pri += pick(q('SELECT DISTINCT VESSELCODE, DOC_NO FROM attachments'), 30)
    pri += [tuple(r) for r in q("SELECT VESSELCODE, DOC_NO FROM master WHERE WORK_REMARK LIKE '%1)%' "
                                "AND WORK_REMARK LIKE '%2)%' ORDER BY length(WORK_REMARK) DESC, VESSELCODE, DOC_NO "
                                "LIMIT 10")]
    final = []
    for x in pri:
        if x not in final and len(final) < N_DOCS:
            final.append(x)
    pools = {v: pick([x for x in docs if x[0] == v and x not in final], 10 ** 6) for v in vessels}
    i = 0
    while len(final) < N_DOCS and any(pools.values()):
        v = vessels[i % len(vessels)]
        i += 1
        if pools[v]:
            final.append(pools[v].pop())
    src.close()

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    for p in (DST, DST + '.wal'):
        if os.path.exists(p):
            os.remove(p)
    db = duckdb.connect(DST)
    db.execute(f"ATTACH '{SRC}' AS s (READ_ONLY)")
    db.execute('CREATE TEMP TABLE _keep (VESSELCODE VARCHAR, DOC_NO VARCHAR)')
    db.executemany('INSERT INTO _keep VALUES (?, ?)', final)
    for t in TABLES:
        cols = ', '.join(f'x.{c.strip()}' for c in ORDER[t].split(','))
        db.execute(f'CREATE TABLE {t} AS SELECT x.* FROM s.{t} x JOIN _keep k '
                   f'ON x.VESSELCODE=k.VESSELCODE AND x.DOC_NO=k.DOC_NO ORDER BY {cols}')
    db.execute('CREATE TABLE vessels AS SELECT * FROM s.vessels ORDER BY VESSELCODE')
    db.execute("""UPDATE vessels SET
      RECORDS = (SELECT count(*) FROM master m WHERE m.VESSELCODE=vessels.VESSELCODE),
      PMS_LINES = (SELECT count(*) FROM pms p WHERE p.VESSELCODE=vessels.VESSELCODE),
      CONS_LINES = (SELECT count(*) FROM consumption c WHERE c.VESSELCODE=vessels.VESSELCODE),
      ATTACHMENTS = (SELECT count(*) FROM attachments a WHERE a.VESSELCODE=vessels.VESSELCODE),
      WORK_DATE_FROM = (SELECT min(WORK_DATE) FROM master m WHERE m.VESSELCODE=vessels.VESSELCODE),
      WORK_DATE_TO = (SELECT max(WORK_DATE) FROM master m WHERE m.VESSELCODE=vessels.VESSELCODE)""")
    db.execute('DETACH s')

    # ── 2. 집계형 100문항 — 이 DB 기준 정답 ───────────────────
    def run(sql):
        # 빈 값(NULL)은 키트의 gold_result · 평가기(run_eval.py)와 같이 문자열 "NULL" 로 적는다
        try:
            cur = db.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [['NULL' if v is None else str(v) for v in r] for r in cur.fetchall()]
            return cols, rows, None
        except Exception as e:
            return None, None, f'{type(e).__name__}: {e}'

    def as_text(cols, rows, err):
        if err:
            return f'[실행 오류] {err}'
        if rows is None:
            return ''
        if len(rows) == 1 and len(cols) == 1:
            return str(rows[0][0])
        head = ' | '.join(cols)
        body = '\n'.join(' | '.join('' if v is None else v for v in r) for r in rows[:20])
        more = f'\n… 총 {len(rows)}행' if len(rows) > 20 else ''
        return f'{head}\n{body}{more}'

    def jsonl(path):
        with open(path, encoding='utf-8') as f:
            return [json.loads(x) for x in f if x.strip()]

    agg_rows = []
    for r in jsonl(os.path.join(KIT, 'questions', 'aggregate100.jsonl')):
        qs = r.get('questions') or {}
        if r['answer_type'] == 'SQL':
            c, rows, err = run(r['gold_sql'])
            ans_text = as_text(c, rows, err)
            ans_json = json.dumps({'columns': c, 'rows': (rows or [])[:50], 'n_rows': len(rows or [])},
                                  ensure_ascii=False)
            n_rows = len(rows or [])
            naive_text, trap = None, None
            if r.get('naive_sql'):
                c2, rows2, err2 = run(r['naive_sql'])
                if rows2 and not re.search(r'order\s+by', r['naive_sql'], re.I):
                    # ORDER BY 없는 결과는 DuckDB 가 내는 순서가 실행마다 달라질 수 있다 — 글로 적을 때 순서를 고정한다
                    rows2 = sorted(rows2, key=lambda x: ['' if v is None else v for v in x])
                naive_text = as_text(c2, rows2, err2)
                trap = (err2 is not None) or sorted(map(tuple, rows2 or [])) != sorted(map(tuple, rows or []))
        else:
            ans_text = r['abstain_token']
            ans_json, n_rows, naive_text, trap = None, None, None, None
        agg_rows.append((
            r['id'], int(r['num']), r.get('difficulty_ko') or r.get('difficulty'), r.get('rule_label'),
            r.get('rule_label_name'), qs.get('orig'), qs.get('L2'), qs.get('L1'), qs.get('NL'),
            r['answer_type'], r.get('abstain_token'), r.get('abstain_reason'), r.get('gold_sql'),
            ans_text, ans_json, n_rows, r.get('naive_sql'), naive_text, trap,
            json.dumps(r.get('gold_result'), ensure_ascii=False) if r.get('gold_result') else None,
            r.get('caution')))
    db.execute("""CREATE TABLE qa_aggregate (
      qid VARCHAR, num INTEGER, difficulty VARCHAR, rule_label VARCHAR, rule_name VARCHAR,
      question_orig VARCHAR, question_L2 VARCHAR, question_L1 VARCHAR, question_nl VARCHAR,
      answer_type VARCHAR, abstain_token VARCHAR, abstain_reason VARCHAR, gold_sql VARCHAR,
      answer_text VARCHAR, answer_json VARCHAR, answer_rows INTEGER,
      naive_sql VARCHAR, naive_answer_text VARCHAR, trap_kept BOOLEAN,
      full_db_answer_json VARCHAR, caution VARCHAR)""")
    db.executemany('INSERT INTO qa_aggregate VALUES (' + ','.join(['?'] * 21) + ')', agg_rows)

    # ── 3. 절차형 100문항과 고정 문맥 ─────────────────────────
    proc_rows, ctx_rows = [], []
    for r in jsonl(os.path.join(KIT, 'questions', 'procedural100.jsonl')):
        # keep: 키트의 true(채택) · false(감사 경고) · null(미판정, 검수 기록 없음)을 그대로(NULL 은 NULL)
        keep = {'true': True, 'false': False}.get(str(r.get('keep')).lower())
        status = {True: '채택', False: '감사 경고', None: '미판정'}[keep]
        # 질문은 평가 프롬프트에 들어간 원 문안. 검수 때 고친 문안은 원 문안과 다를 때만 따로 둔다
        fk = r.get('final_question_ko') if r.get('final_question_ko') != r.get('question_ko') else None
        fe = r.get('final_question_en') if r.get('final_question_en') != r.get('question_en') else None
        proc_rows.append((
            r['id'], r.get('kind'), r.get('category'), r.get('domain'),
            r.get('question_ko'), r.get('question_en'),
            r.get('expected_answer'), r.get('evidence_quote'),
            int(r['gold_n']) if str(r.get('gold_n') or '').isdigit() else None,
            r.get('gold_sql_where'), keep, r.get('keep_warning'),
            int(r['n_records']) if str(r.get('n_records') or '').isdigit() else None,
            status, fk or None, fe or None))
    for r in jsonl(os.path.join(KIT, 'procedural', 'contexts_deid.jsonl')):
        for rec in r['records']:
            ctx_rows.append((r['id'], int(rec['no']), rec.get('title'), rec.get('machine'),
                             rec.get('equipment'), rec.get('date'), rec.get('content')))
    db.execute("""CREATE TABLE qa_procedural (
      qid VARCHAR, kind VARCHAR, category VARCHAR, domain VARCHAR, question_ko VARCHAR,
      question_en VARCHAR, expected_answer VARCHAR, evidence_quote VARCHAR, gold_n INTEGER,
      gold_sql_where VARCHAR, keep BOOLEAN, keep_warning VARCHAR, n_records INTEGER,
      keep_status VARCHAR, final_question_ko VARCHAR, final_question_en VARCHAR)""")
    db.executemany('INSERT INTO qa_procedural VALUES (' + ','.join(['?'] * 16) + ')', proc_rows)
    db.execute("""CREATE TABLE qa_proc_context (
      qid VARCHAR, rec_no INTEGER, title VARCHAR, machine VARCHAR, equipment VARCHAR,
      work_date VARCHAR, content VARCHAR)""")
    db.executemany('INSERT INTO qa_proc_context VALUES (?,?,?,?,?,?,?)', ctx_rows)

    # ── 4. 프롬프트 원문과 표 설명 ───────────────────────────
    P = os.path.join(KIT, 'prompts')
    prompts = [
        ('cond_A', '집계형 SQL 생성 — 조건 A(스키마만)', 'cond_A.txt'),
        ('cond_C', '집계형 SQL 생성 — 조건 C(+미문서화 규칙 12·퓨샷 6)', 'cond_C.txt'),
        ('cond_F', '집계형 SQL 생성 — 조건 F(+컬럼 설명 123 = 연구의 121 + EQUIP_TAG 2, 시스템 기본값)', 'cond_F.txt'),
        ('proc_answer', '절차형 답변 생성 — 고정 문맥 {ctx}와 질문 {q}를 넣는다', 'proc_answer_template.txt'),
        ('datadict', '데이터 사전 — 컬럼 설명과 동의어', 'datadict_deid.txt'),
    ]
    db.execute('CREATE TABLE prompt_templates (name VARCHAR, purpose VARCHAR, text VARCHAR)')
    for n, p, f in prompts:
        fp = os.path.join(P, f)
        if os.path.exists(fp):
            with open(fp, encoding='utf-8') as fh:
                db.execute('INSERT INTO prompt_templates VALUES (?,?,?)', [n, p, fh.read()])

    desc = {
        'master': '정비 문서 1건 = 1행(가명처리). 키 (VESSELCODE, DOC_NO). EQUIP_TAG = 기기5+호기3+장비5+호기3',
        'pms': '정비 작업 라인 1건 = 1행. master 와 (VESSELCODE, DOC_NO)로 연결',
        'approval': '결재 단계 1건 = 1행',
        'attachments': '첨부 파일 1건 = 1행(파일명·ID 가명)',
        'consumption': '부품 소모 1건 = 1행(자재코드·부품번호 가명)',
        'vessels': '선박 6척. 집계 열은 이 DB(문서 1,000건) 기준으로 다시 셈',
        'qa_aggregate': '집계형 100문항. answer_text·answer_json 은 이 DB 기준 정답(빈 값은 NULL), full_db_answer_json 은 전체 DB 기준(참고). trap_kept = 규칙을 모르는 naive SQL 이 틀리는가',
        'qa_procedural': '절차형 100문항. 질문(question_ko·en)은 평가 프롬프트의 원 문안, final_question_* 는 검수 수정 문안(7문항). 기대 답변·근거 인용(가명처리). keep: true 채택 · false 감사 경고(참고 답안) · NULL 미판정(keep_status)',
        'qa_proc_context': '절차형 문항마다 모델에 주는 고정 검색 문맥(기록 4건, 가명처리). 정비 데이터 테이블과 별개',
        'prompt_templates': '시스템이 LLM 에 보내는 프롬프트 원문과 데이터 사전',
    }
    db.execute('CREATE TABLE _readme (table_name VARCHAR, row_count BIGINT, description VARCHAR)')
    for t, d in desc.items():
        n = db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
        db.execute('INSERT INTO _readme VALUES (?,?,?)', [t, n, d])
    db.execute('CHECKPOINT')

    # ── 5. 엑셀 사본(선택) ───────────────────────────────────
    if XLSX:
        import pandas as pd
        from openpyxl.utils import get_column_letter
        order = ['_readme', 'qa_aggregate', 'qa_procedural', 'qa_proc_context', 'master', 'pms',
                 'approval', 'attachments', 'consumption', 'vessels', 'prompt_templates']
        os.makedirs(os.path.dirname(XLSX), exist_ok=True)
        with pd.ExcelWriter(XLSX, engine='openpyxl') as w:
            for t in order:
                df = db.sql(f'SELECT * FROM {t}').df()
                for c in df.columns:     # 엑셀 셀 한도(32,767자)
                    if df[c].dtype == object:
                        df[c] = df[c].map(lambda v: v[:32000] + ' …(잘림)' if isinstance(v, str) and len(v) > 32000 else v)
                df.to_excel(w, sheet_name=t[:31], index=False)
                ws = w.sheets[t[:31]]
                ws.freeze_panes = 'A2'
                ws.auto_filter.ref = ws.dimensions
                for j, c in enumerate(df.columns, 1):
                    ws.column_dimensions[get_column_letter(j)].width = min(50, max(10, len(str(c)) + 2))

    for t, n, _d in db.execute('SELECT * FROM _readme').fetchall():
        print(f'{t:17s} {n:6,d}행')
    print('trap_kept:', db.execute('SELECT trap_kept, count(*) FROM qa_aggregate GROUP BY 1 ORDER BY 1').fetchall())
    print('keep_status:', db.execute('SELECT keep_status, count(*) FROM qa_procedural GROUP BY 1 ORDER BY 1').fetchall())
    print('empty answers:', db.execute("SELECT count(*) FROM qa_aggregate WHERE answer_type='SQL' AND "
                                       "(answer_rows=0 OR answer_text LIKE '[실행 오류]%')").fetchone()[0])
    db.close()


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
