# -*- coding: utf-8 -*-
"""자체 점검 — 실제 LLM 없이 이 패키지의 모든 경로를 끝까지 돌려 본다.

이 PC 안(127.0.0.1 의 빈 포트)에 '모의 OpenAI 호환 서버'를 띄워 LLM 대신 쓰고, 웹 서버를 실제로
기동해 /api/ask 로 집계형 · 기권 · 절차형(영어·한국어) · 후속 질문 · 병기 · 중단 · 오류 처리를 끝까지
점검한다. 터미널판, LLM 점검 도구, 평가기(eval/run_eval.py)와 엑셀 기록 도구도 같은 모의 서버로 돌린다.
학생 PC 의 LLM 실행기·모델과 무관하게 '패키지가 제대로 놓였는가'를 본다(실제 LLM 연결은 check_llm.py).

사용(패키지 폴더에서)
  python tools/selftest.py            전체(1~2분)
  python tools/selftest.py --quick    평가기 자체 검사(eval/run_eval.py --self-test)를 건너뛴다
마지막 줄이 'selftest PASS — …' 이면 통과다(실패가 있으면 'selftest FAIL — …' 이고 요약 줄에 'FAIL n' 이 붙는다).
외부 인터넷은 쓰지 않는다. 임시 웹 서버는 빈 포트에 띄우고 번호표도 임시 폴더에 쓰므로, 켜 둔 웹 서버
(START.bat)와 그 번호표(logs/web.pid)는 건드리지 않는다.
"""
import csv
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
APP = os.path.join(PKG, 'app')
sys.path.insert(0, APP)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PY = sys.executable
DB_NAME = 'MAI_SMA_학생용DB_1000.duckdb'       # 학생용 DB(정비 문서 1,000건 + 질의응답 200문항)
INDEX_NAME = 'bm25_1000.pkl'                  # 검색 색인(정비 문서 기록 + 참고 기록)
FORM_NAME = '기록양식_200문항.xlsx'            # eval/ 의 엑셀 기록양식(이 DB 기준)
EXPECTED_ROWS = {'master': 1000, 'pms': 2296, 'consumption': 174, 'attachments': 286, 'approval': 2979, 'vessels': 6,
                 'qa_aggregate': 100, 'qa_procedural': 100, 'qa_proc_context': 400, 'prompt_templates': 5}
INDEX = {}                                    # sec_a 가 DB 에서 센다: 정비 문서 기록 · 참고 기록 · 합계
Q_AGG = '주기관 정비를 호기별로 세어줘'                       # = qa_aggregate Q090(자연어 문형)
Q_AGG2 = '발전기 정비를 호기별로 세어줘'                      # = Q089(후속 질문이 다시 쓰이는 문장)
Q_AGG_EN = 'Count the main engine maintenance lines by unit number'
Q_ABS = '정비 한 건에 평균 몇 명이나 투입돼?'                 # = Q072 → NO_RELIABLE_VALUE
Q_THINK = '선박은 몇 척인가? (THINK-TEST)'
Q_PROC_EN = 'What torque is the large bowl lock nut tightened to when assembling the purifier?'   # 정비 문서 기록
Q_PROC_KO = '발전기 과급기 물세척할 때 엔진 부하와 터빈 입구 온도는 얼마로 유지해야 하나?'
T_PROC_KO = ('What engine load and turbine inlet temperature should be maintained when water washing '
             'the generator engine turbocharger?')
REF_QID = 'P063'                              # 참고 기록(qa_proc_context)에서 답하는 절차형 문항
Q_PROC_REF = ('Does the pressure for the mooring winch remote operating stand come from a dedicated pump, or is it '
              'taken from the deck machinery pump through a pressure reducing valve? What is the set pressure?')
Q_FOLLOW = '그럼 발전기는?'
SQL_ME = "SELECT MACH_NO, count(*) AS n FROM pms WHERE MACH_DESC='MAIN ENGINE(DIESEL)' GROUP BY MACH_NO ORDER BY MACH_NO;"
SQL_DG = "SELECT MACH_NO, count(*) AS n FROM pms WHERE MACH_DESC='GENERATOR ENGINE(DIESEL)' GROUP BY MACH_NO ORDER BY MACH_NO;"
SQL = {
    Q_AGG: SQL_ME,
    Q_AGG_EN: SQL_ME,
    Q_AGG2: SQL_DG,
    Q_ABS: 'NO_RELIABLE_VALUE',
    Q_THINK: '<think>\nI could write SELECT * FROM master here, but the question is about vessels.\n</think>\n'
             'SELECT count(*) AS n FROM vessels;',
    '선박은 몇 척인가?': 'SELECT count(*) AS n FROM vessels;',
    '발전기 정비에 투입된 인원은 평균 몇 명인가?': 'NO_RELIABLE_VALUE',
}
NEEDLE = re.compile(r'확인 낱말:\s*([A-Z]+-\d+)')
EXPECT = {}                                   # sec_a 가 DB 에서 직접 계산: 질문 → 기대 결과 행(글자)


def expect_rows(con, sql):
    """웹 화면을 거치지 않고 DuckDB 에서 바로 계산한 결과(화면이 내는 형식 = 칸마다 글자)."""
    return [[('' if v is None else str(v)) for v in r] for r in con.execute(sql).fetchall()]


# ───────────────────────── 기록 ─────────────────────────
class T:
    items = []

    @classmethod
    def check(cls, name, ok, detail=''):
        st = 'PASS' if ok else 'FAIL'
        cls.items.append((st, name, detail))
        print(f'[{st}] {name}' + (f'  — {detail}' if detail else ''), flush=True)
        return ok

    @classmethod
    def note(cls, text):
        cls.items.append(('NOTE', text, ''))
        print(f'[참고] {text}', flush=True)

    @staticmethod
    def section(title):
        print(f'\n■ {title}', flush=True)


def guarded(name):
    """점검 하나가 예외로 죽어도 나머지는 계속한다."""
    def deco(fn):
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as e:
                T.check(name, False, f'예외 {type(e).__name__}: {str(e)[:200]}')
                return None
        return run
    return deco


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, body=None, timeout=60):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'},
                                 method='POST' if data is not None else 'GET')
    with OPENER.open(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode('utf-8'))
    except ValueError:
        return raw.decode('utf-8', 'replace')


# ───────────────────────── 모의 LLM ─────────────────────────
def load_kit_answers():
    out = {}
    p = os.path.join(PKG, 'eval', 'questions', 'aggregate100.jsonl')
    if os.path.exists(p):
        for ln in open(p, encoding='utf-8'):
            if ln.strip():
                r = json.loads(ln)
                ans = r['abstain_token'] if r['answer_type'] == 'ABSTAIN' else r['gold_sql']
                for q in r['questions'].values():
                    out.setdefault(q, ans)
    return out


def records_of(prompt):
    """프롬프트의 [RECORDS] 를 기록 번호 → 본문으로 나눈다(웹 형식 · 평가기 형식 모두)."""
    m = re.search(r'\[RECORDS\]\n(.*?)\n\n\[QUESTION\]', prompt, re.S)
    if not m:
        return {}
    out = {}
    for no, chunk in re.findall(r'(?ms)^Record (\d+)\. Title: (.*?)(?=^Record \d+\. Title: |\Z)', m.group(1)):
        lines = chunk.split('\n')
        k = next((i for i, ln in enumerate(lines) if ln.startswith('Date:') or '/ Date:' in ln), 1)
        body = '\n'.join(lines[k + 1:]).strip()
        out[int(no)] = body[len('Content: '):] if body.startswith('Content: ') else body
    return out


def pick_quote(body):
    sents = [s.strip() for s in re.split(r'(?<=\.)\s+', body) if s.strip()]
    for s in sents:
        if 'N.m' in s or 'degC' in s:
            return s
    for s in sents:
        if re.search(r'\d', s):
            return s
    return sents[0] if sents else ''


class MockLLM:
    """OpenAI 호환 모의 서버. 받은 프롬프트의 종류를 보고 정해진 답을 돌려준다."""

    def __init__(self, model='mock-model'):
        self.calls, self.model, self.kit = [], model, load_kit_answers()
        mock = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass            # 호출 쪽이 중단으로 연결을 끊었다

            def do_GET(self):
                mock.calls.append(dict(method='GET', path=self.path))
                if self.path.rstrip('/').endswith('/models'):
                    return self._send(200, {'object': 'list', 'data': [{'id': mock.model, 'object': 'model'}]})
                self._send(404, {'error': {'message': 'not found'}})

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n).decode('utf-8'))
                mock.calls.append(dict(method='POST', path=self.path, body=body,
                                       auth=self.headers.get('Authorization')))
                code, content, fr = mock.respond(body)
                if code != 200:
                    return self._send(code, content)
                p = body['messages'][0]['content']
                self._send(200, {'id': 'mock', 'object': 'chat.completion', 'model': mock.model,
                                 'choices': [{'index': 0, 'finish_reason': fr,
                                              'message': {'role': 'assistant', 'content': content}}],
                                 'usage': {'prompt_tokens': max(1, len(p) // 3), 'completion_tokens': 5}})

        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f'http://127.0.0.1:{self.port}/v1'

    def posts(self):
        return [c for c in self.calls if c['method'] == 'POST']

    def respond(self, body):
        p = body['messages'][0]['content']
        if 'ERROR-404-TEST' in p:
            return 404, {'error': {'message': f'model "{self.model}" not found, try pulling it first'}}, None
        if 'SLOW-TEST' in p:
            time.sleep(20)
            return 200, 'SELECT 1;', 'stop'
        m = NEEDLE.search(p[:200])
        if m:
            return 200, m.group(1), 'stop'
        if p.startswith('Reply with exactly'):
            return 200, 'OK', 'stop'
        if p.startswith('당신은 선박 정비 데이터베이스의 SQL 생성기다'):
            if body.get('max_tokens') == 1 or body.get('max_completion_tokens') == 1:
                return 200, 'S', 'length'
            q = p.rsplit('[질문]\n', 1)[-1].split('\n\n[SQL]')[0].strip()
            return 200, SQL.get(q) or self.kit.get(q) or 'SELECT 1;', 'stop'
        if p.startswith('Translate the following Korean question'):
            q = p.rstrip().rsplit(chr(10), 1)[-1].strip()
            if q == Q_PROC_KO:
                return 200, T_PROC_KO, 'stop'
            if q == Q_AGG:
                return 200, Q_AGG_EN, 'stop'
            mark = ' '.join(re.findall(r'[A-Z0-9]+-TEST', q))
            return 200, (mark + ' How do I dismantle the purifier?').strip(), 'stop'
        if p.startswith('다음 선박 정비 질문을 자연스러운 한국어로'):
            return 200, '주기관 정비 라인을 호기별로 세어 주세요', 'stop'
        if p.startswith('아래는 선박 정비 질의응답의 직전 대화다'):
            return 200, Q_AGG2, 'stop'
        if p.startswith('Below is the preceding turn'):
            return 200, 'Count the generator engine maintenance lines by unit number', 'stop'
        if p.startswith('You answer using ONLY the ship maintenance records below.'):
            if 'BADJSON-TEST' in p:
                return 200, 'I am sorry, I cannot produce JSON for this.', 'stop'
            recs = records_of(p)
            rec = min(recs) if recs else 1
            quote = pick_quote(recs.get(rec, ''))
            st = [{'step': 'Use the value recorded in the source.', 'record': rec, 'quote': quote},
                  {'step': 'Re-torque after 24 hours.', 'record': rec,
                   'quote': 'This sentence does not appear in any of the records.'}]
            if len(recs) == 1:          # LLM 점검 도구(check_llm.py)의 1건 시험 — 바른 답만
                st = st[:1]
            if '"step_ko"' in p:
                for s in st:
                    s['step_ko'] = '기록에 적힌 값을 따른다.'
            return 200, json.dumps({'steps': st, 'missing': 'The records do not give a re-torque interval.'}), 'stop'
        return 200, 'OK', 'stop'

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# ───────────────────────── A. 구성 ─────────────────────────
REQUIRED = ['config.json', 'START.bat', 'STOP.bat', 'start.sh', '00_먼저_읽기.md', 'AI_이식_프롬프트.md',
            'app/maintqa_web.py', 'app/maintqa_web.html', 'app/maintqa_cli.py', 'app/router.py', 'app/rag.py',
            'app/term_map.py', 'app/sel_dict.py', 'app/harness.py', 'app/build_prompt100.py', 'app/answer_text.py',
            'app/model_specs.py', 'app/llm_client.py', 'app/sma_config.py',
            f'data/{DB_NAME}', f'data/{INDEX_NAME}', 'data/datadict_deid.txt', 'data/demo_questions.txt',
            'prompts/01_집계형_SQL생성_조건F.txt', 'prompts/02_절차형_답변.txt', 'prompts/03_질문번역.txt',
            'prompts/04_후속질문_재작성.txt', 'prompts/05_병기번역.txt', 'prompts/README.md',
            'eval/run_eval.py', f'eval/db/{DB_NAME}', f'eval/{FORM_NAME}', 'eval/00_평가_안내.md',
            'eval/prompts/cond_F.txt', 'eval/prompts/F_nl/Q001.txt', 'eval/prompts/P_en/P001.txt',
            'eval/questions/aggregate100.jsonl', 'eval/questions/procedural100.jsonl',
            'eval/procedural/contexts_deid.jsonl', 'eval/eval_from_config.py', 'eval/fill_record_form.py',
            'eval/tool/harness_kit.py', 'eval/tool/score_kit.py',
            'docs/fig_query_architecture.png', 'docs/fig_deployment.png', 'docs/아키텍처_설명.md',
            'docs/원본과_달라진_점.md', 'build/build_student_db_1000.py',
            'tools/rebuild_index.py', 'tools/check_llm.py', 'tools/export_prompts.py',
            'config_examples/ollama.json', 'config_examples/lmstudio.json', 'config_examples/llamacpp.json',
            'config_examples/openai_compatible.json']


def sec_a():
    T.section('A. 패키지 구성과 데이터')
    miss = [p for p in REQUIRED if not os.path.exists(os.path.join(PKG, *p.split('/')))]
    T.check('필수 파일', not miss, f'{len(REQUIRED) - len(miss)}/{len(REQUIRED)}' + (f' 없음: {miss[:5]}' if miss else ''))
    v = sys.version_info
    T.check('파이썬 3.10 이상', v >= (3, 10), f'{v.major}.{v.minor}.{v.micro} · {sys.executable}')
    import sma_config as CONF
    try:
        import duckdb  # noqa: F401
    except ImportError:
        T.check('duckdb 설치', False, 'python -m pip install -r requirements.txt')
        return False
    ok, msg = CONF.duckdb_status()
    if not T.check('duckdb 1.5 이상 2 미만(requirements.txt 의 범위)', ok, msg):
        return False
    os.environ['MAINT_DB'] = os.path.join(PKG, 'data', DB_NAME)
    os.environ['BM25_IDX'] = os.path.join(PKG, 'data', INDEX_NAME)
    os.environ['MAI_SMA_DATADICT'] = os.path.join(PKG, 'data', 'datadict_deid.txt')
    import harness
    con = harness.get_con()
    rows = {t: con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in EXPECTED_ROWS}
    T.check('학생용 DB 테이블 행 수(정비 문서 1,000건 + 집계형 100 · 절차형 100 · 고정 문맥 400 — 17만 행 DB 아님)',
            rows == EXPECTED_ROWS, ' · '.join(f'{t} {n:,}' for t, n in rows.items()))
    # 화면·터미널 점검의 기대값은 DB 에서 바로 계산한다(웹을 거치지 않은 값과 웹이 낸 값을 맞대 본다)
    for q, sql in ((Q_AGG, SQL_ME), (Q_AGG2, SQL_DG), ('선박은 몇 척인가?', 'SELECT count(*) AS n FROM vessels;')):
        EXPECT[q] = expect_rows(con, sql)
    qa = dict(con.execute("SELECT qid, gold_sql FROM qa_aggregate WHERE qid IN ('Q089', 'Q090')").fetchall())
    T.check('예시 질문의 정답 SQL = DB 의 qa_aggregate(Q090 주기관 · Q089 발전기 호기별)',
            qa.get('Q090') == SQL_ME and qa.get('Q089') == SQL_DG,
            f"주기관 {EXPECT[Q_AGG]} · 발전기 {EXPECT[Q_AGG2]}")
    INDEX['doc'] = con.execute('SELECT count(*) FROM master WHERE length(WORK_REMARK) >= 300').fetchone()[0]
    INDEX['ref'] = con.execute('SELECT count(DISTINCT content) FROM qa_proc_context').fetchone()[0]
    INDEX['N'] = INDEX['doc'] + INDEX['ref']
    INDEX['ref_rows'] = con.execute("SELECT rec_no, title, content, machine FROM qa_proc_context WHERE qid = ? "
                                    "ORDER BY rec_no", [REF_QID]).fetchall()
    try:
        con.execute('CREATE TABLE _x AS SELECT 1')
        T.check('DB 읽기 전용', False, '쓰기가 됐다')
    except Exception:
        T.check('DB 읽기 전용', True, '쓰기 거부')
    try:
        con.execute("SELECT * FROM read_text('config.json')").fetchall()
        T.check('모델 SQL 의 외부 파일 접근 차단', False, '파일을 읽었다')
    except Exception as e:
        T.check('모델 SQL 의 외부 파일 접근 차단', True, type(e).__name__)
    tags = con.execute("SELECT count(*) FROM pms WHERE length(EQUIP_TAG) = 16").fetchone()[0]
    T.check('EQUIP_TAG 16자리 복합코드(기기 5 + 호기 3 + 장비 5 + 호기 3)', tags == EXPECTED_ROWS['pms'], f'pms {tags:,}행')
    T.check('data/ 와 eval/db/ 의 DB 가 같은 파일',
            sha(os.path.join(PKG, 'data', DB_NAME)) == sha(os.path.join(PKG, 'eval', 'db', DB_NAME)))
    import pickle
    with open(os.path.join(PKG, 'data', INDEX_NAME), 'rb') as f:
        idx = pickle.load(f)
    refs = [d for d in idx['docs'] if d['meta'][0] == 'QA_REF']
    ok = (idx['N'] == INDEX['N'] and len(refs) == INDEX['ref'] and all(len(d['meta']) == 7 for d in idx['docs'])
          and all(re.fullmatch(r'P\d{3}-[1-4]', d['meta'][1]) for d in refs))
    T.check('검색 색인 = 정비 문서 기록(서술 300자 이상) + 참고 기록(qa_proc_context, 본문 중복 제거)', ok,
            f"N={idx['N']:,} = 정비 문서 {idx['N'] - len(refs):,} + 참고 {len(refs):,} · 어휘 {len(idx['idf']):,}")
    r = subprocess.run([PY, '-B', os.path.join(HERE, 'rebuild_index.py'), '--check'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    T.check('색인 = DB 에서 다시 만든 것(tools/rebuild_index.py --check)', r.returncode == 0, r.stdout.strip()[-80:])
    import sel_dict
    import term_map
    ent = sel_dict.load_entries()
    T.check('데이터 사전(비식별판) 항목', len(ent) >= 121, f'{len(ent)}개 컬럼')
    samples = [ln for ln in open(os.path.join(PKG, 'data', 'demo_questions.txt'), encoding='utf-8')
               if re.match(r'\s*\d+\.\s+', ln)]
    T.check('예시 질문 — 화면 카드가 고르는 네 문항(기권 · 집계 · 절차 · 판정 기준)이 들어 있음',
            all(any(k in ln for ln in samples) for k in ('평균 몇 명', '호기별', 'windlass brake', 'mooring winch remote')),
            f'{len(samples)}개')
    T.check('설비 용어 대응표 82항목', len(term_map._ALL) == 82, f'{len(term_map._ALL)}항목')
    return True


# ───────────────────────── B. 프롬프트 ─────────────────────────
def sec_b():
    T.section('B. 프롬프트 — 평가기와 같은 글')
    import build_prompt100 as BP
    import rag
    tmpl = BP.HEAD + BP.SC + '\n' + BP.RULES + BP.FEWSHOT + BP.TAIL
    kit = open(os.path.join(PKG, 'eval', 'prompts', 'cond_F.txt'), encoding='utf-8').read()
    T.check('집계형 프롬프트(조건 F) = eval/prompts/cond_F.txt', tmpl == kit, f'{len(tmpl):,}자 · 컬럼 설명 '
            f'{sum(1 for ln in BP.SC.split(chr(10)) if "  -- " in ln)}개')
    p1 = open(os.path.join(PKG, 'prompts', '01_집계형_SQL생성_조건F.txt'), encoding='utf-8').read()
    T.check('prompts/01 = 실제로 보내는 집계형 전문', p1 == tmpl)
    pt = open(os.path.join(PKG, 'eval', 'prompts', 'proc_answer_template.txt'), encoding='utf-8').read()
    T.check('절차형 템플릿(rag.ANSWER) = eval/prompts/proc_answer_template.txt', rag.ANSWER == pt)
    f1 = open(os.path.join(PKG, 'eval', 'prompts', 'F_nl', 'Q001.txt'), encoding='utf-8').read()
    q1 = json.loads(open(os.path.join(PKG, 'eval', 'questions', 'aggregate100.jsonl'), encoding='utf-8').readline())
    T.check('평가기 붙여넣기 프롬프트 Q001 = 웹 화면이 같은 질문에 보내는 글', f1 == tmpl.replace('{q}', q1['questions']['NL']))
    r = subprocess.run([PY, '-B', os.path.join(HERE, 'export_prompts.py'), '--check'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    T.check('prompts/02~05 사본 = 코드의 프롬프트', r.returncode == 0, r.stdout.strip()[-60:])


# ───────────────────────── C. 설정·프리셋 ─────────────────────────
def sec_c():
    T.section('C. 설정 파일과 요청 프리셋(llm_client)')
    import sma_config as CONF
    import llm_client as LLM
    try:
        cfg = CONF.load()
        T.check('config.json 읽기·검사', True, CONF.summary(cfg))
    except CONF.ConfigError as e:
        if CONF.PLACEHOLDER.search(str(e)):
            T.note('config.json 의 model 이 아직 자리표시값입니다(설정 전). 00_먼저_읽기.md 4단계에서 채우고 '
                   'tools/check_llm.py 로 실제 LLM 을 점검하십시오.')
        else:
            T.check('config.json 읽기·검사', False, str(e).split('\n')[0][:160])
    want = {'ollama': ('ollama', 'http://localhost:11434/v1'), 'lmstudio': ('lmstudio', 'http://localhost:1234/v1'),
            'llamacpp': ('llamacpp', 'http://127.0.0.1:8080/v1'), 'openai_compatible': ('openai', None)}
    got, bad = {}, []
    for f, (pr, base) in want.items():
        try:
            # utf-8-sig: 메모장의 'UTF-8(BOM)' 으로 저장해도 읽는다(웹 서버 · check_llm 과 같다)
            d = json.load(open(os.path.join(PKG, 'config_examples', f + '.json'), encoding='utf-8-sig'))
            got[f] = (CONF.norm_preset(d.get('preset')), CONF.norm_base(d.get('base_url')))
            if got[f][0] != pr or (base and got[f][1] != base) or set(d) - CONF.KNOWN:
                bad.append(f)
        except (OSError, ValueError) as e:
            bad.append(f'{f}({type(e).__name__})')
    T.check('설정 예시 4종(JSON · preset · 기본 주소 · 설정 이름)', not bad,
            ' · '.join(f'{k}={v[0]}@{v[1]}' for k, v in got.items()) + (f' · 문제 {bad}' if bad else ''))
    d0 = json.load(open(os.path.join(PKG, 'config.json'), encoding='utf-8-sig'))
    d1 = json.load(open(os.path.join(PKG, 'config_examples', 'ollama.json'), encoding='utf-8-sig'))
    if CONF.PLACEHOLDER.search(str(d0.get('model'))):
        T.check('config.json 기본값 = config_examples/ollama.json(설정 전 상태)', d0 == d1)
    else:
        T.check('config.json 설정 이름(오타 없음)', not (set(d0) - CONF.KNOWN), ', '.join(sorted(set(d0) - CONF.KNOWN)))

    def mk(preset, model='m', **kw):
        c = dict(CONF.DEFAULTS, preset=preset, base_url=CONF.DEFAULT_BASE[preset], model=model)
        c.update(kw)
        return LLM.Client(c)
    b = mk('llamacpp', model='').body('x', 600)
    T.check('llamacpp 프리셋 = 원 시연판 요청 본문', {k: v for k, v in b.items() if k != 'messages'} ==
            dict(temperature=0, top_k=1, seed=42, max_tokens=600, chat_template_kwargs=dict(enable_thinking=False)),
            json.dumps({k: v for k, v in b.items() if k != 'messages'}))
    b = mk('ollama').body('x', 600)
    T.check('ollama 프리셋: model 포함 · top_k·chat_template_kwargs 없음 · seed 42',
            b.get('model') == 'm' and 'top_k' not in b and 'chat_template_kwargs' not in b and b.get('seed') == 42
            and b.get('temperature') == 0 and b.get('max_tokens') == 600, json.dumps({k: v for k, v in b.items() if k != 'messages'}))
    b = mk('lmstudio').body('x', 600)
    T.check('lmstudio 프리셋: top_k 1 · seed 42 · chat_template_kwargs 없음',
            b.get('top_k') == 1 and b.get('seed') == 42 and 'chat_template_kwargs' not in b and b.get('model') == 'm')
    b = mk('openai').body('x', 600)
    T.check('openai 프리셋: 표준 필드만(model · messages · temperature · max_tokens)',
            set(b) == {'model', 'messages', 'temperature', 'max_tokens'})
    b = mk('openai', base_url='https://api.openai.com/v1', allow_remote=True).body('x', 600)
    T.check('api.openai.com → max_completion_tokens', 'max_completion_tokens' in b and 'max_tokens' not in b)
    b = mk('ollama', seed=False, temperature=None, extra_body={'reasoning_effort': 'none'}).body('x', 9)
    T.check('설정으로 seed·temperature 빼기 · extra_body 더하기', 'seed' not in b and 'temperature' not in b
            and b.get('reasoning_effort') == 'none')
    nb = [CONF.norm_base(x) for x in ('127.0.0.1:8080', 'http://localhost:11434', 'http://localhost:1234/v1/',
                                      'http://127.0.0.1:8080/v1/chat/completions')]
    T.check('base_url 정리(/v1 보충 · 끝 경로 제거)', nb == ['http://127.0.0.1:8080/v1', 'http://localhost:11434/v1',
                                                       'http://localhost:1234/v1', 'http://127.0.0.1:8080/v1'], str(nb))
    loc = [CONF.is_local(u) for u in ('http://localhost:1/v1', 'http://127.0.0.1:1/v1', 'http://192.168.0.7:1/v1',
                                       'http://10.1.2.3:1/v1', 'https://api.openai.com/v1', 'http://8.8.8.8/v1')]
    T.check('외부 주소 판별(이 PC·사내망만 허용)', loc == [True, True, True, True, False, False], str(loc))
    tmp = tempfile.mkdtemp(prefix='sma_cfg_')
    try:
        p = os.path.join(tmp, 'c.json')
        json.dump(dict(preset='openai', base_url='https://api.example.com/v1', model='x'), open(p, 'w'))
        try:
            CONF.load(p)
            blocked = False
        except CONF.ConfigError:
            blocked = True
        json.dump(dict(preset='openai', base_url='https://api.example.com/v1', model='x', allow_remote=True), open(p, 'w'))
        allowed = CONF.load(p)['base_url'] == 'https://api.example.com/v1'
        T.check('외부 주소는 allow_remote 없이 거부', blocked and allowed)
        open(p, 'w', encoding='utf-8').write('{"preset": "ollama", "model": "x",}')
        try:
            CONF.load(p)
            msg = ''
        except CONF.ConfigError as e:
            msg = str(e)
        T.check('JSON 오류를 줄·칸과 함께 알림', '줄' in msg, msg.split('\n')[0][:80])
        # 윈도 경로를 역슬래시 그대로 적은 경우(Invalid \escape) → / 로 적으라는 안내
        open(p, 'w', encoding='utf-8').write('{"preset": "llamacpp", "model": "", "gguf_path": "C:\\llama\\m.gguf"}')
        try:
            CONF.load(p)
            msg = ''
        except CONF.ConfigError as e:
            msg = str(e)
        T.check('경로 역슬래시(Invalid \\escape) → / 로 적으라고 안내', 'escape' in msg.lower() and 'C:/llama' in msg,
                msg.split('\n')[0][:80])
        # 메모장 'UTF-8(BOM)' 으로 저장한 config.json 도 읽는다
        open(p, 'w', encoding='utf-8-sig').write('{"preset": "ollama", "base_url": "http://localhost:11434/v1", "model": "x"}')
        try:
            ok_bom = CONF.load(p)['model'] == 'x'
        except CONF.ConfigError:
            ok_bom = False
        T.check('UTF-8(BOM) 으로 저장한 config.json 읽기', ok_bom)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    s, had = LLM.strip_think('<think>SELECT x</think>\nSELECT 1;')
    s2, had2 = LLM.strip_think('<think>잘린 생각')
    T.check('사고(thinking) 덩어리 걷어 내기', s.strip() == 'SELECT 1;' and had and s2 == '' and had2)
    ml = [LLM.model_listed('qwen3', ['qwen3:latest']), LLM.model_listed('qwen3:8b', ['qwen3:14b']),
          LLM.model_listed('g.gguf', ['C:/m/g.gguf']), LLM.model_listed('', ['a'])]
    T.check('모델 이름 대조(Ollama :latest · 경로형 id)', ml == [True, False, True, None], str(ml))
    import maintqa_web as W
    nm = [W.server_model_name(['C:\\m\\gemma-4-12B-it-Q4_K_M.gguf']), W.server_model_name(['/m/q.gguf']),
          W.server_model_name(['gemma3:12b']), W.server_model_name([])]
    T.check('model 을 비운 설정의 화면 모델 이름 = 실행기 id 의 파일 이름(경로 없음)',
            nm == ['gemma-4-12B-it-Q4_K_M', 'q', 'gemma3:12b', ''], str(nm))
    dk = [CONF.duckdb_status()[0]]
    import duckdb
    real = duckdb.__version__
    try:
        for fake in ('2.0.0', '1.4.4', '1.5.0', '1.10.1'):
            duckdb.__version__ = fake
            dk.append(CONF.duckdb_status()[0])
    finally:
        duckdb.__version__ = real
    T.check('duckdb 판 범위 판정(1.5 이상 2 미만)', dk == [True, False, False, True, True], str(dk))


# ───────────────────────── D. 웹 화면 끝까지 ─────────────────────────
def write_cfg(path, mock, **kw):
    c = dict(preset='ollama', base_url=mock.base, model='mock-model', web_port=free_port(), warmup=True,
             timeout_sec=60, retries=0)
    c.update(kw)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    return c


def sec_d(mock, tmp):
    T.section('D. 웹 화면 — 모의 LLM 으로 기동해 /api 를 끝까지')
    cfgp = os.path.join(tmp, 'web.json')
    c = write_cfg(cfgp, mock)
    port = c['web_port']
    base = f'http://127.0.0.1:{port}'
    logf = open(os.path.join(tmp, 'web.log'), 'w', encoding='utf-8')
    # 번호표는 임시 폴더에 쓴다 — 켜 둔 실제 서버(START.bat)의 logs/web.pid 를 덮어쓰지 않게
    pidf = os.path.join(tmp, 'web.pid')
    real_pid = os.path.join(PKG, 'logs', 'web.pid')
    before = open(real_pid).read() if os.path.exists(real_pid) else None
    p = subprocess.Popen([PY, '-B', os.path.join(APP, 'maintqa_web.py'), '--config', cfgp, '--no-open',
                          '--pid-file', pidf], cwd=PKG, stdout=logf, stderr=subprocess.STDOUT)
    info, t0 = None, time.time()
    try:
        while time.time() - t0 < 90:
            if p.poll() is not None:
                break
            try:
                info = http_json(base + '/api/info', timeout=3)
                break
            except Exception:
                time.sleep(0.5)
        if not T.check('웹 서버 기동(config 로 LLM 연결 → DB·색인 적재 → 화면 응답)', isinstance(info, dict),
                       f'{time.time() - t0:.1f}초'):
            logf.flush()
            print(open(os.path.join(tmp, 'web.log'), encoding='utf-8', errors='replace').read()[-1500:])
            return
        web_checks(mock, base, info)
        # (venv 의 python.exe 는 진짜 파이썬을 자식으로 띄우므로 번호가 p.pid 와 다를 수 있다 — 숫자인지만 본다)
        got = open(pidf).read().strip() if os.path.exists(pidf) else ''
        after = open(real_pid).read() if os.path.exists(real_pid) else None
        T.check('번호표: 임시 서버는 --pid-file(임시 폴더)에 쓰고 logs/web.pid 는 건드리지 않음',
                got.isdigit() and after == before,
                f'임시 번호표 {got or "없음"} · logs/web.pid ' + ('그대로' if after == before else '바뀜'))
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        logf.close()


def web_checks(mock, base, info):
    T.check('/api/info — 이 프로그램 · 색인 · LLM 준비 · 예시 질문',
            info.get('app') == 'MAI_SMA_student' and info.get('docs') == INDEX['N'] and info.get('docs_ref') == INDEX['ref']
            and info.get('db') == DB_NAME and info.get('ready') is True
            and len(info.get('samples') or []) >= 10 and info.get('model') == 'mock-model',
            f"docs {info.get('docs')}(참고 {info.get('docs_ref')}) · ready {info.get('ready')} · "
            f"예시 {len(info.get('samples') or [])} · {info.get('model')}")
    t0 = time.time()
    while time.time() - t0 < 15 and not any(c.get('body', {}).get('max_tokens') == 1 for c in mock.posts()):
        time.sleep(0.3)
    warm = [c for c in mock.posts() if c['body'].get('max_tokens') == 1]
    T.check('접두부 예열(기동 직후 조건 F 앞부분 1회, max_tokens 1)', len(warm) == 1)
    T.check('상태 확인 GET {base_url}/models', any(c['method'] == 'GET' and c['path'].endswith('/models') for c in mock.calls))
    html = http_json(base + '/', timeout=10)
    T.check('화면(/) 응답', isinstance(html, str) and 'MAI SMA' in html and '학생 이식판' in html)
    st = http_json(base + '/api/settings', timeout=20)
    rt, md, mem, dc = st.get('runtime', {}), st.get('model', {}), st.get('mem', {}), st.get('dict', {})
    T.check('/api/settings — 설정값(실행기 · 주소 · 요청 필드) · 사전 · 메모리',
            rt.get('preset') == 'Ollama' and rt.get('model') == 'mock-model' and 'top_k' not in (rt.get('request') or {})
            and md.get('source') == 'config' and dc.get('ok') and dc.get('n', 0) >= 121
            and rt.get('docs') == INDEX['N'] and rt.get('docs_ref') == INDEX['ref'] and rt.get('db') == DB_NAME
            and ('sys_total_gb' in mem or 'psutil' in str(mem.get('note', ''))),
            f"요청 필드 {sorted((rt.get('request') or {}))} · 메모리 {mem.get('sys_total_gb', mem.get('note', ''))}")
    dct = http_json(base + '/api/dict', timeout=20)
    T.check('/api/dict — 사전 원문 · 용어 대응표', dct.get('ok') and len(dct.get('cols', [])) >= 121 and len(dct.get('terms', [])) == 82)

    n0 = len(mock.posts())
    r = http_json(base + '/api/ask', {'q': Q_AGG, 'rid': 'selftest1'}, timeout=60)
    last = mock.posts()[-1]['body'] if len(mock.posts()) > n0 else {}
    import build_prompt100 as BP
    sent = (last.get('messages') or [{}])[0].get('content')
    want = (BP.HEAD + BP.SC + '\n' + BP.RULES + BP.FEWSHOT + BP.TAIL).replace('{q}', Q_AGG)
    T.check('집계형: 라우터 AGG → SQL 생성 → DuckDB 실행 → 표·답 문장',
            r.get('kind') == 'table' and r.get('route') == 'AGG' and r.get('rows') == EXPECT[Q_AGG]
            and bool(r.get('answer')) and any(s['table'] == 'pms' for s in r.get('src') or []),
            f"{r.get('rows')} · {r.get('answer')}")
    T.check('집계형: 보낸 글 = 조건 F 전문(질문만 바뀜) · 요청 필드(ollama)', sent == want
            and last.get('model') == 'mock-model' and last.get('seed') == 42 and last.get('max_tokens') == 600
            and 'top_k' not in last, f'{len(sent or ""):,}자')
    r = http_json(base + '/api/ask', {'q': Q_ABS}, timeout=60)
    T.check('기권: NO_RELIABLE_VALUE → SQL 을 실행하지 않고 사유 표시',
            r.get('kind') == 'abstain' and r.get('token') == 'NO_RELIABLE_VALUE' and bool(r.get('why')), r.get('head', ''))
    r = http_json(base + '/api/ask', {'q': Q_THINK}, timeout=60)
    T.check('사고형 출력(<think>…)을 걷어 내고 SQL 실행', r.get('kind') == 'table'
            and r.get('rows') == EXPECT['선박은 몇 척인가?'],
            str(r.get('rows') or r.get('msg')))
    r = http_json(base + '/api/ask', {'q': Q_PROC_EN, 'rid': 'selftest2'}, timeout=120)
    prov = r.get('prov') or []
    T.check('절차형(영어): 번역 없이 BM25 → 기록 4건 → JSON → 인용 검증',
            r.get('kind') == 'proc' and r.get('route') == 'PROC' and len(r.get('steps') or []) == 2
            and r.get('verified') == 1 and len(prov) == 4 and (r.get('titles') or [''])[0] == 'L.O purifier overhaul and assembly'
            and not any(s.get('label') == 'Translating question' for s in r.get('stages') or []),
            f"검증 {r.get('verified')}/{len(r.get('steps') or [])} · 1위 기록 {(r.get('titles') or [''])[0]}")
    ok_steps = [s for s in r.get('steps') or [] if s.get('ok')]
    T.check('인용 검증: 원문에 있는 인용만 verified, 지어낸 인용은 unverified',
            len(ok_steps) == 1 and '280 N.m' in ok_steps[0].get('quote', '') and
            any(not s.get('ok') for s in r.get('steps') or []), (ok_steps[0].get('quote', '') if ok_steps else '')[:70])
    T.check('출처 조회(정비 문서 기록): 선박 · 기기 · 호기 · 장비 · 문서 · 정비항목(master·pms) — 원판 그대로',
            bool(prov) and not prov[0].get('ref') and re.fullmatch(r'V0[1-6]', str(prov[0].get('vessel'))) is not None
            and len(str(prov[0].get('equip_code') or '')) == 5 and isinstance(prov[0].get('items'), list),
            f"{prov[0].get('vessel')} · {prov[0].get('mach_code')} · {prov[0].get('equip_code')}" if prov else '')
    r = http_json(base + '/api/ask', {'q': Q_PROC_REF, 'rid': 'selftest3'}, timeout=120)
    prov = r.get('prov') or []
    p0 = prov[0] if prov else {}
    rr = INDEX['ref_rows']
    same = [(no, mach) for no, _t, body, mach in rr if body == rr[0][2]]      # 1위 기록과 본문이 같은 P063 의 기록
    want_q = [f'{REF_QID}-{no}' for no, _m in same]
    want_n = [no for no, _m in same]
    units = sorted({int(m.group(1)) for _no, mach in same for m in [re.search(r'\s(\d+)$', str(mach))] if m})
    T.check(f'절차형(참고 기록): {REF_QID} 영어 질문 → qa_proc_context 기록 검색 → 인용 검증',
            r.get('kind') == 'proc' and r.get('verified', 0) >= 1 and p0.get('ref') is True
            and (r.get('bodies') or {}).get('1') == rr[0][2],
            f"검증 {r.get('verified')}/{len(r.get('steps') or [])} · 1위 {p0.get('doc')} {str(p0.get('title'))[:40]}")
    grp = (p0.get('groups') or [{}])[0]
    machs = p0.get('machs') or [{}]
    T.check('출처 조회(참고 기록): qa_proc_context 의 제목 · 기기 · 호기 · 날짜 · 문항ID(본문이 같은 기록 모두)',
            p0.get('src') == 'qa_proc_context' and p0.get('title') == rr[0][1] and p0.get('doc') == f'{REF_QID}-1'
            and bool(want_q) and all(x in (p0.get('qids') or []) for x in want_q) and bool(p0.get('mach'))
            and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(p0.get('date'))) is not None and p0.get('items') == [],
            f"{p0.get('mach')} {p0.get('mach_no')} · {p0.get('date')} · 문항 {p0.get('qids')}")
    T.check('출처 표(참고 기록): 문항별 묶음(P063 · records 1–4) · 같은 본문 기록의 호기를 모두(1–4)',
            grp.get('qid') == REF_QID and grp.get('recs') == want_n and len(want_n) > 1
            and grp.get('recs_text') == f'{want_n[0]}–{want_n[-1]}'
            and machs[0].get('units') == (f'{units[0]}–{units[-1]}' if len(units) > 1 else str(units[0])),
            f"묶음 {grp.get('qid')} · records {grp.get('recs_text')} · {machs[0].get('name')} {machs[0].get('units')}")
    html = http_json(base + '/', timeout=10)
    T.check('화면: 참고 기록 전용 머리행(Fixed context of) · 정비 문서 기록의 합성 서술 표시(synthetic narrative)',
            isinstance(html, str) and 'Fixed context of' in html and 'synthetic narrative' in html
            and 'headRef' in html)
    r = http_json(base + '/api/ask', {'q': Q_PROC_KO}, timeout=120)
    T.check('절차형(한국어): 용어 대응 → 번역 → 검색어 보강 → 인용 검증',
            r.get('kind') == 'proc' and r.get('query_raw') == T_PROC_KO and r.get('verified', 0) >= 1
            and any(t.get('en') == 'TURBO CHARGER' for t in r.get('terms') or [])
            and (r.get('titles') or [''])[0] == 'D/G turbocharger water washing'
            and any(s.get('label') == 'Translating question' for s in r.get('stages') or []),
            f"1위 기록 {(r.get('titles') or [''])[0]} · 검색어 {str(r.get('query'))[:60]}…")
    hist = [{'q': Q_AGG, 'a': ' · '.join(f'{u}호기 {n}' for u, n in EXPECT[Q_AGG])}]
    r = http_json(base + '/api/ask', {'q': Q_FOLLOW, 'hist': hist}, timeout=60)
    T.check('후속 질문: 직전 대화로 다시 쓴 뒤 같은 경로(재작성은 답변 프롬프트에 섞지 않음)',
            r.get('asked') == Q_FOLLOW and r.get('resolved') == Q_AGG2 and r.get('kind') == 'table'
            and r.get('rows') == EXPECT[Q_AGG2], f"→ {r.get('resolved')} · {r.get('rows')}")
    r = http_json(base + '/api/ask', {'q': Q_AGG_EN, 'bi': True}, timeout=60)
    T.check('한국어 병기(bi): 질문 번역 + 한국어 답 문장(모델 없이 조립)',
            r.get('kind') == 'table' and bool(r.get('q_ko')) and bool(r.get('answer_ko')), f"{r.get('q_ko')} / {r.get('answer_ko')}")
    r = http_json(base + '/api/ask', {'q': 'ERROR-404-TEST 정비 문서는 몇 건인가?'}, timeout=60)
    T.check('LLM 오류(404 모델 없음) → 원인과 조치를 알림', r.get('kind') == 'error' and '모델' in r.get('msg', ''),
            r.get('msg', '')[:90])
    r = http_json(base + '/api/ask', {'q': 'BADJSON-TEST 청정기 분해는 어떻게 하나?'}, timeout=120)
    T.check('절차형 출력 형식 오류 → 한 번 다시 묻고 원문 표시(badfmt)', r.get('kind') == 'badfmt', r.get('kind'))
    res, t0 = {}, time.time()

    def slow():
        res['r'] = http_json(base + '/api/ask', {'q': 'SLOW-TEST 정비 문서는 몇 건인가?', 'rid': 'selftest-stop'}, timeout=60)
    th = threading.Thread(target=slow)
    th.start()
    time.sleep(1.5)
    http_json(base + '/api/cancel?id=selftest-stop', timeout=10)
    th.join(30)
    T.check('중단: /api/cancel 이 LLM 연결을 닫고 곧바로 끝냄', (res.get('r') or {}).get('kind') == 'stopped'
            and time.time() - t0 < 12, f'{time.time() - t0:.1f}초')
    pg = http_json(base + '/api/progress?id=nothing', timeout=10)
    T.check('/api/progress', pg == {})


# ───────────────────────── E. 터미널판 · 점검 도구 ─────────────────────────
def sec_e(mock, tmp):
    T.section('E. 터미널판 · LLM 점검 도구(모의 LLM)')
    cfgp = os.path.join(tmp, 'cli.json')
    write_cfg(cfgp, mock, warmup=False)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    r = subprocess.run([PY, '-B', os.path.join(APP, 'maintqa_cli.py'), '--config', cfgp, '-q', Q_AGG], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=120)
    T.check('터미널판 집계형(maintqa_cli.py -q)', r.returncode == 0 and all(n in r.stdout for _u, n in EXPECT[Q_AGG]),
            r.stdout.strip()[-60:].replace('\n', ' '))
    r = subprocess.run([PY, '-B', os.path.join(APP, 'maintqa_cli.py'), '--config', cfgp, '-q', Q_PROC_EN], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=120)
    T.check('터미널판 절차형(평가기와 같은 한국어 단계 템플릿 · 인용 검증)', r.returncode == 0 and '인용 검증 1/2' in r.stdout,
            (re.search(r'인용 검증 \d+/\d+', r.stdout) or [''])[0])
    r = subprocess.run([PY, '-B', os.path.join(HERE, 'check_llm.py'), '--config', cfgp, '--quick'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=60)
    T.check('check_llm.py --quick(START.bat 이 쓰는 점검)', r.returncode == 0, r.stdout.strip().splitlines()[-1][:80] if r.stdout.strip() else r.stderr[-80:])
    r = subprocess.run([PY, '-B', os.path.join(HERE, 'check_llm.py'), '--config', cfgp, '--no-report'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=300)
    T.check('check_llm.py 전체(대화 · 문맥 길이 · SQL · 기권 · 인용 JSON)', r.returncode == 0,
            r.stdout.strip().splitlines()[-1][:80] if r.stdout.strip() else r.stderr[-80:])
    bad = os.path.join(tmp, 'bad.json')
    write_cfg(bad, mock, base_url='http://127.0.0.1:9/v1')
    r = subprocess.run([PY, '-B', os.path.join(HERE, 'check_llm.py'), '--config', bad, '--quick'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=60)
    T.check('응답 없는 주소 → 코드 11 과 켜는 방법 안내', r.returncode == 11 and 'Ollama' in r.stdout, f'코드 {r.returncode}')


# ───────────────────────── F. 평가기 ─────────────────────────
def sec_f(mock, tmp, quick):
    T.section('F. 평가기(eval/) — 1,000건 DB 기준 채점기')
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if quick:
        T.note('--quick: eval/run_eval.py --self-test 를 건너뛰었다')
    else:
        r = subprocess.run([PY, '-B', os.path.join(PKG, 'eval', 'run_eval.py'), '--self-test'], cwd=os.path.join(PKG, 'eval'),
                           capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=900)
        last = [ln for ln in r.stdout.splitlines() if ln.startswith('결과:')]
        T.check('eval/run_eval.py --self-test(집계형·절차형 채점기 · 호출 경로 · 배포 점검)', r.returncode == 0,
                last[-1] if last else r.stderr[-120:])
    cfgp = os.path.join(tmp, 'eval.json')
    write_cfg(cfgp, mock, preset='lmstudio', model='mock-model')
    out = os.path.join(tmp, 'results')
    r = subprocess.run([PY, '-B', os.path.join(PKG, 'eval', 'eval_from_config.py'), '--config', cfgp, '--task', 'agg',
                        '--ids', 'Q001-Q003,Q070', '--out-dir', out, '--tag', 'selftest_agg'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=300)
    summ = os.path.join(out, 'selftest_agg_summary.json')
    s = json.load(open(summ, encoding='utf-8')) if os.path.exists(summ) else {}
    body = next((c['body'] for c in reversed(mock.posts()) if 'top_k' in c['body']), {})
    T.check('eval_from_config.py → run_eval.py 집계형(config 의 주소·모델·프리셋으로)',
            r.returncode == 0 and s.get('strict') == 4 and body.get('model') == 'mock-model' and body.get('top_k') == 1,
            f"엄격 {s.get('strict')}/4 · 요청 필드 {sorted(k for k in body if k != 'messages')}")
    r = subprocess.run([PY, '-B', os.path.join(PKG, 'eval', 'eval_from_config.py'), '--config', cfgp, '--task', 'proc',
                        '--ids', 'P004', '--out-dir', out, '--tag', 'selftest_proc'], cwd=PKG,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=300)
    sp = os.path.join(out, 'selftest_proc_summary.json')
    s2 = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
    T.check('eval_from_config.py → run_eval.py 절차형(고정 문맥 · 인용 대조)', r.returncode == 0 and s2.get('steps', 0) >= 1,
            f"단계 {s2.get('steps')} · 인용정확 {s2.get('exact')}")
    try:
        import openpyxl  # noqa: F401
        has_xl = True
    except ImportError:
        has_xl = False
    if not has_xl:
        T.note('openpyxl 이 없어 엑셀 기록양식 채우기 점검을 건너뛰었다(python -m pip install openpyxl)')
        return
    xo = os.path.join(tmp, 'form.xlsx')
    r = subprocess.run([PY, '-B', os.path.join(PKG, 'eval', 'fill_record_form.py'),
                        os.path.join(out, 'selftest_agg.csv'), os.path.join(out, 'selftest_proc.csv'), '--out', xo],
                       cwd=PKG, capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=120)
    ok = False
    if r.returncode == 0 and os.path.exists(xo):
        import openpyxl
        wb = openpyxl.load_workbook(xo, read_only=True)
        try:      # 읽기 전용 통합 문서는 닫아야 파일이 풀린다(윈도에서는 안 닫으면 임시 폴더를 지우지 못한다)
            rows = {row[3]: row for row in wb['⑥ChatGPT_기록양식'].iter_rows(min_row=2, values_only=True)}
        finally:
            wb.close()
        q1, p4 = rows.get('Q001') or (), rows.get('P004') or ()
        ok = len(q1) > 9 and q1[0] == 'mock-model' and q1[9] == 'O' and len(p4) > 9 and str(p4[9]).startswith('인용정확')
    T.check(f'fill_record_form.py → {FORM_NAME} ⑥ 기록양식에 결과 채움', ok, r.stdout.strip()[-80:])


# ───────────────────────── G. 배포 점검 ─────────────────────────
def sec_g():
    T.section('G. 배포 점검')
    bad_names = re.compile(r'(PRIVATE|secret_seed|code_map|deid_rules|^maint\.duckdb$|^demo\.duckdb$|^bm25\.pkl$|'
                           r'^bm25_demo\.pkl$|^deid_maint\.duckdb$|^bm25_deid\.pkl$|^정답샘플_200문항\.xlsx$|'
                           r'^answer_sample_200\.xlsx$|\.gguf$)', re.I)
    pats = [re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+', re.I),
            re.compile(r'(?<![\w.])/(?:home|Users)/[\w.-]+/', re.I)]
    names, caches, paths = [], [], []
    for dp, dn, fn in os.walk(PKG):
        # 학생이 만든 기록·결과(logs · eval/results · eval/responses)는 배포 점검에서 뺀다(평가를 돌린 뒤에도 통과해야 한다)
        dn[:] = [d for d in dn if d not in ('logs', 'results', 'responses')]
        for d in dn:
            if d == '__pycache__':
                caches.append(os.path.relpath(os.path.join(dp, d), PKG))
        for f in fn:
            p = os.path.join(dp, f)
            if bad_names.search(f):
                names.append(os.path.relpath(p, PKG))
            if f.endswith('.pyc'):
                caches.append(os.path.relpath(p, PKG))
            if f.lower().endswith(('.py', '.md', '.txt', '.json', '.bat', '.sh', '.html', '.csv')):
                s = open(p, encoding='utf-8', errors='replace').read()
                if any(x.search(s) for x in pats):
                    paths.append(os.path.relpath(p, PKG))
    T.check('실제 DB·17만 행 키트 DB·실제/키트 색인·키트 정답 엑셀·열쇠·모델 파일 없음', not names,
            ', '.join(names[:5]) or '0개')
    T.check('__pycache__ · .pyc 없음', not caches, ', '.join(caches[:5]) or '0개')
    T.check('텍스트 파일에 사용자 폴더 절대경로 없음', not paths, ', '.join(paths[:5]) or '0개')


def main():
    quick = '--quick' in sys.argv
    t0 = time.time()
    print(f'MAI SMA 학생 이식 패키지 — 자체 점검 (python {sys.version.split()[0]})')
    if not sec_a():
        print('\n결과: duckdb 가 없거나 판이 맞지 않아 멈췄다(필요: 1.5 이상 2 미만).')
        print('selftest FAIL — 판을 맞춘 뒤 다시 실행하십시오: python -m pip install "duckdb>=1.5,<2"')
        return 1
    for name, fn in (('B', sec_b), ('C', sec_c)):
        guarded(f'{name} 절 실행')(fn)()
    mock = MockLLM()
    tmp = tempfile.mkdtemp(prefix='sma_selftest_')
    try:
        guarded('D 절 실행')(sec_d)(mock, tmp)
        guarded('E 절 실행')(sec_e)(mock, tmp)
        guarded('F 절 실행')(sec_f)(mock, tmp, quick)
    finally:
        mock.close()
        shutil.rmtree(tmp, ignore_errors=True)
    guarded('G 절 실행')(sec_g)()
    n_pass = sum(1 for s, *_ in T.items if s == 'PASS')
    n_fail = sum(1 for s, *_ in T.items if s == 'FAIL')
    n_note = sum(1 for s, *_ in T.items if s == 'NOTE')
    print(f'\n결과: {n_pass}/{n_pass + n_fail} PASS' + (f' · FAIL {n_fail}' if n_fail else '')
          + (f' · 참고 {n_note}' if n_note else '') + f' · {time.time() - t0:.0f}초')
    if n_fail == 0:
        print('selftest PASS — 패키지가 제대로 놓였습니다. 다음은 tools/check_llm.py 로 실제 LLM 을 점검하십시오.')
    else:
        print(f'selftest FAIL — 실패 {n_fail}개. 위의 [FAIL] 줄을 확인하십시오(00_먼저_읽기.md 5절의 흔한 오류).')
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
