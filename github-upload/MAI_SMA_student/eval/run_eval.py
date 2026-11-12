# -*- coding: utf-8 -*-
"""MAI SMA 학생판 평가기 (eval/run_eval.py) — 학생용 DB(정비 문서 1,000건) 기준

비식별화 시험 키트의 평가기(키트판 VERSION 2026-11-10)를 학생 패키지에 맞게 고친 것이다. 채점 규칙과
OpenAI 호환 호출은 키트판과 글자까지 같다. 바뀐 것은 정답 기준 DB 와 파일 구성뿐이다.
  · 평가 DB = db/MAI_SMA_학생용DB_1000.duckdb(정비 문서 1,000건 + 200문항). 정답은 키트판과 같이 정답 SQL 을
    이 DB 에서 실행한 결과다. questions/aggregate100.jsonl 의 gold_result 도 이 DB 기준이다.
  · 집계형 프롬프트는 템플릿(prompts/cond_A·C·F.txt)에 질문을 넣어 만든다(키트판의 1,200개 JSONL 과 같은 글).
  · 조건 P·S, 대조판(asrun), 배포 묶음(--pack)은 뺐다. 엑셀 기록양식은 기록양식_200문항.xlsx.
  · 결과 파일의 모델 id(version · model_returned)는 파일 이름만 남긴다. llama-server 는 GGUF 의 전체 경로를
    id 로 돌려주기 때문이다(chat 함수는 그대로 두고 _chat_student 로 감쌌다).
  · 자체 검사의 배포 점검은 학생이 만든 results/ · responses/ · logs/ 를 보지 않는다.

원 시스템과 같은 두 경로를 다른 컴퓨터에서 그대로 재현한다. 모델만 바꿔 끼운다.

  집계형 (--task agg)   완성 프롬프트 ─▶ 모델 ─▶ SQL 또는 기권 토큰 추출 ─▶ 키트 DB(읽기 전용) 실행 ─▶ 정답 결과와 비교
  절차형 (--task proc)  고정 검색 문맥 4건 + 질문 ─▶ 모델 ─▶ JSON(steps·missing) 파싱 ─▶ 인용구를 문맥 본문과 문자 대조

채점 규칙은 원 벤치마크 코드와 같다(바꾸지 않았다).
  집계형  bench/run100.py 의 clean, bench/harness.py 의 run·norm·compare·repair_dialect
          (tool/harness_kit.py 를 그대로 import 한다)
  절차형  bench/proc_run.py 의 JSON 파싱·exact·anywhere·MEAS, bench/rag.py 의 sq,
          bench/proc_metrics.py 의 json_valid·step_yield·abstain·gen_fail·PLACEHOLDER·질문 복창 판정

사용 예
  python run_eval.py --self-test                                  채점기·키트 자체 검사(외부 호출 없음)
  python run_eval.py --dry-run --cond F --phrasing NL --ids Q001  완성 프롬프트만 출력(호출 없음)
  python run_eval.py --task agg --cond F --phrasing NL --model gpt-4.1 --ids Q001-Q005
  python run_eval.py --task proc --model gpt-4.1 --ids P001-P005
  python run_eval.py --score-file 응답.csv                        손으로 모은 응답 채점(엑셀 ⑥ 기록양식과 같은 열)
  python run_eval.py --score-dir responses --model ChatGPT-웹      문항ID 이름의 응답 파일(Q001.txt …) 채점
  python run_eval.py --task agg --base-url http://127.0.0.1:<포트>/v1 --cond F --phrasing NL   로컬 llama-server

연결 설정: 환경변수 OPENAI_BASE_URL(기본 https://api.openai.com/v1) · OPENAI_API_KEY · OPENAI_MODEL,
           또는 --base-url · --api-key · --model.
필요: Python 3.8 이상, duckdb(pip install duckdb). .xlsx 응답 파일을 읽을 때만 openpyxl.
결과: results/ 폴더(바꾸려면 --out-dir). 자세한 안내는 00_평가_안내.md.
패키지의 config.json 으로 돌리려면 eval_from_config.py 를 쓴다(주소·모델·프리셋을 옮겨 적지 않는다).
"""
import sys

sys.dont_write_bytecode = True   # tool/ 에 __pycache__ 를 남기지 않는다(.pyc 에는 이 컴퓨터의 절대경로가 들어간다)

import argparse  # noqa: E402
import collections  # noqa: E402
import csv  # noqa: E402
import datetime  # noqa: E402
import email.utils  # noqa: E402
import hashlib  # noqa: E402
import http.client  # noqa: E402
import http.server  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import statistics  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
import zipfile  # noqa: E402

try:   # 파일로 돌려 받아도(> 파일) 줄바꿈이 \n 그대로 — --dry-run 출력이 프롬프트 파일과 바이트 단위로 같게
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', newline='\n')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

KIT = os.path.dirname(os.path.abspath(__file__))


def kp(*parts):
    """키트 폴더 기준 경로. 키트 안에는 절대경로를 적지 않는다."""
    return os.path.join(KIT, *parts)


try:
    import duckdb
except ImportError:
    sys.exit('duckdb 패키지가 없다. 먼저 설치한다:  pip install duckdb')

sys.path.insert(0, kp('tool'))
try:
    from harness_kit import ABSTAIN, compare, is_abstain, repair_dialect  # noqa: E402
    from harness_kit import run as harness_run  # noqa: E402
except ImportError as _e:
    sys.exit(f'tool/harness_kit.py 를 읽지 못했다({_e}). 키트 폴더를 통째로 복사했는지 확인한다.')

VERSION = '2026-11-10-s1000'          # 키트판 2026-11-10 + 학생판(1,000건 DB)

# ═══════════════════════ 키트 파일 ═══════════════════════
DEFAULT_DB = kp('db', 'MAI_SMA_학생용DB_1000.duckdb')     # 학생판: 정비 문서 1,000건 + 200문항 DB
F_AGG_Q = kp('questions', 'aggregate100.jsonl')
F_PROC_Q = kp('questions', 'procedural100.jsonl')
F_CTX = kp('procedural', 'contexts_deid.jsonl')
F_PROC_PROMPTS = kp('prompts', 'proc_prompts.jsonl')
F_PROC_TEMPLATE = kp('prompts', 'proc_answer_template.txt')
F_PROC_HASH = kp('procedural', 'input_hash.txt')
F_XLSX_KO = kp('기록양식_200문항.xlsx')                 # 학생판: 1,000건 DB 의 qa 표로 만든 기록양식
F_XLSX_ASCII = kp('record_form_200.xlsx')         # ASCII 이름 사본(학생판에는 없다 — 있으면 쓴다)
F_XLSX = F_XLSX_KO if os.path.exists(F_XLSX_KO) or not os.path.exists(F_XLSX_ASCII) else F_XLSX_ASCII
SHEET6 = '⑥ChatGPT_기록양식'

# 학생판: 집계형 프롬프트는 템플릿에 질문을 넣어 만든다(키트판 aggregate_prompts.jsonl 과 같은 글).
TEMPLATE_FILE = {('kit', 'A'): 'prompts/cond_A.txt', ('kit', 'C'): 'prompts/cond_C.txt',
                 ('kit', 'F'): 'prompts/cond_F.txt'}
AGG_PROMPT_FILE = dict(TEMPLATE_FILE)
FORMS = ['orig', 'L2', 'L1', 'NL']
FORM_ALIAS = {'orig': 'orig', '원판': 'orig', 'l2': 'L2', 'l1': 'L1', 'nl': 'NL', '자연어': 'NL'}
KINDS = ['절차', '판정기준', '이상소견', '부품']
DIFFS = [('easy', '쉬움'), ('medium', '중간'), ('hard', '어려움')]


def _recorded_hash():
    """빌드가 procedural/input_hash.txt 에 남긴 비식별 문맥의 입력 해시(proc_run.py 방식)."""
    try:
        with open(F_PROC_HASH, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


EXPECTED_PROC_HASH = _recorded_hash()
EXPECTED_ROWS = {'master': 1000, 'pms': 2296, 'consumption': 174, 'attachments': 286, 'approval': 2979, 'vessels': 6, 'qa_aggregate': 100, 'qa_procedural': 100, 'qa_proc_context': 400}   # 학생판 DB(정비 문서 1,000건)
OWN_OUTPUT_DIRS = ('results', 'responses', 'logs')   # 학생판: 학생이 만든 결과 폴더 — 배포 점검에서 뺀다
DEFAULT_MAX_TOKENS = {'agg': 400, 'proc': 2200}   # 원 실험: run100.py ask 400, rag.py llm 2200
DEFAULT_TIMEOUT = {'agg': 300, 'proc': 600}
MK = {'match': 'O', 'mismatch': 'X', 'error': 'E'}

# ═══════════════════════ 원 규칙 — 바꾸지 않는다 ═══════════════════════
# bench/proc_run.py
MEAS = re.compile(r'\d+\.\d+|\d+\s*(?:mm|bar|mmaq|Ω|MΩ|%|nm|kg|℃|rpm|시간|분|초|hrs|hours)', re.I)
FRAG = re.compile(r'\{[^{}]*?"quote"\s*:\s*"(?:[^"\\]|\\.)*"[^{}]*?\}')
# bench/proc_metrics.py
PLACEHOLDER = re.compile(
    r'^\s*(nothing|none|n/?a|empty(\s+string)?(\s+if\s+nothing)?|null|-+|<[^>]*>|\.\s*'
    r'|what\s+the\s+records?\s+do\s+not\s+cover'
    r'(\s*;?\s*empty\s+string(\s+if\s+nothing)?\s*\.?)?'
    r')\s*[\.\s]*$', re.I)


def sq(s):
    """bench/rag.py sq — 공백 정규화·소문자화."""
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def _norm(s):
    """bench/proc_metrics.py _norm."""
    return re.sub(r'[\s\.;,]+', ' ', (s or '').strip().lower()).strip()


def is_question_echo(missing, *questions):
    """bench/proc_metrics.py is_question_echo — 사유 자리에 질문(물음표로 끝남)을 베낀 경우."""
    m = _norm(missing)
    if not m or not missing.strip().endswith('?'):
        return False
    return any(m in _norm(q) for q in questions if q)


def clean(s):
    """bench/run100.py clean — 코드펜스 제거 → 기권 토큰 완전일치 → 첫 SELECT/WITH 부터 첫 ; 까지."""
    s = re.sub(r'^\s*```(?:sql)?\s*', '', s.strip(), flags=re.I)
    s = re.sub(r'```\s*$', '', s.strip()).strip()
    if is_abstain(s):
        return s.strip().strip('`').strip().rstrip(';').strip().upper()
    m = re.search(r'(SELECT|WITH)\b', s, re.I)
    if m:
        s = s[m.start():]
    if ';' in s:
        s = s[:s.index(';') + 1]
    return s.strip()


def _as_text(v):
    """원 코드는 quote·step·missing 을 문자열로 가정하고, null·숫자·목록이 오면 멈춘다.
    그 경우에만 글자로 바꿔 계속 채점한다(원 코드가 정상 처리하던 입력의 판정은 그대로)."""
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def parse_proc(raw):
    """bench/proc_run.py 와 같은 순서의 파싱. 반환: steps, parsed(원 정의), missing."""
    txt = re.sub(r'```\w*', '', raw).strip()
    m = re.search(r'\{.*\}', txt, re.S)
    parsed = True
    try:
        obj = json.loads(m.group(0)) if m else {}
    except Exception:
        obj, parsed = {}, False
    steps = obj.get('steps', []) if isinstance(obj, dict) else []
    if not steps:
        parsed = False
        steps = []          # 원 코드는 "steps": null 이면 여기서 멈춘다 — 빈 목록으로 받아 계속한다
        for mm in FRAG.finditer(txt):
            try:
                steps.append(json.loads(mm.group(0)))
            except Exception:
                pass
    missing = _as_text((obj.get('missing') or '') if isinstance(obj, dict) else '')
    return steps, parsed, missing


def proc_rows(steps, bodies):
    """bench/proc_run.py 의 단계별 판정(exact·anywhere·측정값 근거·감사 위치)."""
    rows = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        try:
            rec = int(str(s.get('record')).strip())
        except (TypeError, ValueError):
            rec = None
        quote, stx = _as_text(s.get('quote', '')), _as_text(s.get('step', ''))
        body = bodies.get(str(rec), '')
        qs, bs = sq(quote), sq(body)
        exact = bool(quote and qs and qs in bs)
        anywhere = any(qs and qs in sq(b) for b in bodies.values())
        nums = MEAS.findall(stx)
        qn = re.sub(r'[,\s]', '', quote).lower()
        hit_rec, hit_pos = (rec, bs.find(qs)) if exact else (None, None)
        if not exact and qs:
            for k, b in bodies.items():
                pos = sq(b).find(qs)
                if pos >= 0:
                    hit_rec, hit_pos = int(k), pos
                    break
        rows.append(dict(exact=exact, anywhere=anywhere, has_num=bool(nums),
                         num_ok=(all(re.sub(r'[,\s]', '', x).lower() in qn for x in nums) if nums else None),
                         step=stx, quote=quote, claimed_rec=rec, matched_rec=hit_rec, match_pos=hit_pos,
                         nums=nums,
                         nums_missing=[x for x in nums if re.sub(r'[,\s]', '', x).lower() not in qn]))
    return rows


def parse_raw(raw):
    """bench/proc_metrics.py parse_raw — 진짜 JSON 객체로 파싱되면 dict, 아니면 None."""
    txt = re.sub(r'```\w*', '', raw).strip()
    m = re.search(r'\{.*\}', txt, re.S)
    try:
        obj = json.loads(m.group(0)) if m else None
    except Exception:
        obj = None
    return obj if isinstance(obj, dict) else None


def classify(raw, n_steps, *questions):
    """bench/proc_metrics.py classify — steps / abstain(의도적 기권) / gen_fail(생성 실패)."""
    obj = parse_raw(raw)
    valid = obj is not None
    if n_steps > 0:
        return dict(kind='steps', json_valid=valid, reason='')
    miss = _as_text((obj.get('missing') or '') if valid else '')
    real = (miss.strip() and not PLACEHOLDER.match(miss.strip())
            and not is_question_echo(miss, *questions))
    if valid and real:
        return dict(kind='abstain', json_valid=True, reason=miss.strip()[:200])
    return dict(kind='gen_fail', json_valid=valid, reason=miss.strip()[:200])


def proc_input_hash(ctx_rows):
    """bench/proc_run.py 의 입력 해시 — {원 id: 문맥 전문} 의 sha1 앞 16자리."""
    return hashlib.sha1(json.dumps({str(c['orig_id']): c['ctx'] for c in ctx_rows},
                                   sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ═══════════════════════ 키트 자료 읽기 ═══════════════════════
_CACHE = {}


def _cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def load_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(x) for x in f if x.strip()]


def AGG():
    return _cached('agg', lambda: {r['id']: r for r in load_jsonl(F_AGG_Q)})


def PROC():
    return _cached('proc', lambda: {r['id']: r for r in load_jsonl(F_PROC_Q)})


def CTX():
    return _cached('ctx', lambda: {r['id']: r for r in load_jsonl(F_CTX)})


def PROC_PROMPTS():
    return _cached('pp', lambda: {(r['id'], r['lang']): r for r in load_jsonl(F_PROC_PROMPTS)})


def agg_prompt_index(variant, cond):
    """{(문항ID, 조건, 문형): {'prompt': …}}. 학생판: 템플릿.format(q=질문) 으로 만든다(원 실행 run100.py 와 같은 방법)."""
    f = TEMPLATE_FILE.get((variant, cond))
    if f is None:
        ok = sorted(c for v, c in TEMPLATE_FILE if v == variant)
        raise SystemExit(f'판 {variant} 에는 조건 {cond} 가 없다. 쓸 수 있는 조건: {", ".join(ok)}')

    def build():
        with open(kp(*f.split('/')), encoding='utf-8') as fh:
            tmpl = fh.read()
        return {(qid, cond, form): dict(id=qid, num=r['num'], cond=cond, form=form, variant=variant,
                                        prompt=tmpl.format(q=q))
                for qid, r in AGG().items() for form, q in r['questions'].items()}
    return _cached(('aggp', f), build)


def sha1(s):
    return hashlib.sha1(s.encode('utf-8')).hexdigest()


# ═══════════════════════ 키트 DB 와 SQL 실행 ═══════════════════════
def open_kit_db(path):
    """읽기 전용 + 외부 파일·네트워크 접근 차단 + 설정 잠금. 모델이 낸 SQL 을 그대로 실행하기 때문이다."""
    if not os.path.exists(path):
        raise SystemExit(f'키트 DB 가 없다: {path}')
    try:
        con = duckdb.connect(path, read_only=True, config={'enable_external_access': False})
        hardened = True
    except Exception:
        con = duckdb.connect(path, read_only=True)
        hardened = False
    try:
        con.execute('SET lock_configuration = true')
    except Exception:
        pass
    if not hardened:
        print('  [주의] 이 duckdb 버전에서는 외부 접근 차단을 켜지 못했다(읽기 전용으로만 연다).', file=sys.stderr)
    return con


class SqlRunner:
    """harness.run 을 그대로 쓰되, 시간 제한을 넘기면 실행을 끊는다(원 실험에는 없던 안전장치)."""

    def __init__(self, con, timeout):
        self.con, self.timeout = con, timeout

    def run(self, sql):
        fired = []

        def _stop():
            fired.append(1)
            try:
                self.con.interrupt()
            except Exception:
                pass
        t = threading.Timer(self.timeout, _stop) if self.timeout and self.timeout > 0 else None
        if t:
            t.daemon = True
            t.start()
        try:
            res, err = harness_run(self.con, sql)
        finally:
            if t:
                t.cancel()
        if fired and res is None:
            err = f'SQL 실행 시간 초과({self.timeout:g}초)'
        return res, err


class Kit:
    def __init__(self, db_path=DEFAULT_DB, sql_timeout=120):
        self.db_path = db_path
        self.con = open_kit_db(db_path)
        self.runner = SqlRunner(self.con, sql_timeout)
        self._gold = None

    def gold(self, qid):
        """run100.py 와 같이 정답은 정답 SQL 을 같은 DB 에서 실행한 결과(기권 문항은 토큰)."""
        if self._gold is None:
            g = {}
            for k, r in AGG().items():
                if r['answer_type'] == 'ABSTAIN':
                    g[k] = r['abstain_token']
                else:
                    res, err = self.runner.run(r['gold_sql'])
                    if res is None:
                        raise SystemExit(f'정답 SQL 실행 실패({k}): {err}')
                    g[k] = res
            self._gold = g
        return self._gold[qid]


# ═══════════════════════ 채점 ═══════════════════════
def score_agg(kit, qid, raw, api_error=None):
    q = AGG()[qid]
    g = kit.gold(qid)
    rec = dict(task='agg', qid=qid, num=q['num'], difficulty=q['difficulty'], answer_type=q['answer_type'],
               abstain_expected=q['abstain_token'], rule_label=q['rule_label'],
               rule_label_name=q['rule_label_name'] or '', prompt_rule=q.get('prompt_rule'),
               compare_exclude_forms=q.get('compare_exclude_forms') or [],
               gold_head=g if isinstance(g, str) else (str(g[0])[:40] if g else '(빈)'))
    if api_error is not None:
        rec.update(extracted='', abstained=False, strict='error', norm='error', exec_error=None,
                   pred_head='', pred_rows=None, result_text=f'[API 오류] {api_error[:200]}')
    else:
        out = clean(raw or '')
        if out in ABSTAIN:
            v = compare(g, out)
            rec.update(extracted=out, abstained=True, strict=v, norm=v, exec_error=None,
                       pred_head=out, pred_rows=None, result_text=out)
        else:
            pred, err = kit.runner.run(out)
            strict = compare(g, pred) if pred is not None else 'error'
            out2 = repair_dialect(out)
            if out2 != out:
                p2, _ = kit.runner.run(out2)
                norm = compare(g, p2) if p2 is not None else 'error'
            else:
                norm = strict
            rec.update(extracted=out, abstained=False, strict=strict, norm=norm, exec_error=err,
                       pred_head=((str(pred[0])[:40] if pred else '(빈)') if pred is not None else ''),
                       pred_rows=(len(pred) if pred is not None else None),
                       result_text=(f'[오류] {err}' if pred is None else
                                    str(pred[:5]) + (f' … 총 {len(pred)}행' if len(pred) > 5 else '')))
    notes = []
    if rec['norm'] != rec['strict']:
        notes.append(f"[방언교정 판정 {rec['norm']}]")
    if rec['abstained'] and q['answer_type'] == 'SQL':
        notes.append('[과잉 기권]')
    if q['answer_type'] == 'ABSTAIN' and not rec['abstained'] and api_error is None:
        notes.append('[기권 지시 불이행]')
    rec['mark'] = MK[rec['strict']]
    rec['auto_note'] = ' '.join(notes)
    return rec


def score_proc(pid, raw, api_error=None):
    q, c = PROC()[pid], CTX()[pid]
    raw = raw or ''
    steps, parsed, missing = parse_proc(raw)
    rows = proc_rows(steps, c['bodies'])
    cls = classify(raw, len(rows), q['question_ko'], q['question_en'])
    n = len(rows)
    ex = sum(r['exact'] for r in rows)
    mis = sum(1 for r in rows if not r['exact'] and r['anywhere'])
    hal = sum(1 for r in rows if not r['anywhere'])
    wn = [r for r in rows if r['has_num']]
    nok = sum(1 for r in wn if r['num_ok'])
    lack = bool(missing.strip() and not PLACEHOLDER.match(missing.strip())
                and not is_question_echo(missing, q['question_ko'], q['question_en']))
    if api_error is not None:
        text = f'[API 오류] {api_error[:200]}'
    else:
        text = (f"분류 {cls['kind']} · json_valid {cls['json_valid']} · 단계 {n} · 인용정확 {ex} · "
                f"기록번호 오기 {mis} · 인용 환각 {hal} · 수치근거 {nok}/{len(wn)}"
                + (f' · missing: {missing[:80]}' if missing else ''))
    return dict(task='proc', qid=pid, orig_id=q['orig_id'], kind=q['kind'], category=q.get('category'),
                gold_n=q.get('gold_n'), keep=q.get('keep'), keep_warning=q.get('keep_warning') or '',
                question_ko=q['question_ko'], question_en=q['question_en'],
                parsed=parsed, json_valid=cls['json_valid'], cls=cls['kind'], reason=cls['reason'],
                n_steps=n, n_exact=ex, n_misrec=mis, n_halluc=hal, n_num=len(wn), n_num_ok=nok,
                missing=missing, lack=lack, rows=rows, result_text=text,
                mark=(f'인용정확 {ex}/{n}' if n else cls['kind']), auto_note='')


# ═══════════════════════ 요약 ═══════════════════════
def _pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


def _timing(recs):
    secs = [r['sec'] for r in recs if isinstance(r.get('sec'), (int, float))]
    if not secs:
        return None
    return dict(n=len(secs), mean=round(statistics.mean(secs), 2), median=round(statistics.median(secs), 2),
                max=round(max(secs), 2), total_min=round(sum(secs) / 60, 1))


def _tokens(recs):
    pt = [r['prompt_tokens'] for r in recs if isinstance(r.get('prompt_tokens'), int)]
    ct = [r['completion_tokens'] for r in recs if isinstance(r.get('completion_tokens'), int)]
    return dict(prompt=sum(pt), completion=sum(ct), n=len(pt)) if pt or ct else None


def _excluded(r):
    forms = r.get('compare_exclude_forms') or []
    ph = r.get('phrasing') or ''
    return (ph in forms) if ph in FORMS else bool(forms)


def agg_stats(recs):
    n = len(recs)
    s = dict(task='agg', items=n,
             strict=sum(r['strict'] == 'match' for r in recs),
             norm=sum(r['norm'] == 'match' for r in recs))
    s['strict_pct'], s['norm_pct'] = _pct(s['strict'], n), _pct(s['norm'], n)
    s['by_difficulty'] = {d: dict(strict=sum(1 for r in recs if r['difficulty'] == d and r['strict'] == 'match'),
                                  norm=sum(1 for r in recs if r['difficulty'] == d and r['norm'] == 'match'),
                                  total=sum(1 for r in recs if r['difficulty'] == d)) for d, _ in DIFFS}
    ab = [r for r in recs if r['answer_type'] == 'ABSTAIN']
    s['abstain_ok'], s['abstain_total'] = sum(r['strict'] == 'match' for r in ab), len(ab)
    s['over_abstain'] = sum(1 for r in recs if r['answer_type'] == 'SQL' and r['abstained'])
    s['exec_error'] = sum(1 for r in recs if r['strict'] == 'error' and not r.get('api_error'))
    s['api_error'] = sum(1 for r in recs if r.get('api_error'))
    s['dialect_recovered'] = sum(1 for r in recs if r['strict'] != 'match' and r['norm'] == 'match')
    # 비교용 — 결과분석보고서 실험 입력과 달라진 문항·문형(aggregate100.jsonl 의 compare_exclude_forms)을 뺀 정확도.
    # 문형을 모르면(응답 파일에 문형 칸이 비어 있으면) 제외 문형이 하나라도 있는 문항은 뺀다.
    cmp = [r for r in recs if not _excluded(r)]
    s['compare'] = dict(items=len(cmp), excluded=n - len(cmp),
                        excluded_ids=sorted({r['qid'] for r in recs if _excluded(r)}),
                        strict=sum(r['strict'] == 'match' for r in cmp), norm=sum(r['norm'] == 'match' for r in cmp))
    rules = {}
    for r in recs:
        if r.get('rule_label') is not None:
            pr = r.get('prompt_rule')
            k = (r['rule_label'], f"라벨 {r['rule_label']}" + (f"(프롬프트 규칙 {pr})" if pr else '') + f" {r['rule_label_name']}")
            x = rules.setdefault(k, [0, 0])
            x[0] += r['strict'] == 'match'
            x[1] += 1
    s['by_rule'] = {k[1]: dict(strict=v[0], total=v[1]) for k, v in sorted(rules.items(), key=lambda kv: kv[0][0])}
    s['timing'] = _timing(recs)
    s['tokens'] = _tokens(recs)
    return s


def proc_stats(recs):
    rows = [x for r in recs for x in r['rows']]
    n = len(rows)
    wn = [x for x in rows if x['has_num']]
    s = dict(task='proc', items=len(recs), steps=n,
             exact=sum(x['exact'] for x in rows),
             misrec=sum(1 for x in rows if not x['exact'] and x['anywhere']),
             halluc=sum(1 for x in rows if not x['anywhere']),
             num_ok=sum(1 for x in wn if x['num_ok']), num_all=len(wn),
             legacy_parsed=sum(1 for r in recs if r['parsed']),
             json_valid=sum(1 for r in recs if r['json_valid']),
             step_yield=sum(1 for r in recs if r['cls'] == 'steps'),
             abstain=sum(1 for r in recs if r['cls'] == 'abstain'),
             gen_fail=sum(1 for r in recs if r['cls'] == 'gen_fail'),
             legacy_refuse=sum(1 for r in recs if r['n_steps'] == 0),
             lack_raw=sum(1 for r in recs if r['missing'].strip()),
             lack=sum(1 for r in recs if r['lack']),
             api_error=sum(1 for r in recs if r.get('api_error')))
    s['exact_pct'], s['misrec_pct'], s['halluc_pct'] = _pct(s['exact'], n), _pct(s['misrec'], n), _pct(s['halluc'], n)
    # 시연 화면(maintqa_web.py do_proc)은 다른 기록에서 찾은 인용도 기록번호를 고쳐 통과시킨다 → 화면 기준 통과 = anywhere
    s['anywhere'] = s['exact'] + s['misrec']
    s['anywhere_pct'] = _pct(s['anywhere'], n)
    s['by_kind'] = {}
    for k in KINDS:
        g = [r for r in recs if r['kind'] == k]
        gr = [x for r in g for x in r['rows']]
        s['by_kind'][k] = dict(total=len(g), steps=len(gr), exact=sum(x['exact'] for x in gr),
                               step_yield=sum(1 for r in g if r['cls'] == 'steps'),
                               abstain=sum(1 for r in g if r['cls'] == 'abstain'),
                               gen_fail=sum(1 for r in g if r['cls'] == 'gen_fail'))
    s['timing'] = _timing(recs)
    s['tokens'] = _tokens(recs)
    return s


def fmt_agg(s, title):
    n = s['items']
    L = [f'=== {title} · 문항 {n} ===']
    for key, lab in [('strict', '엄격'), ('norm', '방언정규화')]:
        by = s['by_difficulty']
        L.append(f'  [{lab}] 전체 {s[key]}/{n} ({_pct(s[key], n):.1f}%)  '
                 + '  '.join(f"{ko} {by[d][key]}/{by[d]['total']}" for d, ko in DIFFS))
    L.append(f"  기권 지시이행 {s['abstain_ok']}/{s['abstain_total']} · 과잉 기권(정답이 SQL 인데 기권) {s['over_abstain']}건")
    L.append(f"  실행 오류(E) {s['exec_error']}건 · API 오류 {s['api_error']}건 · 방언교정으로만 맞은 문항 {s['dialect_recovered']}건")
    c = s.get('compare')
    if c and c['excluded']:
        L.append(f"  비교용(실험 입력과 달라진 문항·문형 {c['excluded']}건 제외: {','.join(c['excluded_ids'])})  "
                 f"엄격 {c['strict']}/{c['items']} ({_pct(c['strict'], c['items']):.1f}%) · 방언정규화 {c['norm']}/{c['items']}")
    if s['by_rule']:
        L.append('  규칙별(엄격, 라벨=벤치 번호·괄호=프롬프트 규칙 번호)  '
                 + ' · '.join(f"{k} {v['strict']}/{v['total']}" for k, v in s['by_rule'].items()))
    t = s.get('timing')
    if t:
        L.append(f"  응답 시간 평균 {t['mean']}초 · 중앙값 {t['median']}초 · 최대 {t['max']}초 · 합계 {t['total_min']}분")
    tk = s.get('tokens')
    if tk:
        L.append(f"  토큰 입력 {tk['prompt']:,} · 출력 {tk['completion']:,} (usage 가 온 {tk['n']}건 합계)")
    return '\n'.join(L)


def fmt_proc(s, title, input_hash=None):
    n, N = s['steps'], s['items']
    L = [f'=== {title} · ' + (f'입력해시 {input_hash} · ' if input_hash else '') + f'문항 {N} · 단계 {n} ===']
    L.append(f"  JSON 파싱(원 정의)      {s['legacy_parsed']}/{N}")
    if n:
        L.append(f"  인용 충실도(exact)      {s['exact']}/{n} ({s['exact_pct']:.1f}%)")
        L.append(f"  기록번호 오기           {s['misrec']}/{n} ({s['misrec_pct']:.1f}%)")
        L.append(f"  인용 환각               {s['halluc']}/{n} ({s['halluc_pct']:.1f}%)")
        L.append(f"  (참고) 시연 화면 기준 통과(anywhere) {s['anywhere']}/{n} ({s['anywhere_pct']:.1f}%) — 화면은 기록번호를 고쳐 통과시킨다")
    if s['num_all']:
        L.append(f"  측정값 근거율           {s['num_ok']}/{s['num_all']}")
    L.append(f"  기록부족 명시(원 정의)  {s['lack_raw']}/{N} · 실제 사유 있는 것 {s['lack']}/{N}")
    L.append(f"  답 못 만듦(단계 0)      {s['legacy_refuse']}/{N}")
    L.append(f"  ── proc_metrics 재정의 ── JSON 유효 {s['json_valid']}/{N} · 단계 산출 {s['step_yield']}/{N} · "
             f"의도적 기권 {s['abstain']} · 생성 실패 {s['gen_fail']}")
    L.append('  유형별(단계산출/기권/생성실패/문항 · 인용정확/단계)  ' + ' · '.join(
        f"{k} {v['step_yield']}/{v['abstain']}/{v['gen_fail']}/{v['total']} · {v['exact']}/{v['steps']}"
        for k, v in s['by_kind'].items() if v['total']))
    L.append(f"  API 오류 {s['api_error']}건")
    t = s.get('timing')
    if t:
        L.append(f"  응답 시간 평균 {t['mean']}초 · 중앙값 {t['median']}초 · 최대 {t['max']}초 · 합계 {t['total_min']}분")
    tk = s.get('tokens')
    if tk:
        L.append(f"  토큰 입력 {tk['prompt']:,} · 출력 {tk['completion']:,} (usage 가 온 {tk['n']}건 합계)")
    return '\n'.join(L)


# ═══════════════════════ 결과 파일 ═══════════════════════
COLS6 = ['모델명', '버전', '시험일', '문항ID', '조건', '문형', '응답 원문', '추출 SQL', '실행 결과', '정답 여부', '비고', '회차']
COLS_X = ['과제', '난이도', '정답 유형', '규칙', '판정(strict)', '판정(norm)', '기권 출력', '정답 미리보기',
          '유형(절차형)', '분류', 'json_valid', '파싱(원 정의)', '단계 수', '인용정확', '기록번호 오기', '인용 환각',
          '수치 단계', '수치 근거', 'missing', '기록부족(실제 사유)', 'keep 경고',
          '응답 시간(초)', '시도 횟수', '입력 토큰', '출력 토큰', '종료 사유', '응답 모델', 'API 오류', '프롬프트 판', '프롬프트 sha1']
STEP_COLS = ['문항ID', '단계 번호', '지목 기록', 'exact', 'anywhere', '찾은 기록', '찾은 위치', '수치', '수치 근거(num_ok)',
             '빠진 수치', 'step', 'quote']


def _cell(v):
    """엑셀이 = + - @ 로 시작하는 칸을 수식으로 읽지 않게 앞에 ' 를 붙인다(CSV 에서만. JSONL 은 원문 그대로)."""
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'True' if v else 'False'
    s = str(v)
    return "'" + s if s[:1] in ('=', '+', '-', '@') else s


def csv_row(r):
    agg = r['task'] == 'agg'
    note = ' '.join(x for x in [r.get('note') or '', r.get('auto_note') or ''] if x).strip()
    base = [r.get('model'), r.get('version'), r.get('date'), r['qid'], r.get('cond'), r.get('phrasing'),
            r.get('response'), r.get('extracted') if agg else '', r.get('result_text'), r.get('mark'), note, r.get('run')]
    x = ['집계형' if agg else '절차형',
         dict(DIFFS).get(r.get('difficulty'), '') if agg else '', r.get('answer_type') if agg else '',
         (f"{r['rule_label']}. {r['rule_label_name']}" if agg and r.get('rule_label') is not None else ''),
         r.get('strict') if agg else '', r.get('norm') if agg else '', r.get('abstained') if agg else '',
         r.get('gold_head') if agg else '',
         r.get('kind') if not agg else '', r.get('cls') if not agg else '', r.get('json_valid') if not agg else '',
         r.get('parsed') if not agg else '', r.get('n_steps') if not agg else '', r.get('n_exact') if not agg else '',
         r.get('n_misrec') if not agg else '', r.get('n_halluc') if not agg else '', r.get('n_num') if not agg else '',
         r.get('n_num_ok') if not agg else '', r.get('missing') if not agg else '', r.get('lack') if not agg else '',
         r.get('keep_warning') if not agg else '',
         r.get('sec'), r.get('attempts'), r.get('prompt_tokens'), r.get('completion_tokens'), r.get('finish_reason'),
         r.get('model_returned'), r.get('api_error'), r.get('variant'), r.get('prompt_sha1')]
    return [_cell(v) for v in base + x]


def write_csv(path, recs):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(COLS6 + COLS_X)
        for r in recs:
            w.writerow(csv_row(r))


def write_steps_csv(path, recs):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(STEP_COLS)
        for r in recs:
            for i, x in enumerate(r.get('rows') or [], 1):
                w.writerow([_cell(v) for v in [r['qid'], i, x['claimed_rec'], x['exact'], x['anywhere'],
                                               x['matched_rec'], x['match_pos'], ' '.join(x['nums']),
                                               x['num_ok'], ' '.join(x['nums_missing']), x['step'], x['quote']]])


def write_jsonl(path, recs):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def write_compat(path, task, tag, recs):
    """원 벤치마크 출력과 같은 구조 — 원 분석 스크립트(proc_metrics.py 등)를 그대로 쓸 수 있게."""
    if task == 'agg':   # bench/run100.py → res100_<태그>.json
        out = [dict(cond=r.get('cond'), i=r['num'], q=r.get('question'), diff=r['difficulty'], rule=r['rule_label'],
                    out=('__ERROR__ ' + r['api_error'][:120]) if r.get('api_error') else r['extracted'],
                    strict=r['strict'], norm=r['norm'], err=r['exec_error'] or (r['api_error'][:120] if r.get('api_error') else None),
                    sec=r.get('sec') or 0, ptok=r.get('prompt_tokens'), ctok=r.get('completion_tokens'),
                    gold_head=r['gold_head'], pred_head=r['pred_head']) for r in recs]
    else:               # bench/proc_run.py → proc100_<태그>.json
        out = dict(tag=tag, input_hash=proc_input_hash(list(CTX().values())),
                   results=[dict(id=r['orig_id'], pid=r['qid'], ko=r['question_ko'], en=r['question_en'], kind=r['kind'],
                                 category=r['category'], gold_n=r['gold_n'], parsed=r['parsed'], n_steps=r['n_steps'],
                                 rows=r['rows'], missing=r['missing'], sec=r.get('sec') or 0, raw=r.get('response') or '')
                            for r in recs])
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def safe_name(s):
    return re.sub(r'[^0-9A-Za-z가-힣._-]+', '-', str(s)).strip('-') or 'x'


def show_path(p):
    """화면·파일에 남길 경로 — 키트 안이면 키트 기준 상대경로."""
    try:
        r = os.path.relpath(p, KIT)
    except ValueError:
        return p
    return r.replace('\\', '/') if not r.startswith('..') else p


# ═══════════════════════ 문항 고르기 ═══════════════════════
def parse_ids(spec, task):
    """'Q001-Q010,Q020' · '1-10' · 'all'. 집계형은 Q, 절차형은 P."""
    pre = 'Q' if task == 'agg' else 'P'
    if not spec or spec.strip().lower() == 'all':
        return [f'{pre}{i:03d}' for i in range(1, 101)]
    out = []
    for part in re.split(r'[,\s]+', spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r'([QqPp])?0*(\d{1,3})(?:-([QqPp])?0*(\d{1,3}))?', part)
        if not m:
            raise SystemExit(f'--ids 형식 오류: {part!r} (예: Q001-Q010,Q020 또는 1-10)')
        for L in (m.group(1), m.group(3)):
            if L and L.upper() != pre:
                raise SystemExit(f'--ids {part!r}: {"집계형은 Q" if task == "agg" else "절차형은 P"} 로 시작해야 한다')
        a = int(m.group(2))
        b = int(m.group(4)) if m.group(4) else a
        if not (1 <= a <= 100 and 1 <= b <= 100):
            raise SystemExit(f'--ids {part!r}: 1~100 범위를 벗어났다')
        for i in range(min(a, b), max(a, b) + 1):
            k = f'{pre}{i:03d}'
            if k not in out:
                out.append(k)
    return out


def norm_form(s):
    f = FORM_ALIAS.get(str(s).strip().lower()) or FORM_ALIAS.get(str(s).strip())
    if not f:
        raise SystemExit(f'--phrasing {s!r}: orig · L2 · L1 · NL(자연어) 중 하나')
    return f


def select_items(a):
    """[(조합 설명 dict, [item...])] — item = dict(qid, prompt, question, prompt_sha1, ...)."""
    combos = []
    if a.task == 'agg':
        conds = [c.strip().upper() for c in a.cond.split(',') if c.strip()]
        forms = [norm_form(x) for x in a.phrasing.split(',') if x.strip()]
        ids = parse_ids(a.ids, 'agg')
        for c in conds:
            idx = agg_prompt_index(a.variant, c)
            for f in forms:
                items = []
                for qid in ids:
                    r = idx.get((qid, c, f))
                    if r is None:
                        raise SystemExit(f'프롬프트 없음: {qid} 조건 {c} 문형 {f} 판 {a.variant}')
                    items.append(dict(qid=qid, prompt=r['prompt'], question=AGG()[qid]['questions'][f],
                                      prompt_sha1=sha1(r['prompt'])))
                if a.limit:
                    items = items[:a.limit]
                combos.append((dict(task='agg', cond=c, phrasing=f, variant=a.variant), items))
    else:
        lang = a.lang
        ids = parse_ids(a.ids, 'proc')
        pp = PROC_PROMPTS()
        items = []
        for pid in ids:
            r = pp.get((pid, lang))
            if r is None:
                raise SystemExit(f'절차형 프롬프트 없음: {pid} ({lang})')
            q = PROC()[pid]
            items.append(dict(qid=pid, prompt=r['prompt'], question=q['question_en'] if lang == 'en' else q['question_ko'],
                              prompt_sha1=sha1(r['prompt'])))
        if a.limit:
            items = items[:a.limit]
        combos.append((dict(task='proc', cond='PROC', phrasing=lang, variant='kit'), items))
    return combos


# ═══════════════════════ OpenAI 호환 호출 ═══════════════════════
class FatalApiError(Exception):
    """더 보내도 같은 결과가 나올 오류(인증·주소·지원하지 않는 설정·잔액). 실행을 멈춘다."""


def is_loopback(url):
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return host in ('localhost', '::1') or host.startswith('127.')


def endpoint(base):
    b = base.strip().rstrip('/')
    if '://' not in b:                     # 127.0.0.1:8080/v1 처럼 적어도 된다
        b = 'http://' + b
    if b.endswith('/chat/completions'):
        return b
    if urllib.parse.urlsplit(b).path in ('', '/'):
        b += '/v1'
    return b + '/chat/completions'


def make_cfg(a, task):
    base = a.base_url or os.environ.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1'
    url = endpoint(base)
    loop = is_loopback(url)
    preset = a.preset if a.preset != 'auto' else ('llama' if loop else 'openai')
    key = a.api_key if a.api_key is not None else os.environ.get('OPENAI_API_KEY', '')
    model = a.model if a.model is not None else os.environ.get('OPENAI_MODEL', '')
    mt = DEFAULT_MAX_TOKENS[task] if a.max_tokens is None else a.max_tokens
    mtp = a.max_tokens_param
    if mtp == 'auto':
        host = (urllib.parse.urlsplit(url).hostname or '').lower()
        mtp = 'max_completion_tokens' if (preset == 'openai' and host.endswith('openai.com')) else 'max_tokens'
    extra = {}
    if a.extra:
        try:
            extra = json.loads(a.extra)
            assert isinstance(extra, dict)
        except Exception:
            raise SystemExit('--extra 는 JSON 객체여야 한다. 예: --extra "{\\"reasoning_effort\\": \\"low\\"}"')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if loop else urllib.request.build_opener()
    return dict(url=url, base=base, loopback=loop, preset=preset, api_key=key, model=model,
                temperature=None if a.no_temperature else a.temperature, max_tokens=mt, max_tokens_param=mtp,
                seed=a.seed, extra=extra, timeout=(a.timeout or DEFAULT_TIMEOUT[task]), retries=a.retries,
                backoff=a.backoff, backoff_cap=120.0, interval=a.interval, opener=opener, _last=[0.0],
                strip=(task == 'proc'))


def build_body(prompt, cfg):
    """llama 프리셋은 원 실험(run100.py ask · rag.py llm)과 같은 본문을 만든다."""
    body = {}
    if cfg['model']:
        body['model'] = cfg['model']
    elif cfg['preset'] == 'openai':
        raise SystemExit('모델 이름이 없다. --model 또는 환경변수 OPENAI_MODEL 을 준다.')
    body['messages'] = [{'role': 'user', 'content': prompt}]
    if cfg['temperature'] is not None:
        body['temperature'] = cfg['temperature']
    if cfg['preset'] == 'llama':
        body['top_k'] = 1
        body['seed'] = 42
    if cfg['seed'] is not None:
        body['seed'] = cfg['seed']
    if cfg['max_tokens']:
        body[cfg['max_tokens_param']] = cfg['max_tokens']
    if cfg['preset'] == 'llama':
        body['chat_template_kwargs'] = {'enable_thinking': False}
    body.update(cfg['extra'])
    return body


def request_summary(cfg):
    b = build_body('<프롬프트>', dict(cfg, model=cfg['model'] or ('' if cfg['preset'] == 'llama' else '<모델>')))
    b.pop('messages', None)
    return b


def _retry_after(headers):
    if not headers:
        return None
    v = headers.get('retry-after-ms')
    if v:
        try:
            return float(v) / 1000.0
        except ValueError:
            pass
    v = headers.get('Retry-After')
    if v:
        try:
            return float(v)
        except ValueError:
            try:
                d = email.utils.parsedate_to_datetime(v)
                return max(0.0, (d - datetime.datetime.now(d.tzinfo)).total_seconds())
            except Exception:
                return None
    return None


def chat(prompt, cfg, log=None):
    """POST /v1/chat/completions. 성공하면 content, 실패하면 error 를 담아 돌려준다.
    429·5xx·연결 오류는 다시 보낸다(Retry-After 를 따른다). 인증·설정 오류는 FatalApiError."""
    log = log or (lambda m: print(m, flush=True))
    body = build_body(prompt, cfg)
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if cfg['api_key']:
        headers['Authorization'] = 'Bearer ' + cfg['api_key']
    t_all, err = time.time(), None
    for attempt in range(cfg['retries'] + 1):
        gap = cfg['interval'] - (time.time() - cfg['_last'][0])
        if gap > 0:
            time.sleep(gap)
        cfg['_last'][0] = time.time()
        req = urllib.request.Request(cfg['url'], data=data, headers=headers, method='POST')
        t = time.time()
        wait = min(cfg['backoff_cap'], cfg['backoff'] * (2 ** attempt))
        try:
            with cfg['opener'].open(req, timeout=cfg['timeout']) as r:
                d = json.loads(r.read().decode('utf-8'))
            dt = time.time() - t
            if not isinstance(d, dict) or 'choices' not in d:
                err = f'응답 형식이 다름: {str(d)[:200]}'
                if isinstance(d, dict) and d.get('error'):
                    err = f"서버 오류: {str(d.get('error'))[:200]}"
            else:
                ch = (d.get('choices') or [{}])[0] or {}
                msg = ch.get('message') or {}
                content = msg.get('content')
                if isinstance(content, list):
                    content = ''.join(p.get('text', '') for p in content if isinstance(p, dict))
                content = content or ''
                usage = d.get('usage') or {}
                return dict(content=content.strip() if cfg['strip'] else content, error=None, sec=round(dt, 2),
                            total_sec=round(time.time() - t_all, 2), attempts=attempt + 1,
                            prompt_tokens=usage.get('prompt_tokens'), completion_tokens=usage.get('completion_tokens'),
                            finish_reason=ch.get('finish_reason'), model_returned=d.get('model'),
                            fingerprint=d.get('system_fingerprint'), refusal=msg.get('refusal'))
        except urllib.error.HTTPError as e:
            try:
                txt = e.read().decode('utf-8', 'replace')
            except Exception:
                txt = ''
            low = txt.lower()
            ra = _retry_after(e.headers)
            if e.code == 429 and 'insufficient_quota' in low:
                raise FatalApiError('잔액·사용 한도 부족(insufficient_quota). API 결제·한도 설정을 확인한다.')
            if e.code in (401, 403):
                raise FatalApiError(f'인증 거부(HTTP {e.code}). OPENAI_API_KEY 와 --base-url 을 확인한다. {txt[:200]}')
            if e.code == 404:
                raise FatalApiError(f'주소 또는 모델을 찾지 못함(HTTP 404). --base-url 과 --model 을 확인한다. {txt[:200]}')
            if e.code in (400, 422):
                if 'temperature' in low:
                    raise FatalApiError('이 모델은 temperature 설정을 받지 않는다 → --no-temperature 를 붙여 다시 실행한다. '
                                        f'서버 응답: {txt[:240]}')
                if 'max_tokens' in low or 'max_completion_tokens' in low:
                    raise FatalApiError('출력 길이 매개변수 이름이 맞지 않는다 → --max-tokens-param max_completion_tokens '
                                        f'(또는 max_tokens) 로 바꾸거나 --max-tokens 0 으로 뺀다. 서버 응답: {txt[:240]}')
                if re.search(r'unsupported_(parameter|value)|unrecognized request argument|unknown (parameter|field)', low):
                    raise FatalApiError(f'지원하지 않는 요청 설정. --extra·--seed·--preset 을 확인한다. 서버 응답: {txt[:240]}')
                return dict(content='', error=f'HTTP {e.code}: {txt[:300]}', sec=None,
                            total_sec=round(time.time() - t_all, 2), attempts=attempt + 1)
            if e.code in (408, 409, 425, 429) or e.code >= 500:
                err = f'HTTP {e.code}: {txt[:200]}'
                if ra is not None:
                    wait = min(600.0, ra)
            else:
                return dict(content='', error=f'HTTP {e.code}: {txt[:300]}', sec=None,
                            total_sec=round(time.time() - t_all, 2), attempts=attempt + 1)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as e:
            err = f'{type(e).__name__}: {e}'
        if attempt < cfg['retries']:
            log(f'    · 재시도 {attempt + 1}/{cfg["retries"]} — {err[:120]} · {wait:.1f}초 대기')
            time.sleep(wait)
    return dict(content='', error=err, sec=None, total_sec=round(time.time() - t_all, 2), attempts=cfg['retries'] + 1)


def model_id_name(v):
    """모델 id → 파일 이름만(학생판). 'C:\\…\\gemma-4-12B-it-Q4_K_M.gguf' → 'gemma-4-12B-it-Q4_K_M.gguf'.
    경로가 아닌 id(예: 'gemma3:12b')는 그대로 둔다."""
    if isinstance(v, str) and ('/' in v or '\\' in v):
        return re.split(r'[\\/]', v.strip().rstrip('\\/'))[-1]
    return v


_chat_kit = chat


def _chat_student(prompt, cfg, log=None):
    """키트판 chat 을 그대로 부르고, 돌려받은 model_returned 만 파일 이름으로 줄인다(학생판)."""
    res = _chat_kit(prompt, cfg, log)
    if isinstance(res, dict) and res.get('model_returned'):
        res['model_returned'] = model_id_name(res['model_returned'])
    return res


chat = _chat_student


# ═══════════════════════ API 시험 실행 ═══════════════════════
def default_tag(combo, cfg):
    m = safe_name(cfg['model'] or 'local')
    if combo['task'] == 'agg':
        return f"agg_{combo['cond']}_{combo['phrasing']}_{combo['variant']}_{m}"
    return f"proc_{combo['phrasing']}_{m}"


def load_done(path):
    done = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done[r['qid']] = r
                except Exception:
                    pass             # 중단 때 반쯤 쓰인 마지막 줄
    return done


def finish_outputs(out_dir, tag, combo, recs, quiet=False):
    order = {f'Q{i:03d}': i for i in range(1, 101)}
    order.update({f'P{i:03d}': i for i in range(1, 101)})
    recs = sorted(recs, key=lambda r: order.get(r['qid'], len(order) + 1))
    base = os.path.join(out_dir, tag)
    write_csv(base + '.csv', recs)
    if combo['task'] == 'agg':
        s = agg_stats(recs)
        title = f"{tag} · 조건 {combo['cond']} · 문형 {combo['phrasing']} · 판 {combo['variant']}"
        text = fmt_agg(s, title)
        write_compat(os.path.join(out_dir, f'res100_{tag}.json'), 'agg', tag, recs)
    else:
        s = proc_stats(recs)
        h = proc_input_hash(list(CTX().values()))
        text = fmt_proc(s, f"{tag} · 질문 {combo['phrasing']}", h)
        s['input_hash'] = h
        write_steps_csv(base + '_steps.csv', recs)
        write_compat(os.path.join(out_dir, f'proc100_{tag}.json'), 'proc', tag, recs)
    s['combo'] = combo
    with open(base + '_summary.json', 'w', encoding='utf-8', newline='\n') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    with open(base + '_summary.txt', 'w', encoding='utf-8', newline='\n') as f:
        f.write(text + '\n')
    if not quiet:
        print('\n' + text)
        print(f'  저장: {show_path(base)}.csv · .jsonl · _summary.txt · _summary.json'
              + (' · _steps.csv' if combo['task'] == 'proc' else '')
              + f" · {'res100_' if combo['task'] == 'agg' else 'proc100_'}{tag}.json", flush=True)
    return s, text


def run_combo(combo, items, cfg, kit, out_dir, tag, resume=False, overwrite=False, version=None, log=None,
              quiet=False):
    log = log or (lambda m: print(m, flush=True))
    os.makedirs(out_dir, exist_ok=True)
    jl = os.path.join(out_dir, tag + '.jsonl')
    model_label = cfg['model'] or '(로컬)'
    done = {}
    if os.path.exists(jl):
        if resume:
            done = load_done(jl)
            first = next(iter(done.values()), None)
            if first and (first.get('cond'), first.get('phrasing'), first.get('model'), first.get('variant')) != \
                    (combo['cond'], combo['phrasing'], model_label, combo['variant']):
                raise SystemExit(f'--resume: {show_path(jl)} 은 다른 설정(조건·문형·모델·판)의 결과다. --tag 를 바꾼다.')
            log(f'  이어서 실행: 이미 끝난 {len(done)}건은 건너뛴다')
        elif not overwrite:
            raise SystemExit(f'결과 파일이 이미 있다: {show_path(jl)}  (이어서 하려면 --resume, 새로 하려면 --overwrite 또는 --tag)')
    today = datetime.date.today().isoformat()
    fatal = None
    with open(jl, 'a' if resume else 'w', encoding='utf-8', newline='\n') as f:
        for n, it in enumerate(items, 1):
            if it['qid'] in done:
                continue
            try:
                res = chat(it['prompt'], cfg, log)
            except FatalApiError as e:
                fatal = str(e)
                break
            if combo['task'] == 'agg':
                rec = score_agg(kit, it['qid'], res['content'], api_error=res['error'])
            else:
                rec = score_proc(it['qid'], res['content'], api_error=res['error'])
            rec.update(model=model_label, version=version or res.get('model_returned') or '',
                       date=today, cond=combo['cond'], phrasing=combo['phrasing'], variant=combo['variant'],
                       question=it['question'], response=res['content'], note='',
                       sec=res.get('sec'), total_sec=res.get('total_sec'), attempts=res.get('attempts'),
                       prompt_tokens=res.get('prompt_tokens'), completion_tokens=res.get('completion_tokens'),
                       finish_reason=res.get('finish_reason'), model_returned=res.get('model_returned'),
                       fingerprint=res.get('fingerprint'), api_error=res.get('error'), refusal=res.get('refusal'),
                       prompt_sha1=it['prompt_sha1'], prompt_chars=len(it['prompt']),
                       endpoint=('loopback' if cfg['loopback'] else urllib.parse.urlsplit(cfg['url']).hostname),
                       request=request_summary(cfg), kit_version=VERSION,
                       ts=datetime.datetime.now().isoformat(timespec='seconds'))
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
            done[it['qid']] = rec
            sec = f"{rec['sec']:5.1f}s" if isinstance(rec['sec'], (int, float)) else '  API오류'
            if combo['task'] == 'agg':
                log(f"  {MK[rec['strict']]}{MK[rec['norm']]} {rec['num']:>3} [{rec['difficulty']:<6}] {sec}  "
                    f"{it['question'][:30]:<32} → {str(rec['pred_head'])[:24]:<26} (정답 {str(rec['gold_head'])[:20]})")
            else:
                log(f"  {n:>3}/{len(items)} [{rec['kind']:<4}] 단계 {rec['n_steps']:>2} · 인용정확 {rec['n_exact']:>2} · "
                    f"{rec['cls']:<8} {sec}  {it['question'][:28]}")
    recs = [done[it['qid']] for it in items if it['qid'] in done]
    s, text = finish_outputs(out_dir, tag, combo, recs, quiet) if recs else (None, '')
    if fatal:
        raise FatalApiError(fatal + f'  (끝난 {len(recs)}건은 저장했다. 고친 뒤 --resume 으로 이어서 실행한다.)')
    return s


def confirm_cost(cfg, n_calls, n_chars, yes):
    if cfg['loopback'] or yes:
        return
    msg = (f'외부 API({urllib.parse.urlsplit(cfg["url"]).hostname})로 {n_calls}건을 보낸다 '
           f'(프롬프트 합계 {n_chars:,}자, 모델 {cfg["model"]}). 비용이 든다. 계속할까? [y/N] ')
    hint = msg.replace(' 계속할까? [y/N] ', '') + '\n비대화형 실행이다. 확인했으면 --yes 를 붙인다.'
    if not (sys.stdin and sys.stdin.isatty()):
        raise SystemExit(hint)
    try:
        ans = input(msg)
    except EOFError:        # Windows 에서는 NUL 장치도 단말로 보인다
        raise SystemExit('\n' + hint)
    if ans.strip().lower() not in ('y', 'yes', 'ㅛ'):
        raise SystemExit('취소했다.')


def cmd_run(a):
    combos = select_items(a)
    cfg = make_cfg(a, a.task)
    if cfg['preset'] == 'openai' and not cfg['model']:
        raise SystemExit('모델 이름이 없다. --model 또는 환경변수 OPENAI_MODEL 을 준다.')
    if not cfg['loopback'] and not cfg['api_key']:
        raise SystemExit('API 키가 없다. 환경변수 OPENAI_API_KEY 를 설정한다(명령줄 --api-key 는 기록에 남을 수 있다).')
    n_calls = sum(len(it) for _, it in combos)
    n_chars = sum(len(x['prompt']) for _, it in combos for x in it)
    print(f'호출 계획: {len(combos)}개 조합 · {n_calls}건 · 프롬프트 합계 {n_chars:,}자', flush=True)
    print(f'  주소 {cfg["url"]} · 프리셋 {cfg["preset"]} · 요청 설정 {json.dumps(request_summary(cfg), ensure_ascii=False)}',
          flush=True)
    confirm_cost(cfg, n_calls, n_chars, a.yes)
    kit = Kit(a.db, a.sql_timeout) if a.task == 'agg' else None
    out_dir = a.out_dir or kp('results')
    results = []
    for combo, items in combos:
        tag = a.tag or default_tag(combo, cfg)
        if a.tag and len(combos) > 1:
            tag = f"{a.tag}_{combo['cond']}_{combo['phrasing']}"
        tag = safe_name(tag)
        print(f"\n── {tag}: {len(items)}건", flush=True)
        try:
            s = run_combo(combo, items, cfg, kit, out_dir, tag, a.resume, a.overwrite, a.model_version)
        except FatalApiError as e:
            print(f'\n[중단] {e}', file=sys.stderr)
            return 2
        results.append((tag, s))
    if len(results) > 1 and a.task == 'agg':
        print('\n=== 조합별 정확도(엄격 / 방언정규화) ===')
        for tag, s in results:
            if s:
                print(f"  {tag:<40} {s['strict']}/{s['items']} / {s['norm']}/{s['items']} · 기권 {s['abstain_ok']}/{s['abstain_total']}")
    return 0


def cmd_dry_run(a):
    combos = select_items(a)
    cfg = make_cfg(a, a.task)
    total = 0
    for combo, items in combos:
        desc = (f"조건 {combo['cond']} · 문형 {combo['phrasing']} · 판 {combo['variant']}" if combo['task'] == 'agg'
                else f"절차형 · 질문 {combo['phrasing']}")
        for it in items:
            total += 1
            print(f"===== {it['qid']} · {desc} · {len(it['prompt']):,}자 · sha1 {it['prompt_sha1'][:12]} =====")
            print(it['prompt'])
    print('\n===== 요청 설정(--dry-run: 보내지 않았다) =====', file=sys.stderr)
    print(f"POST {cfg['url']}  (프리셋 {cfg['preset']})", file=sys.stderr)
    print(json.dumps(request_summary(cfg), ensure_ascii=False), file=sys.stderr)
    print(f"Authorization: {'Bearer ***(키 있음)' if cfg['api_key'] else '(없음)'} · 건수 {total} · "
          f"프롬프트 합계 {sum(len(x['prompt']) for _, it in combos for x in it):,}자", file=sys.stderr)
    return 0


# ═══════════════════════ 손으로 모은 응답 채점(--score-file) ═══════════════════════
ALIASES = {
    'qid': ['qid', 'id', '문항id', '문항'],
    'cond': ['cond', 'condition', '조건'],
    'phrasing': ['phrasing', 'form', '문형'],
    'response': ['response', 'answer', 'output', 'raw', '응답원문', '응답'],
    'model': ['model', '모델명', '모델'],
    'version': ['version', '버전'],
    'date': ['date', '시험일'],
    'note': ['note', '비고'],
    'run': ['run', 'trial', 'repeat', '회차', '반복'],
}


def _hdr_key(h):
    k = re.sub(r'[\s_]+', '', str(h or '')).lower()
    for canon, names in ALIASES.items():
        if k in names:
            return canon
    return None


def _unescape(v):
    """이 도구가 CSV 에 붙인 ' 를 떼고 읽는다."""
    if isinstance(v, str) and len(v) > 1 and v[0] == "'" and v[1] in '=+-@':
        return v[1:]
    return v


def read_response_table(path):
    low = path.lower()
    if low.endswith(('.xlsx', '.xlsm')):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise SystemExit('.xlsx 를 읽으려면 openpyxl 이 필요하다(pip install openpyxl). 또는 CSV(UTF-8)로 저장해 넘긴다.')
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[SHEET6] if SHEET6 in wb.sheetnames else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        hdr = list(next(it, []))
        raw_rows = [list(r) for r in it]
        wb.close()
    elif low.endswith('.jsonl'):
        recs = load_jsonl(path)
        hdr = []
        for r in recs:
            for k in r:
                if k not in hdr:
                    hdr.append(k)
        raw_rows = [[r.get(k) for k in hdr] for r in recs]
    else:
        data = open(path, 'rb').read()
        text = None
        for enc in ('utf-8-sig', 'cp949', 'utf-16'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise SystemExit(f'{path}: 글자 인코딩을 알 수 없다(UTF-8 로 저장한다)')
        first = text.split('\n', 1)[0]
        delim = '\t' if first.count('\t') > first.count(',') else ','
        rows = list(csv.reader(io.StringIO(text, newline=''), delimiter=delim))   # 따옴표 안 줄바꿈 보존
        hdr, raw_rows = (rows[0] if rows else []), rows[1:]
    keys = [_hdr_key(h) for h in hdr]
    if 'qid' not in keys or 'response' not in keys:
        raise SystemExit(f'{path}: 열 이름을 찾지 못했다. 필요한 열: 문항ID(qid) · 응답 원문(response). '
                         f'있으면 쓰는 열: 모델명(model) · 버전(version) · 시험일(date) · 조건(cond) · 문형(phrasing) · 비고(note). '
                         f'읽은 머리글: {hdr}')
    out = []
    for i, r in enumerate(raw_rows, 2):
        d = {}
        for k, v in zip(keys, r):
            if k and k not in d:
                d[k] = _unescape(v)
        d['_row'] = i
        out.append(d)
    return out


def norm_qid(v, task=None):
    s = str(v or '').strip()
    m = re.fullmatch(r'([QqPp])\s*0*(\d{1,3})', s)
    if m and 1 <= int(m.group(2)) <= 100:
        return f'{m.group(1).upper()}{int(m.group(2)):03d}'
    m = re.fullmatch(r'0*(\d{1,3})(?:\.0)?', s)
    if m and task in ('agg', 'proc') and 1 <= int(m.group(1)) <= 100:
        return f"{'Q' if task == 'agg' else 'P'}{int(m.group(1)):03d}"
    return None


_SQL_TAIL = re.compile(r'^\s*(FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|AND|OR|JOIN|LEFT|INNER|ON|UNION)\b', re.I)


def paste_warning(qid, resp):
    """엑셀 칸만 선택하고 여러 줄 응답을 붙이면 줄마다 아래 행(다음 문항 칸)으로 퍼진다. 그렇게 보이는 응답을 잡는다.
    채점은 그대로 하고 경고만 단다(모델이 정말 그렇게 답했을 수도 있으므로)."""
    t = (resp or '').strip()
    if not t:
        return ''
    if re.fullmatch(r'```\s*\w*', t):
        return '코드펜스 줄만 있음'
    if t[:1] in '}])':
        return '닫는 괄호로 시작'
    if qid.startswith('Q'):
        if not re.search(r'\b(SELECT|WITH)\b', t, re.I) and not any(tok in t.upper() for tok in ABSTAIN):
            return 'SQL·기권 토큰 없음'
        if _SQL_TAIL.match(t):
            return 'SQL 중간부터 시작'
    elif '{' not in t:
        return 'JSON 없음'
    return ''


def _txt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.strftime('%Y-%m-%d')
    return str(v).strip()


def score_table(rows, kit, task=None):
    """반환: (채점된 기록 목록, 건너뛴 행 목록).
    모델명·버전·시험일이 모두 빈 행은 바로 위 행(빈 응답 행 포함)의 값을 이어받는다(meta_filled 로 표시)."""
    recs, skipped = [], []
    last_meta = None
    for d in rows:
        meta = (_txt(d.get('model')), _txt(d.get('version')), _txt(d.get('date'))[:10])
        filled = False
        if any(meta):
            last_meta = meta
        elif last_meta:
            meta, filled = last_meta, True
        qid = norm_qid(d.get('qid'), task)
        resp = d.get('response')
        if resp is None or str(resp).strip() == '':
            if qid:
                skipped.append((d['_row'], qid, '응답 없음'))
            continue
        if not qid:
            skipped.append((d['_row'], d.get('qid'), '문항ID 를 알 수 없음(Q001~Q100 · P001~P100)'))
            continue
        resp = str(resp)
        if qid.startswith('Q'):
            rec = score_agg(kit, qid, resp)
            rec['question'] = None
        else:
            rec = score_proc(qid, resp)
        cond = str(d.get('cond') or ('PROC' if qid.startswith('P') else '')).strip()
        ph = str(d.get('phrasing') or '').strip()
        if qid.startswith('Q') and ph:
            try:
                ph = norm_form(ph)
            except SystemExit:
                pass
            if ph in FORMS:
                rec['question'] = AGG()[qid]['questions'][ph]
        rec.update(model=meta[0], version=meta[1], date=meta[2], cond=cond.upper(), phrasing=ph, variant='',
                   run=_txt(d.get('run')), response=resp, note=_txt(d.get('note')), source_row=d['_row'],
                   meta_filled=filled)
        w = paste_warning(qid, resp)
        if w:
            rec['paste_warning'] = w
            rec['auto_note'] = (rec.get('auto_note', '') + f' [붙여넣기 확인: {w}]').strip()
        recs.append(rec)
    return recs, skipped


def summarize_scored(recs, skipped):
    """손으로 모은 응답의 묶음별 요약. 반환: (요약 글, 요약 dict 목록). 묶음 = 과제·모델명·버전·조건·문형·회차."""
    groups = {}
    for r in recs:
        groups.setdefault((r['task'], r['model'], r['version'], r['cond'], r['phrasing'], r.get('run') or ''), []).append(r)
    texts, summ = [], []
    for (task, model, ver, cond, ph, run), g in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        title = (f"{model or '(모델명 없음)'} {ver} · " + (f'조건 {cond} · 문형 {ph}' if task == 'agg' else f'절차형 · 질문 {ph}')
                 + (f' · 회차 {run}' if run else ''))
        if task == 'agg':
            s = agg_stats(g)
            texts.append(fmt_agg(s, title))
        else:
            s = proc_stats(g)
            texts.append(fmt_proc(s, title, proc_input_hash(list(CTX().values()))))
        s['group'] = dict(model=model, version=ver, cond=cond, phrasing=ph, run=run)
        summ.append(s)
    text = '\n\n'.join(texts) if texts else '(채점할 응답이 없다 — 응답 원문 칸이 비어 있다)'
    n_fill = sum(1 for r in recs if r.get('meta_filled'))
    if n_fill:
        text += (f'\n\n알림: 모델명·버전·시험일이 비어 있던 {n_fill}행은 바로 위 행의 값을 이어받아 묶었다'
                 '(다른 모델이면 그 행에 모델명을 적는다).')
    warn = [r for r in recs if r.get('paste_warning')]
    if warn:
        text += (f'\n\n붙여넣기 확인 필요 {len(warn)}행(여러 줄 응답이 아래 행으로 퍼졌을 수 있다 — 엑셀 칸은 더블클릭·F2 로 편집 상태에서 붙인다): '
                 + ', '.join(f"{r['qid']}({r['paste_warning']})" for r in warn[:30]) + (' …' if len(warn) > 30 else ''))
    if skipped:
        n_blank = sum(1 for x in skipped if x[2] == '응답 없음')
        text += f'\n\n건너뛴 행: 응답 없음 {n_blank}행' + ''.join(
            f'\n  {row}행 {q!r}: {why}' for row, q, why in skipped if why != '응답 없음')
    return text, summ


def write_scored(recs, skipped, out_dir, tag, source):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, tag + '_scored')
    write_csv(base + '.csv', recs)
    write_jsonl(base + '.jsonl', recs)
    if any(r['task'] == 'proc' for r in recs):
        write_steps_csv(base + '_steps.csv', [r for r in recs if r['task'] == 'proc'])
    text, summ = summarize_scored(recs, skipped)
    with open(base + '_summary.txt', 'w', encoding='utf-8', newline='\n') as f:
        f.write(text + '\n')
    with open(base + '_summary.json', 'w', encoding='utf-8', newline='\n') as f:
        json.dump(dict(source=source, groups=summ, skipped=len(skipped),
                       paste_warnings=[dict(qid=r['qid'], row=r.get('source_row'), why=r['paste_warning'])
                                       for r in recs if r.get('paste_warning')],
                       meta_filled=sum(1 for r in recs if r.get('meta_filled'))), f, ensure_ascii=False, indent=1)
    return base, text


def cmd_score_file(a):
    path = a.score_file
    if not os.path.exists(path):
        raise SystemExit(f'파일이 없다: {path}')
    rows = read_response_table(path)
    need_db = any(str(norm_qid(d.get('qid'), a.task_given) or '').startswith('Q') for d in rows)
    kit = Kit(a.db, a.sql_timeout) if need_db else None
    recs, skipped = score_table(rows, kit, a.task_given)
    tag = safe_name(a.tag or os.path.splitext(os.path.basename(path))[0])
    base, text = write_scored(recs, skipped, a.out_dir or kp('results'), tag, os.path.basename(path))
    print(text)
    print(f'\n채점 {len(recs)}건 → {show_path(base)}.csv · .jsonl · _summary.txt · _summary.json'
          + (' · _steps.csv' if any(r['task'] == 'proc' for r in recs) else ''))
    return 0


def read_response_dir(path, a):
    """문항ID 이름의 응답 파일(Q001.txt · P004.txt · q1.md …)을 ⑥ 기록양식과 같은 행으로 읽는다.
    엑셀 칸에 붙여넣다 줄이 퍼지는 문제를 피하는 길이다. 모델명·버전은 --model · --model-version, 시험일은 파일 수정일."""
    if not os.path.isdir(path):
        raise SystemExit(f'폴더가 없다: {path}')
    conds = [c.strip().upper() for c in str(a.cond).split(',') if c.strip()]
    forms = [x for x in str(a.phrasing).split(',') if x.strip()]
    if len(conds) != 1 or len(forms) != 1:
        raise SystemExit('--score-dir 는 한 조건·한 문형만 받는다(예: --cond F --phrasing NL). 조합마다 폴더를 나눈다.')
    ph = norm_form(forms[0])
    rows = []
    for i, f in enumerate(sorted(os.listdir(path)), 1):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in ('.txt', '.md', '.json', '.sql'):
            continue
        qid = norm_qid(stem.split('_')[0])
        if not qid:
            continue
        p = os.path.join(path, f)
        data = open(p, 'rb').read()
        text = None
        for enc in ('utf-8-sig', 'cp949', 'utf-16'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise SystemExit(f'{f}: 글자 인코딩을 알 수 없다(UTF-8 로 저장한다)')
        run = stem.split('_', 1)[1] if '_' in stem else ''
        rows.append(dict(qid=qid, response=text, model=a.model or os.path.basename(os.path.abspath(path)),
                         version=a.model_version or '', run=run,
                         date=datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat(),
                         cond=conds[0] if qid.startswith('Q') else 'PROC', phrasing=ph if qid.startswith('Q') else a.lang,
                         note=f'파일 {f}', _row=i))
    if not rows:
        raise SystemExit(f'{path}: 문항ID 이름의 응답 파일(Q001.txt · P001.txt …)이 없다')
    return rows


def cmd_score_dir(a):
    rows = read_response_dir(a.score_dir, a)
    kit = Kit(a.db, a.sql_timeout) if any(d['qid'].startswith('Q') for d in rows) else None
    recs, skipped = score_table(rows, kit)
    tag = safe_name(a.tag or os.path.basename(os.path.abspath(a.score_dir)))
    base, text = write_scored(recs, skipped, a.out_dir or kp('results'), tag, os.path.basename(os.path.abspath(a.score_dir)))
    print(text)
    print(f'\n채점 {len(recs)}건(파일 {len(rows)}개) → {show_path(base)}.csv · .jsonl · _summary.txt · _summary.json'
          + (' · _steps.csv' if any(r['task'] == 'proc' for r in recs) else ''))
    return 0


# ═══════════════════════ 자체 검사(--self-test) ═══════════════════════
class Tester:
    def __init__(self):
        self.items = []

    def check(self, name, ok, detail=''):
        st = 'PASS' if ok else 'FAIL'
        self.items.append((st, name, detail))
        print(f'[{st}] {name}' + (f'  — {detail}' if detail else ''), flush=True)
        return ok

    def skip(self, name, why):
        self.items.append(('SKIP', name, why))
        print(f'[SKIP] {name}  — {why}', flush=True)

    def section(self, title):
        self.items.append(('', title, ''))
        print(f'\n■ {title}', flush=True)


class MockServer:
    """자체 검사 전용 가짜 OpenAI 호환 서버. 이 프로세스 안에서 127.0.0.1 임시 포트에 띄우고 검사 뒤 닫는다.
    실제 API·실행 중인 서버는 부르지 않는다."""

    def __init__(self, responder):
        self.calls = []
        mock = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n).decode('utf-8'))
                mock.calls.append(dict(path=self.path, auth=self.headers.get('Authorization'), body=body))
                status, hdrs, payload = responder(len(mock.calls), body)
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    @property
    def base(self):
        return f'http://127.0.0.1:{self.port}/v1'

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _ok_payload(content, model='mock-model'):
    return dict(id='x', object='chat.completion', model=model, system_fingerprint='fp_mock',
                choices=[dict(index=0, message=dict(role='assistant', content=content), finish_reason='stop')],
                usage=dict(prompt_tokens=100, completion_tokens=10, total_tokens=110))


def _ns(**kw):
    """make_cfg 에 넘길 인자 묶음(자체 검사용)."""
    d = dict(base_url=None, api_key='', model='', preset='auto', no_temperature=False, temperature=0.0,
             max_tokens=None, max_tokens_param='auto', seed=None, extra=None, timeout=None, retries=3,
             backoff=0.05, interval=0.0)
    d.update(kw)
    return argparse.Namespace(**d)


def _fake_proc(quote, record, step='근거 단계'):
    return json.dumps(dict(steps=[dict(step=step, record=record, quote=quote)], missing=''), ensure_ascii=False)


def self_test(a):
    T = Tester()
    tmp = tempfile.mkdtemp(prefix='kit_selftest_')
    try:
        _self_test(T, a, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n_pass = sum(1 for s, _, _ in T.items if s == 'PASS')
    n_fail = sum(1 for s, _, _ in T.items if s == 'FAIL')
    n_skip = sum(1 for s, _, _ in T.items if s == 'SKIP')
    head = (f'결과: {n_pass}/{n_pass + n_fail} PASS' + (f' · FAIL {n_fail}' if n_fail else '')
            + (f' · SKIP {n_skip}' if n_skip else ''))
    print('\n' + head)
    print('self-test PASS — 채점기와 평가 파일이 제대로 놓였습니다.' if n_fail == 0 else
          f'self-test FAIL — 실패 {n_fail}개. 위의 [FAIL] 줄을 확인하십시오.')
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        p = os.path.join(a.out_dir, 'selftest_report.txt')
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f'run_eval.py --self-test ({VERSION})\n{head}\n')
            for s, name, d in T.items:
                f.write((f'\n■ {name}\n' if s == '' else f'[{s}] {name}' + (f'  — {d}' if d else '') + '\n'))
        print(f'보고서: {show_path(p)}')
    return 0 if n_fail == 0 else 1


def _self_test(T, a, tmp):
    # ── A. 키트 무결성 ─────────────────────────────────────
    T.section('A. 키트 무결성')
    need = [DEFAULT_DB, F_AGG_Q, F_PROC_Q, F_CTX, F_PROC_PROMPTS, F_PROC_TEMPLATE, F_PROC_HASH, kp('tool', 'harness_kit.py'),
            kp('tool', 'score_kit.py')]
    need += [kp(*f.split('/')) for f in set(AGG_PROMPT_FILE.values())]
    need += [kp(*f.split('/')) for f in TEMPLATE_FILE.values()]
    missing = [show_path(p) for p in need if not os.path.exists(p)]
    if not T.check('필수 파일', not missing, f'{len(need) - len(missing)}/{len(need)}' + (f' 없음 {missing}' if missing else '')):
        return
    kit = Kit(a.db, a.sql_timeout)
    counts = {t: kit.con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in EXPECTED_ROWS}
    T.check('학생용 DB 테이블 행 수(정비 문서 1,000건 — 17만 행 키트 DB 가 아님)', counts == EXPECTED_ROWS, ' · '.join(f'{t} {n:,}' for t, n in counts.items()))
    try:
        kit.con.execute('CREATE TABLE _x AS SELECT 1')
        T.check('키트 DB 읽기 전용', False, '쓰기가 됐다')
    except Exception:
        T.check('키트 DB 읽기 전용', True, '쓰기 거부')
    res, err = kit.runner.run("SELECT * FROM read_text('run_eval.py');")
    T.check('모델 SQL 의 외부 파일 접근 차단', res is None, (err or '')[:90])
    agg, proc, ctx = AGG(), PROC(), CTX()
    dc = collections.Counter(r['difficulty'] for r in agg.values())
    T.check('집계형 100문항(쉬움 25 · 중간 31 · 어려움 44 · 기권 8)',
            len(agg) == 100 and dc == {'easy': 25, 'medium': 31, 'hard': 44}
            and sum(r['answer_type'] == 'ABSTAIN' for r in agg.values()) == 8, str(dict(dc)))
    kc = collections.Counter(r['kind'] for r in proc.values())
    T.check('절차형 100문항(절차 32 · 판정기준 29 · 이상소견 22 · 부품 17)',
            len(proc) == 100 and kc == {'절차': 32, '판정기준': 29, '이상소견': 22, '부품': 17}, str(dict(kc)))
    # 집계형 프롬프트 = 템플릿.format(q=질문)
    bad, cnt = 0, collections.Counter()
    for (variant, cond), f in sorted(AGG_PROMPT_FILE.items()):
        idx = agg_prompt_index(variant, cond)
        tmpl = open(kp(*TEMPLATE_FILE[(variant, cond)].split('/')), encoding='utf-8').read() \
            if (variant, cond) in TEMPLATE_FILE else None
        for (qid, c, form), r in idx.items():
            if c != cond:
                continue
            cnt[(variant, cond)] += 1
            q = agg[qid]['questions'][form]
            if tmpl is not None:
                bad += tmpl.format(q=q) != r['prompt']
            else:   # 조건 S: 질문마다 사전을 골라 붙인 판 — 끝부분(질문 자리)만 확인
                bad += not r['prompt'].endswith(f'\n[질문]\n{q}\n\n[SQL]\n')
    T.check('집계형 프롬프트 = 템플릿에 질문을 넣은 것(조건 A·C·F × 100문항 × 4문형)',
            bad == 0 and all(v == 400 for v in cnt.values()) and len(cnt) == 3,
            f'{sum(cnt.values()):,}개 · 불일치 {bad}')
    fidx = agg_prompt_index('kit', 'F')
    fn_ok = sum(1 for i in range(1, 101) if os.path.exists(kp('prompts', 'F_nl', f'Q{i:03d}.txt')) and
                open(kp('prompts', 'F_nl', f'Q{i:03d}.txt'), encoding='utf-8').read() == fidx[(f'Q{i:03d}', 'F', 'NL')]['prompt'])
    T.check('붙여넣기용 prompts/F_nl/Q001~Q100 = 조건 F·자연어 프롬프트', fn_ok == 100, f'{fn_ok}/100')
    tmpl = open(F_PROC_TEMPLATE, encoding='utf-8').read()
    pp = PROC_PROMPTS()
    p_ok = sum(1 for pid, c in ctx.items() if tmpl.format(ctx=c['ctx'], q=c['query_en']) == pp[(pid, 'en')]['prompt']
               and tmpl.format(ctx=c['ctx'], q=c['question_ko']) == pp[(pid, 'ko')]['prompt'])
    T.check('절차형 프롬프트 = rag.py ANSWER 템플릿(문맥, 질문) — 영어·한국어판', p_ok == 100, f'{p_ok}/100 × 2')
    pe_ok = sum(1 for pid in ctx if os.path.exists(kp('prompts', 'P_en', f'{pid}.txt')) and
                open(kp('prompts', 'P_en', f'{pid}.txt'), encoding='utf-8').read() == pp[(pid, 'en')]['prompt'])
    T.check('붙여넣기용 prompts/P_en/P001~P100 = 영어판 프롬프트', pe_ok == 100, f'{pe_ok}/100')
    rb = sum(1 for c in ctx.values() if '\n\n'.join(
        f"Record {r['no']}. Title: {r['title']}\nMachine: {r['machine']} / Equipment: {r['equipment']} / "
        f"Date: {r['date']}\nContent: {r['content'].strip()}" for r in c['records']) == c['ctx']
        and {str(r['no']): r['content'] for r in c['records']} == c['bodies'] and len(c['records']) == 4)
    T.check('절차형 문맥 = 기록 4건을 proc_run.py 형식으로 이은 것', rb == 100, f'{rb}/100')
    h = proc_input_hash(list(ctx.values()))
    T.check('절차형 입력 해시(원 proc_run.py 방식) = procedural/input_hash.txt 기록값', bool(EXPECTED_PROC_HASH) and h == EXPECTED_PROC_HASH,
            f'{h} (키트 기록값 {EXPECTED_PROC_HASH or "없음"})')
    g_ok = 0
    for qid, r in agg.items():
        if r['answer_type'] != 'SQL':
            continue
        rows = kit.gold(qid)
        s = [[('NULL' if v is None else str(v)) for v in row] for row in rows]
        g_ok += (sorted(s[:len(r['gold_result']['rows'])]) == sorted(r['gold_result']['rows'])
                 and r['gold_result']['n_rows'] == len(rows))
    T.check('정답 SQL 을 이 DB(1,000건)에서 실행한 결과 = 문항 파일의 정답 결과', g_ok == 92, f'{g_ok}/92')
    # 학생판: 문항 파일과 DB 의 qa 표가 같은 정답을 말하는가(엑셀·DuckDB 로 보는 정답 = 채점 기준).
    # 정답 JSON 의 NULL 은 키트와 같이 문자열 "NULL" 이다 — 글자 그대로 같아야 한다.
    qa = {x[0]: x for x in kit.con.execute('SELECT qid, answer_type, abstain_token, answer_json, trap_kept '
                                           'FROM qa_aggregate').fetchall()}
    same = 0
    for qid, r in agg.items():
        x = qa.get(qid)
        if not x or x[1] != r['answer_type']:
            continue
        if r['answer_type'] == 'ABSTAIN':
            same += x[2] == r['abstain_token'] and x[3] is None and r.get('gold_result') is None
        else:
            j = json.loads(x[3])
            same += (j['rows'] == r['gold_result']['rows'] and j['columns'] == r['gold_result']['columns']
                     and j['n_rows'] == r['gold_result']['n_rows'] and x[4] == r.get('trap_kept'))
    T.check('문항 파일 정답 = DB 의 qa_aggregate(answer_json 글자 그대로 · 기권 토큰 · trap_kept)', same == 100, f'{same}/100')
    pdb = {x[0]: x for x in kit.con.execute('SELECT qid, kind, keep, question_ko, question_en FROM qa_procedural').fetchall()}
    cdb = {(x[0], x[1]): x[2] for x in kit.con.execute('SELECT qid, rec_no, content FROM qa_proc_context').fetchall()}
    keep_of = {'true': True, 'false': False}        # 문항 파일의 keep 이 null(미판정)이면 DB 도 NULL
    p_same = sum(1 for pid, q in proc.items() if pid in pdb and pdb[pid][1] == q['kind']
                 and pdb[pid][2] == keep_of.get(str(q.get('keep')).lower()))
    q_same = sum(1 for pid, q in proc.items() if pid in pdb and pdb[pid][3] == q['question_ko']
                 and pdb[pid][4] == q['question_en'])
    c_same = sum(1 for pid, c in ctx.items() for rr in c['records'] if cdb.get((pid, int(rr['no']))) == rr['content'])
    T.check('절차형 문항·고정 문맥 = DB 의 qa_procedural · qa_proc_context(유형 · keep · 질문 문안 · 문맥 본문)',
            p_same == 100 and q_same == 100 and c_same == 400 and len(cdb) == 400,
            f'문항 {p_same}/100 · 질문 문안(프롬프트와 같은 원 문안) {q_same}/100 · 문맥 기록 {c_same}/400')

    # ── B. 집계형 채점기 ───────────────────────────────────
    T.section('B. 집계형 채점기(run100.py clean + harness.compare)')

    def batch(fn, ids=None):
        out = []
        for qid in (ids or sorted(agg)):
            raw = fn(agg[qid])
            if raw is not None:
                out.append(score_agg(kit, qid, raw))
        return out

    gold_resp = lambda r: r['abstain_token'] if r['answer_type'] == 'ABSTAIN' else r['gold_sql']  # noqa: E731
    b1 = batch(gold_resp)
    s1 = agg_stats(b1)
    T.check('정답 SQL·기권 토큰을 응답으로 → 100/100', s1['strict'] == 100 and s1['norm'] == 100,
            f"엄격 {s1['strict']}/100 · 방언정규화 {s1['norm']}/100 · 기권 {s1['abstain_ok']}/{s1['abstain_total']} · "
            + ' '.join(f"{ko} {s1['by_difficulty'][d]['strict']}/{s1['by_difficulty'][d]['total']}" for d, ko in DIFFS))

    def wrapped(r):
        if r['answer_type'] == 'ABSTAIN':
            return f"```\n{r['abstain_token'].lower()};\n```"
        return f"다음은 요청한 질의다.\n```sql\n{r['gold_sql']}\n```\n위 SQL 을 실행하면 된다."
    s2 = agg_stats(batch(wrapped))
    T.check('코드펜스·앞뒤 설명을 붙여도(기권 토큰은 펜스·소문자·세미콜론) → 100/100', s2['strict'] == 100,
            f"{s2['strict']}/100")
    b3 = batch(lambda r: 'NOT_RECORDED' if r['answer_type'] == 'SQL' else None)
    T.check('정답이 SQL 인 92문항에 기권 토큰 → 전부 오답(과잉 기권)',
            len(b3) == 92 and all(x['strict'] == 'mismatch' for x in b3) and agg_stats(b3)['over_abstain'] == 92,
            f"{sum(x['strict'] == 'mismatch' for x in b3)}/92")
    b4 = batch(lambda r: f"{r['abstain_token']} (기록에 없음)" if r['answer_type'] == 'ABSTAIN' else None)
    T.check('기권 토큰 뒤에 설명을 붙이면 완전일치가 아니어서 → 8문항 전부 실행 오류(E)',
            len(b4) == 8 and all(x['strict'] == 'error' for x in b4), f"{sum(x['strict'] == 'error' for x in b4)}/8")
    b5 = batch(lambda r: r['naive_sql'] if r.get('naive_sql') else None)
    # 학생판: trap_kept = 함정 SQL 이 이 DB 에서도 정답과 다른가(기권 문항은 None — 그 함정은 늘 오답이다)
    gone = [x for x in b5 if agg[x['qid']].get('trap_kept') is False]
    kept = [x for x in b5 if agg[x['qid']].get('trap_kept') is not False]
    T.check('함정 SQL(naive) → 함정이 이 DB 에서도 살아 있는 문항(trap_kept · 기권 문항)은 전부 오답, 사라진 문항은 정답과 같음',
            b5 and all(x['strict'] != 'match' for x in kept) and all(x['strict'] == 'match' for x in gone),
            f"오답 {sum(x['strict'] != 'match' for x in kept)}/{len(kept)} · 함정이 사라진 문항 {len(gone)}개"
            f"({','.join(x['qid'] for x in gone)} — 표본 1,000건에서는 naive SQL 도 같은 답)")
    d_ids = [k for k, r in sorted(agg.items()) if r['gold_sql'] and re.search(r'count\s*\(\s*DISTINCT\s*\(', r['gold_sql'], re.I)]

    def mysqlize(r):
        return re.sub(r'count\s*\(\s*DISTINCT\s*\(([^()]+)\)\s*\)', r'count(DISTINCT \1)', r['gold_sql'], flags=re.I)
    b6 = batch(mysqlize, d_ids)
    T.check('MySQL 식 count(DISTINCT a, b) → 엄격 E · 방언정규화 O',
            b6 and all(x['strict'] == 'error' and x['norm'] == 'match' for x in b6), f'{len(b6)}문항')
    b7 = batch(lambda r: '죄송하지만 이 질문에는 답할 수 없습니다.')
    T.check('SQL 이 없는 문장 → 실행 오류(E) 100/100', all(x['strict'] == 'error' for x in b7),
            f"{sum(x['strict'] == 'error' for x in b7)}/100")
    slow = Kit.__new__(Kit)
    slow.con, slow._gold = kit.con, kit._gold
    slow.runner = SqlRunner(kit.con, 1.0)
    t0 = time.time()
    r8 = score_agg(slow, 'Q003', 'SELECT count(*) FROM master a, master b, pms c, pms d;')   # 학생판: DB 가 작아 하나 더 잇는다
    T.check('SQL 시간 제한(1초로 시험) → 끊고 E', r8['strict'] == 'error' and '시간 초과' in (r8['exec_error'] or '')
            and time.time() - t0 < 30, str(r8['exec_error']))

    # ── C. 절차형 채점기 ───────────────────────────────────
    T.section('C. 절차형 채점기(proc_run.py · proc_metrics.py 규칙)')
    inctx = [pid for pid, q in sorted(proc.items()) if q['evidence_in_context']]
    outctx = [pid for pid, q in sorted(proc.items()) if not q['evidence_in_context']]
    T.check('근거 인용이 문맥 안에 있는 문항 수 · 위치가 원 문맥 기록과 같음',
            len(inctx) == 25 and all(q['evidence_in_context'] == q['evidence_in_context_orig'] for q in proc.values()),
            f'{len(inctx)}/100 · 위치 동일 {sum(q["evidence_in_context"] == q["evidence_in_context_orig"] for q in proc.values())}/100')
    c1 = [score_proc(pid, _fake_proc(proc[pid]['evidence_quote'], proc[pid]['evidence_in_context'][0])) for pid in inctx]
    T.check('비식별 근거 인용 + 맞는 기록번호 → exact 25/25',
            all(r['n_exact'] == 1 and r['cls'] == 'steps' and r['parsed'] and r['json_valid'] for r in c1),
            f"exact {sum(r['n_exact'] for r in c1)}/25")
    agree = 0
    for pid, q in proc.items():
        for rec_no in range(1, 5):
            r = score_proc(pid, _fake_proc(q['evidence_quote'], rec_no))
            agree += (r['rows'][0]['exact'] == (rec_no in q['evidence_in_context'])
                      and r['rows'][0]['anywhere'] == bool(q['evidence_in_context']))
    T.check('기록 1~4 각각을 지목했을 때 exact·anywhere 판정 = 키트에 기록된 근거 위치', agree == 400, f'{agree}/400')
    c3 = [score_proc(pid, _fake_proc(proc[pid]['evidence_quote'], 9)) for pid in inctx]
    T.check('없는 기록번호(9)를 지목 → 기록번호 오기 25/25(찾은 기록 = 첫 근거 위치)',
            all(r['n_misrec'] == 1 and r['rows'][0]['matched_rec'] == proc[r['qid']]['evidence_in_context'][0] for r in c3),
            f"{sum(r['n_misrec'] for r in c3)}/25")
    c4 = [score_proc(pid, _fake_proc(proc[pid]['evidence_quote'], 1)) for pid in outctx]
    T.check('근거 인용이 문맥 밖인 75문항 → 인용 환각으로 판정', all(r['n_halluc'] == 1 for r in c4),
            f"{sum(r['n_halluc'] for r in c4)}/75")
    c5 = [score_proc(pid, _fake_proc('  ' + re.sub(r' ', '\n  ', proc[pid]['evidence_quote'].upper(), count=2) + ' ',
                                     proc[pid]['evidence_in_context'][0])) for pid in inctx]
    T.check('인용의 공백·줄바꿈·대소문자 차이는 무시(sq 정규화) → exact 25/25', all(r['n_exact'] == 1 for r in c5),
            f"{sum(r['n_exact'] for r in c5)}/25")
    nq = [(pid, MEAS.findall(proc[pid]['evidence_quote'])) for pid in inctx]
    nq = [(pid, m[0]) for pid, m in nq if m]
    ok_t = sum(1 for pid, tok in nq if score_proc(pid, _fake_proc(proc[pid]['evidence_quote'], proc[pid]['evidence_in_context'][0],
                                                                    f'기준값은 {tok} 이다'))['rows'][0]['num_ok'] is True)
    ok_f = sum(1 for pid, tok in nq if score_proc(pid, _fake_proc(proc[pid]['evidence_quote'], proc[pid]['evidence_in_context'][0],
                                                                    '기준값은 987.65 mm 이다'))['rows'][0]['num_ok'] is False)
    T.check('측정값 근거(MEAS): 인용에 있는 수치 → num_ok, 없는 수치 → 실패',
            nq and ok_t == len(nq) and ok_f == len(nq), f'수치가 든 근거 {len(nq)}문항 · 맞음 {ok_t} · 잡아냄 {ok_f}')
    p = 'P009'
    q9 = proc[p]
    en_q = next((q['question_en'] for q in proc.values() if q['question_en'].strip().endswith('?')), None)
    echo_pid = next(pid for pid, q in proc.items() if q['question_en'] == en_q)
    cases = [
        ('유효 JSON·단계 0·실제 사유 → 의도적 기권', p, '{"steps": [], "missing": "기록에 해당 절차가 없다."}',
         dict(cls='abstain', json_valid=True, parsed=False, lack=True)),
        ('사유가 빈칸 → 생성 실패', p, '{"steps": [], "missing": ""}', dict(cls='gen_fail', json_valid=True)),
        ('템플릿 문구 복창 → 생성 실패', p, '{"steps":[],"missing":"what the records do not cover; empty string if nothing"}',
         dict(cls='gen_fail', json_valid=True, lack=False)),
        ('질문 복창(물음표) → 생성 실패', echo_pid, json.dumps(dict(steps=[], missing=en_q), ensure_ascii=False),
         dict(cls='gen_fail', json_valid=True, lack=False)),
        ('JSON 아님 → 생성 실패·json_valid 아님', p, '기록을 보면 밸브를 점검해야 합니다.', dict(cls='gen_fail', json_valid=False)),
        ('잘린 JSON → 조각에서 단계 회수(parsed 아님)', p,
         '{"steps":[{"step":"a","record":%d,"quote":%s},{"step":"b","rec' % (
             q9['evidence_in_context'][0], json.dumps(q9['evidence_quote'])),
         dict(cls='steps', json_valid=False, parsed=False, n_steps=1, n_exact=1)),
        ('코드펜스 + 기록번호를 문자열로 → 정상 파싱', p,
         '```json\n{"steps":[{"step":"a","record":"%d","quote":%s}],"missing":""}\n```' % (
             q9['evidence_in_context'][0], json.dumps(q9['evidence_quote'])),
         dict(cls='steps', json_valid=True, parsed=True, n_exact=1)),
        ('"steps": null (원 코드가 멈추던 입력) → 멈추지 않고 기권 판정', p, '{"steps": null, "missing": "관련 기록 없음"}',
         dict(cls='abstain', json_valid=True)),
    ]
    bad = []
    for name, pid, raw, exp in cases:
        r = score_proc(pid, raw)
        if any(r.get(k) != v for k, v in exp.items()):
            bad.append(f"{name}: {dict((k, r.get(k)) for k in exp)}")
    T.check(f'분류·파싱 사례 {len(cases)}가지(abstain · gen_fail · 자리표시자 · 질문 복창 · 잘린 JSON · 펜스)', not bad,
            '; '.join(bad) if bad else ' · '.join(c[0].split(' → ')[1] for c in cases))
    allr = c1 + c3 + c4
    st = proc_stats(allr)
    T.check('요약 산식(인용 충실도 · 기록번호 오기 · 인용 환각)', (st['exact'], st['misrec'], st['halluc'], st['steps']) == (25, 25, 75, 125),
            f"exact {st['exact']} · 오기 {st['misrec']} · 환각 {st['halluc']} / 단계 {st['steps']}")

    # ── D. 응답 파일 채점 ──────────────────────────────────
    T.section('D. 손으로 모은 응답 채점(--score-file)')
    ev4 = proc['P004']
    sample = [
        ('Q001', 'F', 'NL', agg['Q001']['gold_sql'], 'O'),
        ('Q070', 'F', 'NL', 'NO_RELIABLE_VALUE', 'O'),
        ('Q003', 'F', 'NL', 'SELECT 1;', 'X'),
        ('Q004', 'F', 'NL', '-- 행 수\nSELECT count(*) FROM pms;', 'O'),
        ('P004', 'PROC', 'en', _fake_proc(ev4['evidence_quote'], ev4['evidence_in_context'][0]), '인용정확 1/1'),
        ('Q005', 'F', 'NL', '', None),
    ]
    want = [m for *_, m in sample if m]

    def run_file(path, task=None):
        recs, skipped = score_table(read_response_table(path), kit, task)
        return [r['mark'] for r in recs], skipped
    p1 = os.path.join(tmp, 'resp_ko.csv')
    with open(p1, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(COLS6)
        for qid, c, ph, resp, _ in sample:
            w.writerow(['gpt-test', 'v1', '2026-11-10', qid, c, ph, resp, '', '', '', '', ''])
    m1, sk1 = run_file(p1)
    T.check('⑥ 기록양식과 같은 한국어 열(UTF-8 CSV)', m1 == want and len(sk1) == 1, f'{m1} · 빈 응답 {len(sk1)}행 건너뜀')
    p2 = os.path.join(tmp, 'resp_en.csv')
    with open(p2, 'w', encoding='cp949', newline='') as f:
        w = csv.writer(f)
        w.writerow(['qid', 'cond', 'phrasing', 'response'])
        for qid, c, ph, resp, _ in sample:
            w.writerow([qid.lower().replace('00', '0', 1), c, ph.lower(), resp])
    m2, _ = run_file(p2)
    T.check('영어 열 이름(qid·cond·phrasing·response) · cp949 CSV · 문항ID 표기 변형(q01·p04)', m2 == want, str(m2))
    p3 = os.path.join(tmp, 'resp_scored.csv')
    recs3, _ = score_table(read_response_table(p1), kit)
    write_csv(p3, recs3)
    m3, _ = run_file(p3)
    T.check('이 도구가 낸 결과 CSV 를 다시 넣어도 같은 판정(엑셀 수식 방지 따옴표 처리)', m3 == want, str(m3))
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = SHEET6
        ws.append(COLS6)
        for qid, c, ph, resp, _ in sample:
            ws.append(['gpt-test', 'v1', '2026-11-10', qid, c, ph, resp or None, None, None, None, None, None])
        p4 = os.path.join(tmp, 'resp.xlsx')
        wb.save(p4)
        m4, _ = run_file(p4)
        T.check('엑셀(.xlsx) ⑥ChatGPT_기록양식 시트', m4 == want, str(m4))
    except ImportError:
        T.skip('엑셀(.xlsx) ⑥ChatGPT_기록양식 시트', 'openpyxl 없음(CSV 로는 채점 가능)')
    if os.path.exists(F_XLSX):
        try:
            rows_x = read_response_table(F_XLSX)
            ids_x = [norm_qid(d.get('qid')) for d in rows_x]
            T.check(f'기록양식 엑셀 ⑥ 시트를 그대로 읽음(문항ID 200행, {os.path.basename(F_XLSX)})',
                    sorted(i for i in ids_x if i) == sorted(list(agg) + list(proc)),
                    f"{sum(1 for i in ids_x if i)}행 · 응답 있는 행 {sum(1 for d in rows_x if str(d.get('response') or '').strip())}")
        except SystemExit as e:
            if 'openpyxl' in str(e):
                T.skip('기록양식 엑셀 ⑥ 시트를 그대로 읽음', 'openpyxl 없음')
            else:
                T.check('기록양식 엑셀 ⑥ 시트를 그대로 읽음', False, str(e)[:120])
    else:
        T.skip('기록양식 엑셀 ⑥ 시트를 그대로 읽음', '기록양식_200문항.xlsx 가 없다(CSV·응답 폴더 채점은 된다)')

    # 모델명·버전·시험일을 첫 행에만 적은 표 → 아래 행이 이어받아 한 묶음. 회차가 다르면 따로 묶음.
    p5 = os.path.join(tmp, 'resp_firstrow.csv')
    with open(p5, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(COLS6)
        for n, (qid, c, ph, resp, _) in enumerate(sample):
            w.writerow((['gpt-test', 'v1', '2026-11-10'] if n == 0 else ['', '', '']) + [qid, c, ph, resp, '', '', '', '', ''])
        w.writerow(['', '', '', 'Q001', 'F', 'NL', agg['Q001']['gold_sql'], '', '', '', '', '2'])
    r5, sk5 = score_table(read_response_table(p5), kit)
    _, summ5 = summarize_scored(r5, sk5)
    T.check('모델명·버전·시험일이 빈 행은 바로 위 행 값을 이어받음 · 회차가 다르면 다른 묶음',
            all(r['model'] == 'gpt-test' and r['version'] == 'v1' for r in r5)
            and sum(1 for r in r5 if r.get('meta_filled')) == len(r5) - 1
            and sorted((s['group']['phrasing'], s['group']['run']) for s in summ5) == [('NL', ''), ('NL', '2'), ('en', '')],
            f"이어받은 행 {sum(1 for r in r5 if r.get('meta_filled'))} · 묶음 {[(s['group']['model'], s['group']['phrasing'], s['group']['run']) for s in summ5]}")

    # 엑셀 칸만 선택하고 붙여 여러 줄 응답이 아래 행으로 퍼진 모양 → 경고(판정은 그대로)
    spill = [('Q001', '```sql'), ('Q002', 'FROM pms WHERE CRITICAL_YN = \'Y\';'), ('Q003', '```'), ('P001', '"missing": ""}'),
             ('P002', '}'), ('Q004', agg['Q004']['gold_sql']), ('P004', _fake_proc(ev4['evidence_quote'], ev4['evidence_in_context'][0])),
             ('Q070', 'NO_RELIABLE_VALUE')]
    w_rows = [dict(qid=q, response=t, model='m', _row=i) for i, (q, t) in enumerate(spill, 2)]
    r6, _ = score_table(w_rows, kit)
    flagged = [r['qid'] for r in r6 if r.get('paste_warning')]
    T.check('붙여넣기 퍼짐 의심 행 경고(코드펜스 줄만·SQL 중간부터·} 로 시작) — 정상 응답은 경고 없음',
            flagged == ['Q001', 'Q002', 'Q003', 'P001', 'P002'], f'경고 {flagged}')

    # 응답 폴더(--score-dir): Q001.txt · Q070.txt · P004.txt · Q001_2.txt(회차 2)
    rd = os.path.join(tmp, 'responses')
    os.makedirs(rd)
    for nm, body in [('Q001.txt', agg['Q001']['gold_sql']), ('Q070.txt', 'NO_RELIABLE_VALUE'),
                     ('P004.txt', _fake_proc(ev4['evidence_quote'], ev4['evidence_in_context'][0])),
                     ('Q001_2.txt', 'SELECT 1;'), ('메모.txt', '무시')]:
        with open(os.path.join(rd, nm), 'w', encoding='utf-8', newline='\n') as f:
            f.write(body)
    ns = argparse.Namespace(cond='F', phrasing='NL', lang='en', model='웹-시험', model_version='v9')
    r7, _ = score_table(read_response_dir(rd, ns), kit)
    got7 = sorted((r['qid'], r.get('run') or '', r['mark']) for r in r7)
    T.check('응답 폴더 채점(--score-dir): 문항ID 이름 파일 · 회차 접미 · 다른 파일 무시',
            got7 == [('P004', '', '인용정확 1/1'), ('Q001', '', 'O'), ('Q001', '2', 'X'), ('Q070', '', 'O')]
            and all(r['model'] == '웹-시험' and r['version'] == 'v9' and r['cond'] in ('F', 'PROC') for r in r7), str(got7))

    # tool/score_kit.py — run_eval 의 함수를 쓰고, 원 코드가 멈추던 입력에서도 계속 채점하는지
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('score_kit_selftest', kp('tool', 'score_kit.py'))
        skm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(skm)
        odd = [dict(qid='P009', response='{"steps": null, "missing": "관련 기록 없음"}', _row=2),
               dict(qid='P009', response='{"steps": [], "missing": ["a", "b"]}', _row=3),
               dict(qid='P009', response='{"steps": [{"step": "s", "record": 1, "quote": 987654321}], "missing": ""}', _row=4),
               dict(qid='Q001', response=agg['Q001']['gold_sql'], _row=5)]
        rows_k, recs_k, sk_k = skm.score_rows(odd, kit)
        marks_k = [r['mark'] for r in recs_k]
        same = [r['mark'] for r in score_table(read_response_table(p1), kit)[0]] == \
            [r['mark'] for r in skm.score_rows(read_response_table(p1), kit)[1]]
        T.check('tool/score_kit.py = run_eval 채점 함수 · "steps": null · missing 목록 · quote 숫자에서도 멈추지 않음',
                same and marks_k == ['abstain', 'abstain', '인용정확 0/1', 'O'] and not sk_k and len(rows_k) == 4,
                f'판정 {marks_k} · 표본 판정 일치 {same}')
    except Exception as e:
        T.check('tool/score_kit.py 채점 함수', False, f'{type(e).__name__}: {str(e)[:120]}')

    # ── E. HTTP 호출 경로(모의 서버) ───────────────────────
    T.section('E. OpenAI 호환 호출 경로(이 프로세스 안의 모의 서버 — 외부 호출 없음)')
    qid_of_sha = {sha1(r['prompt']): qid for (qid, c, f), r in fidx.items() if c == 'F' and f == 'NL'}
    pid_of_sha = {sha1(r['prompt']): pid for (pid, lang), r in pp.items() if lang == 'en'}

    def responder(n, body):
        """첫 요청은 429(Retry-After 0), 그 뒤로는 프롬프트에 맞는 '정답 응답'을 돌려준다."""
        if n == 1:
            return 429, {'Retry-After': '0'}, {'error': {'message': 'rate limit', 'type': 'requests'}}
        h = sha1(body['messages'][0]['content'])
        if h in qid_of_sha:
            return 200, {}, _ok_payload(gold_resp(agg[qid_of_sha[h]]))
        if h in pid_of_sha:
            q = proc[pid_of_sha[h]]
            return 200, {}, _ok_payload(_fake_proc(q['evidence_quote'], (q['evidence_in_context'] or [1])[0]))
        return 200, {}, _ok_payload('?')
    try:
        mock = MockServer(responder)
    except OSError as e:
        T.skip('모의 서버', f'127.0.0.1 에 임시 포트를 열지 못함({e})')
        mock = None
    if mock:
        try:
            cfg = make_cfg(_ns(base_url=mock.base, api_key='sk-selftest', model='mock-model', preset='openai'), 'agg')
            res = chat(fidx[('Q001', 'F', 'NL')]['prompt'], cfg, log=lambda m: None)
            b = mock.calls[-1]['body'] if mock.calls else {}
            T.check('429 + Retry-After 뒤 재시도해 성공', res['error'] is None and res['attempts'] == 2 and len(mock.calls) == 2,
                    f"시도 {res['attempts']} · 응답 {res['content'][:40]!r}")
            T.check('요청 본문(openai 프리셋): model · messages · temperature 0 · max_tokens 400 · 인증 머리글',
                    b.get('model') == 'mock-model' and b.get('temperature') == 0.0 and b.get('max_tokens') == 400
                    and b['messages'][0]['role'] == 'user' and mock.calls[-1]['auth'] == 'Bearer sk-selftest'
                    and 'top_k' not in b, json.dumps({k: v for k, v in b.items() if k != 'messages'}))
            cfg2 = make_cfg(_ns(base_url=mock.base, preset='auto', no_temperature=True), 'agg')
            chat('x', cfg2, log=lambda m: None)
            b2 = mock.calls[-1]['body']
            T.check('--no-temperature → temperature 를 보내지 않음', 'temperature' not in b2, json.dumps({k: v for k, v in b2.items() if k != 'messages'}))
            cfg3 = make_cfg(_ns(base_url=mock.base, preset='auto'), 'proc')
            chat('x', cfg3, log=lambda m: None)
            b3 = mock.calls[-1]['body']
            T.check('로컬 주소 → llama 프리셋 = 원 실험 본문(temperature 0 · top_k 1 · seed 42 · max_tokens 2200 · thinking 끔, model 없음)',
                    {k: v for k, v in b3.items() if k != 'messages'} == dict(temperature=0.0, top_k=1, seed=42, max_tokens=2200,
                                                                            chat_template_kwargs=dict(enable_thinking=False))
                    and mock.calls[-1]['auth'] is None, json.dumps({k: v for k, v in b3.items() if k != 'messages'}))
            cfgo = make_cfg(_ns(base_url='https://api.openai.com/v1', api_key='k', model='m'), 'agg')
            T.check('api.openai.com → max_completion_tokens 이름으로 보냄', 'max_completion_tokens' in build_body('x', cfgo)
                    and 'max_tokens' not in build_body('x', cfgo), json.dumps(request_summary(cfgo)))
            out_dir = os.path.join(tmp, 'results')
            combo = dict(task='agg', cond='F', phrasing='NL', variant='kit')
            items = [dict(qid=q, prompt=fidx[(q, 'F', 'NL')]['prompt'], question=agg[q]['questions']['NL'],
                          prompt_sha1=sha1(fidx[(q, 'F', 'NL')]['prompt'])) for q in ('Q001', 'Q070', 'Q086')]
            s = run_combo(combo, items, cfg, kit, out_dir, 'mock_agg', log=lambda m: None, quiet=True)
            n_before = len(mock.calls)
            s_again = run_combo(combo, items, cfg, kit, out_dir, 'mock_agg', resume=True, log=lambda m: None, quiet=True)
            files = sorted(os.listdir(out_dir))
            T.check('집계형 전 과정(호출 → 추출 → 키트 DB 실행 → 채점 → CSV·JSONL·요약·원 형식 JSON)',
                    s and s['strict'] == 3 and s['abstain_ok'] == 1 and len(files) == 5,
                    f"{s['strict']}/3 · 파일 {files}")
            T.check('--resume: 끝난 문항은 다시 보내지 않음', len(mock.calls) == n_before and s_again['strict'] == 3,
                    f'추가 호출 {len(mock.calls) - n_before}건')
            cfgp = make_cfg(_ns(base_url=mock.base, api_key='sk-selftest', model='mock-model', preset='openai'), 'proc')
            pitems = [dict(qid=x, prompt=pp[(x, 'en')]['prompt'], question=proc[x]['question_en'],
                           prompt_sha1=sha1(pp[(x, 'en')]['prompt'])) for x in ('P004', 'P001')]
            sp = run_combo(dict(task='proc', cond='PROC', phrasing='en', variant='kit'), pitems, cfgp, None, out_dir,
                           'mock_proc', log=lambda m: None, quiet=True)
            cj = json.load(open(os.path.join(out_dir, 'proc100_mock_proc.json'), encoding='utf-8'))
            T.check('절차형 전 과정(호출 → JSON 파싱 → 인용 대조 → 요약 · proc100 형식 JSON)',
                    sp and sp['exact'] == 1 and sp['halluc'] == 1 and cj['input_hash'] == EXPECTED_PROC_HASH
                    and [x['id'] for x in cj['results']] == [1, 4],
                    f"exact {sp['exact']} · 환각 {sp['halluc']} · 입력해시 {cj['input_hash']}")
            bad_mock = MockServer(lambda n, b: (400, {}, {'error': {'message': "Unsupported value: 'temperature' does not "
                                                                    "support 0 with this model.", 'code': 'unsupported_value'}}))
            try:
                chat('x', make_cfg(_ns(base_url=bad_mock.base, api_key='k', model='m', preset='openai'), 'agg'), log=lambda m: None)
                T.check('temperature 를 받지 않는 모델 → 멈추고 --no-temperature 안내', False, '예외 없음')
            except FatalApiError as e:
                T.check('temperature 를 받지 않는 모델 → 멈추고 --no-temperature 안내', '--no-temperature' in str(e)
                        and len(bad_mock.calls) == 1, f'시도 {len(bad_mock.calls)}회')
            finally:
                bad_mock.close()
        finally:
            mock.close()

    # ── F. 배포 점검 ───────────────────────────────────────
    T.section('F. 배포 점검(eval 폴더만 복사해도 되는가 — 학생이 만든 results/ · responses/ 는 빼고 본다)')
    forbid = re.compile(r'(PRIVATE|secret_seed|code_map|deid_rules|^maint\.duckdb$|^demo\.duckdb$|^bm25.*\.pkl$|'
                        r'^deid_maint\.duckdb$|^정답샘플_200문항\.xlsx$|^answer_sample_200\.xlsx$)', re.I)
    bad_files, caches, texts = [], [], []
    for dp, dn, fn in os.walk(KIT):
        dn[:] = [d for d in dn if d not in OWN_OUTPUT_DIRS]
        for d in dn:
            if d == '__pycache__':
                caches.append(show_path(os.path.join(dp, d)))
        for f in fn:
            p = os.path.join(dp, f)
            if forbid.search(f):
                bad_files.append(show_path(p))
            if f.endswith('.pyc'):
                caches.append(show_path(p))
            if f.lower().endswith(('.py', '.txt', '.md', '.json', '.jsonl', '.csv')):
                texts.append(p)
    T.check('열쇠·실제 DB·17만 행 키트 DB·키트 정답 엑셀 없음(PRIVATE·시드·대응표·원 DB·검색 색인)', not bad_files,
            ', '.join(bad_files) or '0개')
    T.check('__pycache__ · .pyc 없음(안에 절대경로가 들어감)', not caches, ', '.join(caches) or '0개')
    parent = os.path.dirname(KIT)
    pats = [re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+', re.I),
            re.compile(r'(?<![\w.])/(?:home|Users)/[\w.-]+/', re.I),
            re.compile(re.escape(parent.replace('\\', '/')), re.I), re.compile(re.escape(parent.replace('/', '\\')), re.I)]
    hits = []
    for p in texts:
        try:
            s = open(p, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        if any(x.search(s) for x in pats):
            hits.append(show_path(p))
    n_x = 0
    for xp in (F_XLSX_KO, F_XLSX_ASCII):
        if os.path.exists(xp):
            n_x += 1
            with zipfile.ZipFile(xp) as z:
                for nm in z.namelist():
                    s = z.read(nm).decode('utf-8', 'replace')
                    if any(x.search(s) for x in pats):
                        hits.append(f'{show_path(xp)}:{nm}')
    T.check('키트 텍스트·엑셀에 절대경로 없음', not hits, (', '.join(hits[:5]) if hits else f'{len(texts)}개 파일 + 엑셀 {n_x}개'))
    big = []
    for dp, dn, fn in os.walk(KIT):
        dn[:] = [d for d in dn if d not in OWN_OUTPUT_DIRS]
        for f in fn:
            if not f.lower().endswith('.duckdb'):
                continue
            p = os.path.join(dp, f)
            if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(a.db)):
                n = kit.con.execute('SELECT count(*) FROM master').fetchone()[0]
            else:
                try:
                    c2 = duckdb.connect(p, read_only=True)
                    n = c2.execute('SELECT count(*) FROM master').fetchone()[0]
                    c2.close()
                except Exception as e:
                    n = type(e).__name__
            if n != EXPECTED_ROWS['master']:
                big.append(f'{show_path(p)}(master {n})')
    T.check('DuckDB 파일은 학생용 DB(문서 1,000건)뿐 — 17만 행 키트 DB 없음', not big, ', '.join(big) or '이상 없음')
    rd = kp('results')
    if os.path.isdir(rd) and os.listdir(rd):
        T.skip('results/ 폴더', f'{len(os.listdir(rd))}개 파일 — 모델 응답이 들어 있다. 배포할 때는 빼거나 함께 보낼지 정한다')
    else:
        T.check('results/ 폴더 비어 있음(시험 결과를 함께 내보내지 않음)', True, '없음')


# ═══════════════════════ 명령줄 ═══════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='run_eval.py', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='MAI SMA 학생판 평가기(학생용 DB 정비 문서 1,000건 기준) — 집계형(SQL)·절차형(인용) 경로를 '
                    '원 규칙으로 시험·채점한다.',
        epilog='예)\n'
               '  python run_eval.py --self-test\n'
               '  python run_eval.py --dry-run --cond F --phrasing NL --ids Q001\n'
               '  python run_eval.py --task agg --cond F --phrasing NL --model gpt-4.1 --ids Q001-Q005\n'
               '  python run_eval.py --task proc --model gpt-4.1 --no-temperature\n'
               '  python run_eval.py --score-file 기록양식_200문항.xlsx\n'
               '  python run_eval.py --score-dir responses --model ChatGPT-웹 --model-version "<화면의 모델 이름>"\n'
               '  python run_eval.py --task agg --base-url http://127.0.0.1:<포트>/v1 --cond F --phrasing NL --tag local_12b\n')
    m = ap.add_argument_group('모드(하나만, 없으면 API 시험)')
    m.add_argument('--self-test', action='store_true', help='채점기·키트 자체 검사(외부 호출 없음)')
    m.add_argument('--dry-run', action='store_true', help='완성 프롬프트만 출력(호출 없음)')
    m.add_argument('--score-file', metavar='FILE', help='손으로 모은 응답 채점: .csv · .xlsx(⑥ 시트) · .jsonl')
    m.add_argument('--score-dir', metavar='DIR', help='문항ID 이름의 응답 파일(Q001.txt · P001.txt, 회차는 Q001_2.txt) 폴더 채점. '
                                                     '모델명은 --model, 버전은 --model-version, 집계형 조건·문형은 --cond · --phrasing')
    s = ap.add_argument_group('문항')
    s.add_argument('--task', choices=['agg', 'proc'], default=None, help='agg=집계형(기본) · proc=절차형')
    s.add_argument('--cond', default='F', help='집계형 조건 A·C·F — 쉼표로 여러 개 (기본 F = 웹 화면과 같은 프롬프트)')
    s.add_argument('--phrasing', default='NL', help='집계형 문형 orig·L2·L1·NL — 쉼표로 여러 개 (기본 NL)')
    s.add_argument('--variant', choices=['kit'], default='kit',
                   help='kit=시연 도구·웹 화면과 같은 프롬프트 생성 로직(학생판은 이 판만 있다)')
    s.add_argument('--lang', choices=['en', 'ko'], default='en', help='절차형 질문 언어(기본 en = 원 실험)')
    s.add_argument('--ids', default='all', help='문항 범위. 예: Q001-Q010,Q020 · P001-P005 · 1-10 (기본 전체)')
    s.add_argument('--limit', type=int, default=0, help='고른 문항 중 앞의 N개만')
    c = ap.add_argument_group('연결(OpenAI 호환 /v1/chat/completions)')
    c.add_argument('--base-url', default=None, help='기본: 환경변수 OPENAI_BASE_URL 또는 https://api.openai.com/v1')
    c.add_argument('--api-key', default=None, help='기본: 환경변수 OPENAI_API_KEY (명령줄보다 환경변수 권장)')
    c.add_argument('--model', default=None, help='모델 이름(기본: 환경변수 OPENAI_MODEL). 로컬 llama-server 는 생략 가능')
    c.add_argument('--model-version', default=None, help='결과의 "버전" 칸에 적을 값(기본: 서버가 돌려준 모델 이름)')
    c.add_argument('--preset', choices=['auto', 'openai', 'llama'], default='auto',
                   help='auto: 127.0.0.1·localhost 면 llama(원 실험 본문: top_k 1·seed 42·thinking 끔), 아니면 openai')
    c.add_argument('--temperature', type=float, default=0.0, help='기본 0(원 실험)')
    c.add_argument('--no-temperature', action='store_true', help='temperature 를 보내지 않음(추론형 모델 등)')
    c.add_argument('--max-tokens', type=int, default=None, help='출력 상한(기본 집계형 400 · 절차형 2200, 0=보내지 않음)')
    c.add_argument('--max-tokens-param', choices=['auto', 'max_tokens', 'max_completion_tokens'], default='auto',
                   help='auto: api.openai.com 이면 max_completion_tokens, 아니면 max_tokens')
    c.add_argument('--seed', type=int, default=None, help='seed 를 보냄(지원하는 서버만)')
    c.add_argument('--extra', default=None, help='요청 본문에 덧붙일 JSON 객체. 예: {"reasoning_effort":"low"}')
    c.add_argument('--timeout', type=float, default=None, help='요청 하나의 시간 제한(초, 기본 집계형 300 · 절차형 600)')
    c.add_argument('--retries', type=int, default=5, help='429·5xx·연결 오류 재시도 횟수(기본 5)')
    c.add_argument('--backoff', type=float, default=2.0, help='재시도 대기 시작값(초, 2배씩, 최대 120). Retry-After 가 오면 그것을 따름')
    c.add_argument('--interval', type=float, default=0.0, help='요청 사이 최소 간격(초) — 분당 요청 한도가 낮을 때')
    c.add_argument('--yes', action='store_true', help='외부 API 비용 확인 질문을 건너뜀')
    o = ap.add_argument_group('실행·출력')
    o.add_argument('--db', default=os.environ.get('KIT_DB') or DEFAULT_DB, help='평가 DB(기본 db/MAI_SMA_학생용DB_1000.duckdb)')
    o.add_argument('--sql-timeout', type=float, default=120.0, help='모델 SQL 실행 시간 제한(초, 기본 120, 0=없음)')
    o.add_argument('--out-dir', default=None, help='결과 폴더(기본 results/). --self-test 는 줄 때만 보고서를 쓴다')
    o.add_argument('--tag', default=None, help='결과 파일 이름(기본: 과제_조건_문형_판_모델)')
    o.add_argument('--resume', action='store_true', help='같은 태그의 끝난 문항은 건너뛰고 이어서')
    o.add_argument('--overwrite', action='store_true', help='같은 태그의 결과를 덮어씀')
    a = ap.parse_args(argv)
    modes = sum(bool(x) for x in (a.self_test, a.dry_run, a.score_file, a.score_dir))
    if modes > 1:
        ap.error('--self-test · --dry-run · --score-file · --score-dir 중 하나만 쓴다')
    a.task_given = a.task
    a.task = a.task or 'agg'
    if a.self_test:
        return self_test(a)
    if a.score_file:
        return cmd_score_file(a)
    if a.score_dir:
        return cmd_score_dir(a)
    if a.dry_run:
        return cmd_dry_run(a)
    return cmd_run(a)


if __name__ == '__main__':
    sys.exit(main())
