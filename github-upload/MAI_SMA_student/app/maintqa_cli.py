# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/maintqa_cli.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · LLM: GGUF·llama-server 고정 대신 config.json 의 실행기(llm_client). Server 는 응답 확인 + 선택적 직접 기동
#   · 옵션: --model · --port · --no-server 대신 --config
#   · 시작 안내: 학생용 DB(정비 문서 1,000건)와 색인 구성(정비 문서 + 참고 기록)을 보인다
#   · 집계·절차 경로(run_agg · run_proc · 인용 검증)는 바꾸지 않았다
"""정비이력 질의 도구 — 선내 오프라인 사용.

연구계획서 2.1 이 약속한 산출물이다. 인터넷 연결이나 외부 서비스 가입 없이
실행 파일과 모델 파일만으로 동작한다.

질문을 받으면 라우터가 두 경로 중 하나로 보낸다.
  집계형  "발전기 정비가 몇 건인가"  → SQL 생성 → DuckDB 실행 → 결과
  절차형  "이 작업 어떻게 하나"      → BM25 검색 → 근거와 함께 생성 → 인용 검증

실험 결과를 그대로 반영한 설계
  · 프롬프트는 조건 S(선택 사전)를 쓴다. 전체 사전과 성능이 대등하면서
    토큰이 절반이다(12B 자연어 71 대 73).
  · 답할 수 없는 질문에는 답하지 않는다. NOT_RECORDED / NO_RELIABLE_VALUE
    를 그대로 사용자에게 보인다. 없는 데이터로 숫자를 지어내는 것이
    이 시스템의 가장 큰 위험이다.
  · 절차형 답변의 인용구는 전부 원문과 대조해 표시한다. 대조에 실패한
    문장은 [미검증]으로 찍는다. 검증 없이 보여주면 환각을 거를 방법이 없다.
  · 근거 없음으로 기권하면 검색 범위를 넓혀 재시도할지 묻는다.
    12B 의 기권 39건 중 13건은 14B 가 답할 수 있었던 문항이다.
  · JSON 파싱에 실패하면 한 번 재질의한다. 소형 모델에서 21~31% 발생한다.

사용(패키지 폴더에서):
  python app/maintqa_cli.py                    대화형
  python app/maintqa_cli.py -q "질문"          한 번만 묻고 종료
  python app/maintqa_cli.py --config 설정.json 다른 설정 파일
학생 이식판: LLM 은 config.json 에 적은 실행기(Ollama · LM Studio · llama.cpp 등)를 쓴다.
"""
import os, sys, re, json, time, argparse, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)).replace(chr(92), '/')
# 자기 폴더를 먼저 본다. 예전에는 벤치 폴더의 절대경로를 맨 앞에 끼워 넣었는데,
# 그러면 harness 가 벤치 사본으로 적재되어 시연 DB 가 아니라 실제 DB 를 물고 돈다.
sys.path.insert(0, HERE)


def _dir(*cands):
    """있는 폴더를 먼저 고른다. 배포본에는 시연용만 들어 있을 수 있다."""
    for c in cands:
        if os.path.isdir(c):
            return os.path.abspath(c).replace(chr(92), '/')
    return cands[0]


sys.dont_write_bytecode = True
import sma_config as CONF     # 학생 이식판: 설정 파일
import llm_client as LLM       # 학생 이식판: OpenAI 호환 호출

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# 학생 이식판: 모델은 config.json 이 정한다(원 시연판의 GGUF 목록 MODELS 는 쓰지 않는다).
# 실험에서 배포 기준(원출력 재현 · 기권 지시이행 · 인용 충실도)을 통과한 두 모델.
# 둘 사이의 차이는 어느 축에서도 유의하지 않아 순위를 두지 않는다.

C = dict(hd='\033[1;36m', ok='\033[32m', warn='\033[33m', err='\033[31m',
         dim='\033[90m', b='\033[1m', x='\033[0m')
if os.name == 'nt' and not os.environ.get('WT_SESSION'):
    try:
        import colorama
        colorama.just_fix_windows_console()
    except Exception:
        C = {k: '' for k in C}


def cprint(s=''):
    print(s.format(**C))


# ──────────────────────────── 서버 ────────────────────────────
class Server:
    """추론 서버. 학생 이식판: 이미 켜 둔 실행기에 붙는 것이 기본이다.
    config.json 의 llama_server 에 실행 파일과 모델 경로가 있을 때만 직접 띄운다."""

    def __init__(self, cfg):
        self.p, self.cfg = None, cfg
        self.cli = LLM.Client(cfg)

    def start(self):
        if self.alive():
            print(C['ok'] + 'LLM 서버 응답 확인 — ' + CONF.summary(self.cfg) + C['x'])
            return True
        if not LLM.embedded_ready(self.cfg):
            print(C['err'] + 'LLM 서버가 응답하지 않습니다: ' + self.cfg['base_url'] + C['x'])
            print('  ' + LLM.start_hint(self.cfg['preset']))
            return False
        cprint('{dim}llama-server 를 띄우는 중…{x}')
        try:
            self.p = LLM.launch_llama_server(self.cfg, os.path.join(CONF.LOGS, 'llama_server_cli.log'))
        except Exception as e:
            print(C['err'] + '  ' + str(e) + C['x'])
            return False
        for i in range(200):
            time.sleep(3)
            if self.alive():
                cprint('{ok}' + f'준비 완료 ({i*3}초)' + '{x}')
                return True
            if self.p.poll() is not None:
                cprint('{err}서버가 조기 종료했습니다. logs/llama_server_cli.log 를 보십시오.{x}')
                return False
        cprint('{err}서버 기동 시간초과{x}')
        return False

    def alive(self):
        return self.cli.health(max_age=0)

    def stop(self):
        if self.p and self.p.poll() is None:
            self.p.kill()
            try:
                self.p.wait(timeout=15)
            except Exception:
                pass


def ask_llm(prompt, port=None, mx=1200, retries=2):
    """학생 이식판: config.json 의 실행기로 보낸다. port 는 원 호출 모양을 지키려고 남겼다(쓰지 않음)."""
    r = LLM.default_client().chat(prompt, mx, retries=retries)
    if r['error']:
        return f"__ERROR__ {r['error']}", 0.0
    return r['content'], r['sec']


# ──────────────────────────── 집계형 ────────────────────────────
def clean_sql(s):
    s = re.sub(r'^\s*```(?:sql)?\s*', '', s.strip(), flags=re.I)
    s = re.sub(r'```\s*$', '', s.strip()).strip()
    from harness import is_abstain
    if is_abstain(s):
        return s.strip().strip('`').strip().rstrip(';').strip().upper()
    m = re.search(r'(SELECT|WITH)\b', s, re.I)
    if m:
        s = s[m.start():]
    if ';' in s:
        s = s[:s.index(';') + 1]
    return s.strip()


ABSTAIN_MSG = {
    'NOT_RECORDED':
        ('이 시스템에 기록이 없습니다.',
         '소모 부품 기록은 전체 정비문서의 3.21% 에만 존재합니다. '
         '해당 정비에 무엇을 얼마나 썼는지는 이 데이터로 답할 수 없습니다.'),
    'NO_RELIABLE_VALUE':
        ('기록은 있으나 신뢰할 수 없습니다.',
         '소요시간·투입인원은 두 곳에 있고 둘 다 쓸 수 없습니다. '
         'pms 쪽은 99% 가 0 이고, master 쪽은 73% 가 값 1.0 에 몰려 있어 '
         '작업 규모와의 상관이 0.16 에 불과합니다. 합산하면 틀린 숫자가 나옵니다.'),
}


def agg_schema(ctx):
    """조건 F 의 스키마 — 전체 컬럼 설명을 붙인 DDL.

    질문과 무관하게 고정이라 한 번 만들어 두고 계속 쓴다. 고정이어야
    추론 서버의 접두부 캐시가 듣는다. 질문마다 붙는 설명이 달라지는
    조건 S 는 토큰이 절반인데도 응답이 7배 느렸다(76.2초 대 10.6초).
    """
    import build_prompt100 as BP
    if ctx.get('sc') is None:
        sc = BP.SC
        if sc is None:                  # 사전 파일이 없으면 조건 C 로 내려간다
            sc, n = BP.SCHEMA, 0
        else:
            n = sum(1 for ln in sc.split('\n') if '  -- ' in ln)
        ctx['sc'], ctx['ncol'] = sc, n
    return ctx['sc'], ctx['ncol']


def run_agg(q, port, ctx):
    from harness import run, repair_dialect
    import build_prompt100 as BP
    sc, n_cols = agg_schema(ctx)
    prompt = (BP.HEAD + sc + '\n' + BP.RULES + BP.FEWSHOT
              + BP.TAIL.replace('{q}', q))
    raw, dt = ask_llm(prompt, port, mx=600)
    if raw.startswith('__ERROR__'):
        cprint('{err}모델 호출 실패: ' + raw[:120] + '{x}')
        return
    sql = clean_sql(raw)
    if sql in ABSTAIN_MSG:
        head, why = ABSTAIN_MSG[sql]
        cprint('{warn}■ ' + head + '{x}')
        print('  ' + why)
        ctx['last'] = dict(kind='agg', sql=sql, cols=n_cols)
        return
    pred, err = run(ctx['con'], sql)
    if pred is None:
        sql2 = repair_dialect(sql)
        if sql2 != sql:
            pred, err = run(ctx['con'], sql2)
            if pred is not None:
                sql = sql2
    if pred is None:
        cprint('{err}■ SQL 실행 실패{x}')
        print('  ' + str(err)[:200])
        cprint('{dim}  /sql 로 생성된 질의를 볼 수 있습니다.{x}')
        ctx['last'] = dict(kind='agg', sql=sql, cols=n_cols, err=err)
        return
    cprint('{ok}■ 결과{x}' + f'  {{dim}}({len(pred)}행, {dt:.1f}초){{x}}')
    show_rows(pred)
    ctx['last'] = dict(kind='agg', sql=sql, cols=n_cols, rows=pred)


def show_rows(rows, limit=20):
    if not rows:
        print('  (해당하는 행이 없습니다)')
        return
    for r in rows[:limit]:
        vals = [('—' if v is None else str(v)) for v in r]
        print('  ' + ' | '.join(vals))
    if len(rows) > limit:
        cprint('{dim}  … 외 ' + f'{len(rows)-limit}행{{x}}')


# ──────────────────────────── 절차형 ────────────────────────────
def run_proc(q, port, ctx, k=4, tried=False):
    from rag import bm25, ANSWER, sq
    q_en = translate(q, port)
    hits = bm25(ctx['idx'], q_en, k=k)
    if not hits:
        cprint('{warn}■ 관련 기록을 찾지 못했습니다.{x}')
        return
    bodies = {str(i): h[1]['meta'][6] for i, h in enumerate(hits, 1)}
    titles = [h[1]['meta'][2] for h in hits]
    context = '\n\n'.join(
        f"Record {i}. Title: {h[1]['meta'][2]}\nMachine: {h[1]['meta'][3]} / "
        f"{h[1]['meta'][4]}\nDate: {h[1]['meta'][5]}\n{h[1]['meta'][6]}"
        for i, h in enumerate(hits, 1))
    raw, dt = ask_llm(ANSWER.format(ctx=context, q=q_en), port, mx=2200)
    obj = parse_json(raw)
    if obj is None:                      # 소형 모델에서 21~31% 발생 - 한 번 재질의
        cprint('{dim}  출력 형식이 깨져 한 번 더 묻습니다…{x}')
        raw, dt2 = ask_llm(ANSWER.format(ctx=context, q=q_en) +
                           '\n\nReturn ONLY valid JSON.', port, mx=2200)
        dt += dt2
        obj = parse_json(raw)
    if obj is None:
        cprint('{err}■ 출력 형식을 읽지 못했습니다.{x}')
        cprint('{dim}  /raw 로 원문을 볼 수 있습니다.{x}')
        ctx['last'] = dict(kind='proc', raw=raw, titles=titles)
        return
    steps = obj.get('steps') or []
    missing = (obj.get('missing') or '').strip()
    ctx['last'] = dict(kind='proc', raw=raw, titles=titles,
                       bodies=bodies, steps=steps, q_en=q_en, k=k)
    if not steps:
        cprint('{warn}■ 근거가 부족해 답하지 않았습니다.{x}')
        if missing:
            print('  모자란 것: ' + missing[:300])
        print()
        cprint('{dim}  검색 범위를 넓혀 다시 시도할 수 있습니다.{x}')
        if not tried and ask_yes('  범위를 넓혀 재검색할까요?'):
            return run_proc(q, port, ctx, k=k * 3, tried=True)
        return
    cprint('{ok}■ 절차{x}' + f'  {{dim}}({len(steps)}단계, 근거 {k}건, {dt:.1f}초){{x}}')
    n_ok = 0
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            continue
        txt = str(s.get('step', '')).strip()
        quote = str(s.get('quote', '')).strip()
        try:
            rec = int(str(s.get('record')).strip())
        except (TypeError, ValueError):
            rec = None
        body = bodies.get(str(rec), '')
        exact = bool(quote and sq(quote) and sq(quote) in sq(body))
        where = rec
        if not exact and quote:
            for kk, b in bodies.items():
                if sq(quote) and sq(quote) in sq(b):
                    exact, where = True, int(kk)
                    break
        n_ok += exact
        mark = '{ok}[검증]{x}' if exact else '{err}[미검증]{x}'
        cprint(f'  {i}. ' + mark + f' {txt}')
        if quote:
            src = f'기록 {where}' if exact else '대조 실패'
            cprint('{dim}       근거(' + src + '): "' + quote[:110] + '"{x}')
    print()
    rate = n_ok / max(1, len(steps)) * 100
    col = 'ok' if rate >= 90 else ('warn' if rate >= 70 else 'err')
    cprint('{' + col + '}' + f'  인용 검증 {n_ok}/{len(steps)} ({rate:.0f}%){{x}}'
           + '{dim}  — [미검증] 문장은 근거 문서에 없습니다. 그대로 믿지 마십시오.{x}')
    if missing:
        cprint('{warn}  기록에 없는 항목: {x}' + missing[:250])


def parse_json(raw):
    txt = re.sub(r'```\w*', '', raw).strip()
    m = re.search(r'\{.*\}', txt, re.S)
    try:
        o = json.loads(m.group(0)) if m else None
    except Exception:
        o = None
    return o if isinstance(o, dict) else None


TRANS_P = ("Translate the following Korean question about ship maintenance "
           "into English. Output only the translation.\n\n{q}")


def translate(q, port):
    if not re.search(r'[가-힣]', q):
        return q
    out, _ = ask_llm(TRANS_P.format(q=q), port, mx=200)
    if out.startswith('__ERROR__'):
        return q
    return out.strip().strip('"').split('\n')[0]


def ask_yes(prompt):
    try:
        return input(prompt + ' [y/N] ').strip().lower().startswith('y')
    except (EOFError, KeyboardInterrupt):
        return False


# ──────────────────────────── 본체 ────────────────────────────
HELP = """
  질문을 한국어로 그대로 입력하십시오.

    발전기 정비가 몇 건이야?              → 집계 (SQL)
    과급기 물세척 어떻게 해?              → 절차 (기록 검색)

  명령
    /sql      마지막 집계 질의의 SQL 과 선택된 컬럼 설명
    /raw      마지막 절차형 답변의 원문
    /source   마지막 절차형 답변이 참조한 기록
    /model    현재 모델
    /help     이 도움말
    /quit     종료
"""


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('-q', '--question')
    ap.add_argument('--config', default=None, help='설정 파일(기본: 패키지의 config.json)')
    ap.add_argument('-h', '--help', action='store_true')
    a = ap.parse_args()
    a.port = None                      # 원 호출 모양 유지(쓰지 않음)
    if a.help:
        print(__doc__)
        print(HELP)
        return 0
    try:
        cfg = CONF.load(a.config)
    except CONF.ConfigError as e:
        cprint('{err}설정 오류{x}')
        print('  ' + str(e).replace('\n', '\n  '))
        return 2
    # 하위 모듈(harness·rag·sel_dict·build_prompt100)이 같은 파일을 보도록 고정한다
    os.environ['MAINT_DB'] = cfg['db']
    os.environ['BM25_IDX'] = cfg['index']
    os.environ['MAI_SMA_DATADICT'] = cfg['datadict']
    LLM.default_client(cfg)

    cprint('{hd}╭─ 선박 정비이력 질의 도구 (학생 이식판) ───────────╮{x}')
    cprint('{hd}│{x} 이 PC 의 LLM · 학생용 DB(1,000건) · 외부 통신 없음{hd}│{x}')
    cprint('{hd}╰───────────────────────────────────────────────────╯{x}')
    print(C['dim'] + CONF.summary(cfg) + C['x'])
    for w in cfg.get('_warnings') or []:
        print(C['warn'] + '  주의: ' + w + C['x'])

    srv = Server(cfg)
    if not srv.start():
        return 3

    from harness import get_con, schema_ddl
    from rag import load_index
    from router import route_rule
    cprint('{dim}데이터베이스와 검색 색인 여는 중…{x}')
    ctx = dict(con=get_con(), schema=schema_ddl(), idx=load_index(), last=None)
    n_ref = sum(1 for d in ctx['idx']['docs'] if d['meta'][0] == 'QA_REF')
    cprint('{dim}' + f"정비기록 색인 {ctx['idx']['N']:,}건 준비(정비 문서 {ctx['idx']['N'] - n_ref:,} + 참고 기록 {n_ref:,})"
           + '{x}')
    print(HELP)

    def handle(q):
        kind, conf = route_rule(q)
        label = '집계' if kind == 'AGG' else '절차'
        cprint('{dim}→ ' + f'{label} 질의로 처리합니다'
               + ('' if conf >= 1.0 else ' (판단 불확실)') + '{x}')
        if kind == 'AGG':
            run_agg(q, a.port, ctx)
        else:
            run_proc(q, a.port, ctx)

    try:
        if a.question:
            handle(a.question)
            return 0
        while True:
            try:
                q = input('\n질문> ').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            if q in ('/quit', '/q', 'exit'):
                break
            if q == '/help':
                print(HELP)
                continue
            if q == '/model':
                print('  ' + CONF.summary(cfg))
                continue
            if q in ('/sql', '/raw', '/source'):
                show_last(q, ctx)
                continue
            if q.startswith('/'):
                cprint('{warn}  알 수 없는 명령입니다. /help{x}')
                continue
            handle(q)
    finally:
        srv.stop()
        cprint('\n{dim}종료했습니다.{x}')
    return 0


def show_last(cmd, ctx):
    L = ctx.get('last')
    if not L:
        cprint('{warn}  아직 질의한 것이 없습니다.{x}')
        return
    if cmd == '/sql':
        if L['kind'] != 'agg':
            cprint('{warn}  마지막 질의는 집계형이 아닙니다.{x}')
            return
        print('  ' + L['sql'])
        if L.get('cols'):
            cprint('{dim}  붙인 컬럼 설명 ' + f"{L['cols']}개{{x}}")
    elif cmd == '/raw':
        print(L.get('raw', '(없음)')[:3000])
    elif cmd == '/source':
        for i, t in enumerate(L.get('titles', []), 1):
            print(f'  기록 {i}. {t}')
            body = (L.get('bodies') or {}).get(str(i), '')
            if body:
                cprint('{dim}     ' + body[:300].replace('\n', ' ') + '{x}')


if __name__ == '__main__':
    sys.exit(main() or 0)
