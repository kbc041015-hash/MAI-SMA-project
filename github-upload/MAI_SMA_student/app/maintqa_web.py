# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/maintqa_web.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · LLM: GGUF·llama-server 직접 기동 대신 config.json 의 실행기(llm_client). 상태 확인은 GET {base_url}/models
#   · start_server: 응답하면 그대로 쓰고, config 의 llama_server(exe·model_path)가 있을 때만 직접 띄운다(선택)
#   · ask_llm: 요청 본문은 preset 별(llamacpp · ollama · lmstudio · openai). 중단(소켓 닫기)은 원판 그대로
#   · 설정 창: GGUF 가 있으면 헤더 제원, 없으면 설정값 + 시스템 메모리만(gguf_spec · mem_now · runtime_info)
#   · DB: 학생용 DB(data/MAI_SMA_학생용DB_1000.duckdb), 연결은 harness.get_con(외부 접근 차단), 요청마다 커서(db) — 커서를 잡아 둔 채 execute 로 읽는다(duckdb 판과 무관하게), SQL 시간 제한(run_sql)
#   · 출처 조회(provenance): 참고 기록(색인 meta[0] = QA_REF)은 qa_proc_context 에서 제목·기기·장비·호기·날짜와 문항별로 묶은 문항ID 를 채운다(ref_provenance). 정비 문서 기록은 원판과 같은 값
#   · 화면 모델 이름: model 을 비운 설정(llama.cpp 등)은 실행기 /v1/models 의 id 파일 이름(server_model_name)
#   · 번호표: --pid-file(기본 logs/web.pid). 자체 점검의 임시 서버는 임시 폴더에 써서 실제 서버의 번호표를 건드리지 않는다
#   · 질의 처리(라우터 · 집계형 · 기권 · 절차형 · 인용 검증 · 병기 · 후속 질문)와 모든 프롬프트는 바꾸지 않았다
#   · /api/info 에 app · preset · base_url · remote · db · docs_ref 를 더했다(START.bat 이 이 프로그램인지 확인한다)
"""정비이력 질의 도구 — 웹 화면판 (학생 이식판).

maintqa_cli.py 와 같은 엔진을 쓰고 화면만 브라우저로 낸다. 서버는 이 PC 안에서만
열리며(127.0.0.1) 외부 통신이 없다. 브라우저를 쓰지만 인터넷은 쓰지 않는다.

학생 이식판: LLM 은 config.json 에 적은 실행기(Ollama · LM Studio · llama.cpp · 그 밖의
OpenAI 호환 서버)를 쓰고, 데이터는 학생용 DB(정비 문서 1,000건 + 질의응답 200문항)만 쓴다.

  python app/maintqa_web.py                  config.json 으로 기동, 브라우저 자동 열림
  python app/maintqa_web.py --port 8800      화면 포트 지정(기본: config.json 의 web_port)
  python app/maintqa_web.py --config 다른설정.json
"""
import os, sys, re, json, time, argparse, subprocess, threading, webbrowser
import http.client
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.dont_write_bytecode = True      # 학생 이식판: __pycache__ 를 남기지 않는다
from answer_text import compose

HERE = os.path.dirname(os.path.abspath(__file__)).replace('\\', '/')
sys.path.insert(0, HERE)
import sma_config as CONF     # 학생 이식판: 설정 파일
import llm_client as LLM       # 학생 이식판: OpenAI 호환 호출·상태 확인·프리셋
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# 학생 이식판: 모델은 config.json 이 정한다. 원 시연판의 GGUF 목록(MODELS)과
# 권장 모델 표(RECOMMENDED)는 쓰지 않는다. 연구에서 배포 기준(원출력 재현 · 기권
# 지시이행 · 인용 충실도)을 통과한 것은 12B · 14B 급 두 모델이었다.


def quant_of(fname):
    """GGUF 파일명에서 양자화 표기를 읽는다.

    표기를 손으로 적어 두면 모델 파일을 바꿨을 때 어긋난다. 실제로 적재되는
    파일 이름에서 뽑아야 화면의 표기와 돌아가는 가중치가 항상 일치한다.
    """
    m = re.search(r'[-_.](Q\d+_[A-Z0-9_]+|F16|BF16|F32)(?![A-Za-z0-9])',
                  fname, re.I)
    return m.group(1).upper() if m else ''


def quant_text(q):
    """GGUF 양자화 표기를 사람이 읽는 영문으로 바꾼다.

    Q4_K_M 같은 표기는 이 분야의 내부 약속이라 그대로 두면 읽히지 않는다.
    화면에는 뜻이 보이는 말로 적고, 정확한 표기는 /api/info 에 남긴다.
    """
    if not q:
        return ''
    u = q.upper()
    if u.startswith('Q') and u[1:2].isdigit():
        bits = re.match(r'Q(\d+)', u).group(1)
        return f'{bits}-bit quantized'
    return {'F16': '16-bit float', 'BF16': 'bfloat16',
            'F32': '32-bit float'}.get(u, u)


def model_label(tag=None):
    """화면에 쓰는 모델 표기. 설정의 모델 이름 + 양자화(이름에서 읽힐 때만).
    학생 이식판: model 을 비운 llama.cpp 등은 실행기의 /v1/models 가 알려 준 이름(파일 이름만)을 쓴다(server_model)."""
    conf = CFG.get('conf') or {}
    path = conf.get('gguf_path') or (conf.get('llama_server') or {}).get('model_path') or ''
    nm = (conf.get('model') or (os.path.basename(path) if path else '') or CFG.get('server_model')
          or 'Server default model')
    nm = re.sub(r'\.gguf$', '', nm, flags=re.I)
    q = quant_of(nm) or quant_of(os.path.basename(path))
    return f'{nm} ({q})' if q and q.upper() not in nm.upper() else nm


def server_model_name(ids):
    """실행기 모델 목록의 첫 id → 화면에 쓸 이름(학생 이식판). llama-server 는 GGUF 의 전체 경로를 id 로 주므로
    파일 이름만 남긴다(화면·기록에 이 PC 의 폴더 경로가 남지 않게)."""
    if not ids:
        return ''
    v = re.split(r'[\\/]', str(ids[0]).strip().rstrip('\\/'))[-1]
    return re.sub(r'\.gguf$', '', v, flags=re.I)


CFG = {}


# ─────────────────────── 창 없이 돌리기 ───────────────────────


class Tee:
    """화면과 파일에 함께 적는다.

    시연 중에는 콘솔 창을 숨긴다. 그러면 여기 찍히던 글이 어디에도 남지
    않아, 모델이 안 뜨거나 포트가 막혔을 때 볼 것이 없어진다. 숨기는 것과
    잃는 것은 다르다.
    """

    def __init__(self, stream, path):
        self.s = stream
        self.f = open(path, 'a', encoding='utf-8', buffering=1)
        self.f.write('\n' + '=' * 54 + '\n'
                     + time.strftime('  %Y-%m-%d %H:%M:%S 기동\n'))

    def write(self, t):
        if self.s is not None:
            try:
                self.s.write(t)
            except Exception:
                pass
        try:
            self.f.write(t)
        except Exception:
            pass
        return len(t)

    def flush(self):
        for x in (self.s, self.f):
            try:
                x.flush()
            except Exception:
                pass


def hide_console():
    """이 프로세스가 가진 콘솔 창을 감춘다.

    --hide 로 명시했을 때만 부른다. 시연 화면 옆에 검은 창이 남지 않게
    하려는 것이고, 창을 없애는 것이 아니라 감추는 것이라 프로세스는 그대로
    산다. 끝낼 때는 STOP.bat 을 쓴다.
    """
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetConsoleWindow()
        if h:
            ctypes.windll.user32.ShowWindow(h, 0)      # SW_HIDE
    except Exception:
        pass


# ───────────────────────── 추론 서버 ─────────────────────────
SRV = {'p': None}


def start_server():
    """LLM 서버 준비. 학생 이식판에서 바꿨다.

    원 시연판은 GGUF 파일로 llama-server 를 직접 띄웠다. 이식판은 학생이 이미 켜 둔 실행기에
    붙는 것이 기본이다. 주소가 응답하면 그대로 쓰고(중복 기동 없음), 응답하지 않을 때는
    config.json 의 llama_server 에 실행 파일과 모델 경로가 있을 때만 직접 띄운다(선택 기능).
    """
    conf = CFG['conf']
    if health():
        return True, f'LLM 서버 응답 확인 — {CONF.summary(conf)}'
    if not LLM.embedded_ready(conf):
        return False, (f'[!] LLM 서버가 응답하지 않습니다: {conf["base_url"]}\n'
                       f'      {LLM.start_hint(conf["preset"])}')
    try:
        SRV['p'] = LLM.launch_llama_server(conf, os.path.join(CONF.LOGS, 'llama_server.log'),
                                           os.path.join(CONF.LOGS, 'llama_server.pid'))
    except Exception as e:
        return False, f'[!] llama-server 를 띄우지 못했습니다: {e}'
    for i in range(200):
        time.sleep(3)
        if health():
            return True, f'llama-server 준비 완료 ({i*3}초) — {CONF.summary(conf)}'
        if SRV['p'].poll() is not None:
            return False, '[!] 추론 서버가 조기 종료했습니다. logs/llama_server.log 를 보십시오.'
    return False, '[!] 추론 서버 기동 시간초과'


def stop_embedded():
    """이 프로그램이 직접 띄운 llama-server 만 내린다(학생 이식판)."""
    if SRV['p'] and SRV['p'].poll() is None:
        SRV['p'].kill()
    try:
        os.remove(os.path.join(CONF.LOGS, 'llama_server.pid'))
    except OSError:
        pass


def health(port=None):
    """GET {base_url}/models 가 응답하면 준비된 것으로 본다(학생 이식판: 실행기 공통 확인)."""
    try:
        return CFG['llm'].health()
    except Exception:
        return False


# ── 중단 ─────────────────────────────────────────────
# 화면에서 기다리기만 그만두면 추론 서버는 계속 계산한다. 슬롯이 하나라
# 다음 질문이 그만큼 막힌다. 실제로 끊으려면 추론 서버로 가는 연결을
# 닫아야 한다. 연결이 끊기면 llama.cpp 가 그 작업을 취소한다.
LIVE = {}               # rid -> 지금 열려 있는 추론 서버 연결
STOPPED = set()         # 중단된 rid
STOP_LOCK = threading.Lock()


class Stopped(Exception):
    """사용자가 중단했다."""


def stop_request(rid):
    """다른 스레드에서 불린다. 연결을 닫아 추론을 실제로 끊는다."""
    if not rid:
        return False
    with STOP_LOCK:
        STOPPED.add(rid)
        if len(STOPPED) > 200:
            STOPPED.clear()
            STOPPED.add(rid)
        c = LIVE.get(rid)
    if c is not None:
        # close() 만으로는 getresponse() 에 막혀 있는 스레드가 깨어나지 않는다.
        # 소켓을 shutdown 해야 그 recv 가 곧바로 풀리고, 추론 서버도 끊긴 것을
        # 알아채 작업을 버린다. close() 만 했을 때는 생성이 끝까지 돌았다.
        try:
            if getattr(c, 'sock', None) is not None:
                c.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass
        return True
    return False


def check_stop(rid):
    if rid:
        with STOP_LOCK:
            if rid in STOPPED:
                raise Stopped()


def ask_llm(prompt, mx=1200, retries=1, rid=None, allow_empty=False):
    """LLM 호출. 학생 이식판: 요청 본문은 llm_client 가 config.json 의 preset 에 맞춰 만든다.

    중단 처리는 원 시연판과 같다. 진행 중인 연결을 LIVE 에 걸어 두고, 중단하면
    stop_request() 가 그 소켓을 닫아 실행기도 생성을 멈추게 한다.
    """
    def hook(conn):
        if rid:
            with STOP_LOCK:
                LIVE[rid] = conn
    check_stop(rid)
    try:
        r = CFG['llm'].chat(prompt, mx, retries=retries, conn_hook=hook,
                            stop_check=lambda: check_stop(rid), allow_empty=allow_empty)
    finally:
        if rid:
            with STOP_LOCK:
                LIVE.pop(rid, None)
    if r.get('prompt_tokens') is not None:
        CFG['last_usage'] = dict(prompt_tokens=r.get('prompt_tokens'),
                                 completion_tokens=r.get('completion_tokens'), sec=r.get('sec'))
    if r['error']:
        return f"__ERROR__ {r['error']}", 0.0
    return r['content'], r['sec']


# ───────────────────── 설정 화면용 정보 ─────────────────────


def gguf_spec():
    """설정 창의 모델 칸. 학생 이식판에서 바꿨다.

    config.json 에 GGUF 파일(gguf_path 또는 llama_server.model_path)이 있으면 그 헤더에서 제원을
    읽는다(원 시연판과 같다). 없으면(Ollama · LM Studio 등) 설정값만 보인다.
    """
    if CFG.get('spec'):
        return CFG['spec']
    conf = CFG['conf']
    path = conf.get('gguf_path') or (conf.get('llama_server') or {}).get('model_path') or ''
    name = (conf.get('model') or (os.path.basename(path) if path else '') or CFG.get('server_model')
            or '(server default)')
    q = quant_of(name) or quant_of(os.path.basename(path))
    out = dict(name=name, file=os.path.basename(path) if path else '', quant=q, quant_text=quant_text(q),
               source='config', preset=CONF.PRESET_LABEL[conf['preset']], base_url=conf['base_url'])
    if path and os.path.exists(path):
        try:
            import model_specs as MSPEC
            s = MSPEC.spec_path(path) or {}
            out.update({k: v for k, v in s.items() if v is not None and k != 'name'})
            out['source'] = 'gguf'
            out['file_gb'] = round(os.path.getsize(path) / 1024 ** 3, 2)
            out['quant'] = quant_of(os.path.basename(path)) or out.get('quant', '')
            out['quant_text'] = quant_text(out['quant'])
        except Exception as e:
            out['note'] = f'Could not read the GGUF header: {type(e).__name__}'
    elif path:
        out['note'] = f'The GGUF file in config.json was not found: {os.path.basename(path)}'
    else:
        out['note'] = 'No GGUF file is configured (config.json gguf_path), so only the configured values are shown.'
    CFG['spec'] = out
    return out


def mem_now():
    """메모리 사용량. 학생 이식판에서 바꿨다.

    GGUF 를 설정했거나 이 프로그램이 llama-server 를 띄웠을 때만 추론 서버 프로세스의 점유량을
    읽는다(원 시연판과 같은 방식). 그 밖의 실행기(Ollama · LM Studio 등)는 프로세스 구성이
    제각각이라 시스템 메모리만 보인다. 값은 1.5초 동안 그대로 쓴다.
    """
    now = time.time()
    c = CFG.get('mem_cache')
    if c and now - c[0] < 1.5:
        return c[1]
    out = dict(ok=False)
    try:
        import psutil
    except ImportError:
        out['note'] = 'psutil is not installed, so memory use cannot be read (pip install psutil).'
        CFG['mem_cache'] = (now, out)
        return out
    try:
        vm = psutil.virtual_memory()
        out.update(sys_total_gb=round(vm.total / 1024 ** 3, 1),
                   sys_used_gb=round((vm.total - vm.available) / 1024 ** 3, 1),
                   sys_pct=vm.percent)
        spec = CFG.get('spec') or {}
        if not (SRV['p'] or spec.get('source') == 'gguf'):
            out['note'] = ('System memory only. No GGUF file is configured, so the model process is not '
                           'identified — Ollama, LM Studio and other runners organise their processes differently.')
            CFG['mem_cache'] = (now, out)
            return out
        me = 0.0
        kept = []
        for pr in CFG.get('mem_procs') or []:
            try:
                me += pr.memory_info().rss
                kept.append(pr)
            except Exception:
                kept = []
                me = 0.0
                break
        if not kept:
            for pr in psutil.process_iter(['name']):
                if (pr.info.get('name') or '').lower().startswith('llama-server'):
                    try:
                        me += pr.memory_info().rss
                        kept.append(pr)
                    except Exception:
                        pass
            CFG['mem_procs'] = kept
        if kept:
            out.update(ok=True, model_gb=round(me / 1024 ** 3, 2), procs=len(kept))
        else:
            out['note'] = 'No llama-server process is running on this PC. System memory only.'
    except Exception as e:
        out['note'] = f'{type(e).__name__}: {e}'
    CFG['mem_cache'] = (now, out)
    return out


def runtime_info():
    """설정 창의 '실행 환경' 칸(학생 이식판: config.json 의 값과 실제로 보내는 요청 필드)."""
    conf = CFG['conf']
    emb = bool(SRV['p'])
    ls = conf.get('llama_server') or {}
    args = [str(x) for x in (ls.get('extra_args') or [])]
    cache = None
    if emb and '--cache-reuse' in args and args.index('--cache-reuse') + 1 < len(args):
        cache = args[args.index('--cache-reuse') + 1]
    return dict(ctx=conf.get('context_tokens'),
                ngl=(ls.get('ngl') if emb else None), slots=(1 if emb else None), cache_reuse=cache,
                port=CONF.port_of(conf['base_url']), cond='F', demo=True,
                docs=CFG['idx']['N'] if CFG.get('idx') else 0,
                docs_ref=index_refs(), db=os.path.basename(conf['db']),
                preset=CONF.PRESET_LABEL[conf['preset']], base_url=conf['base_url'],
                model=conf.get('model') or '', request=CFG['llm'].request_summary(),
                embedded=emb, last=CFG.get('last_usage'), app=CONF.APP_ID)


def index_refs():
    """색인 속 참고 기록(qa_proc_context) 수. 학생 이식판에서 더했다. 나머지는 정비 문서 기록(master)이다."""
    idx = CFG.get('idx')
    if not idx:
        return 0
    if 'n_ref' not in CFG:
        CFG['n_ref'] = sum(1 for d in idx['docs'] if d['meta'][0] == REF)
    return CFG['n_ref']


def dict_overview():
    """데이터 사전의 개요. 파일에서 세어 내므로 사전을 고치면 따라 바뀐다."""
    if CFG.get('dictov'):
        return CFG['dictov']
    out = dict(ok=False)
    try:
        from sel_dict import load_entries
        import build_prompt100 as BP
        ent = load_entries()
        per = {}
        exprs = []
        for t, c, d, syn in ent:
            per[t] = per.get(t, 0) + 1
            exprs += [x.strip() for x in (syn or '').split(',') if x.strip()]
        annotated = (sum(1 for ln in (BP.SC or '').split('\n') if '  -- ' in ln)
                     if BP.SC else 0)
        out = dict(ok=True, n=len(ent),
                   tables=sorted(per.items(), key=lambda x: -x[1]),
                   with_syn=sum(1 for e in ent if e[3]),
                   exprs=len(exprs), uniq=len(set(exprs)),
                   annotated=annotated, chars=len(BP.SC or ''))
        import term_map
        out['terms'] = len(term_map._ALL)
        out['term_names'] = len(set(term_map.vocabulary()))
    except Exception as e:
        out['note'] = f'{type(e).__name__}: {e}'
    CFG['dictov'] = out
    return out


def dict_entries():
    """사전 원문. 세어 놓은 수 말고 내용을 그대로 넘긴다.

    '121 항목' 만 보이면 그 안에 무엇이 들어 있는지 확인할 길이 없다.
    이 도구가 말과 컬럼을 어떻게 잇는지가 곧 이 연구의 내용이므로,
    화면에서 열어 볼 수 있어야 한다. 읽기만 하고 고치지는 못한다.
    """
    if CFG.get('dictent'):
        return CFG['dictent']
    out = dict(ok=False)
    try:
        from sel_dict import load_entries
        import term_map
        out = dict(ok=True,
                   cols=[dict(t=t, c=c, d=d, s=syn)
                         for t, c, d, syn in load_entries()],
                   terms=[dict(a=list(ko), en=en) for ko, en in term_map._ALL])
    except Exception as e:
        out['note'] = f'{type(e).__name__}: {e}'
    CFG['dictent'] = out
    return out


# ───────────────────── 진행 상황 ─────────────────────
# 한 질의가 1분을 넘기기도 한다. 지금 어느 단계이고 앞 단계가 몇 초
# 걸렸는지 보이지 않으면 멈춘 것으로 읽힌다. 요청마다 식별자를 받아
# 단계를 기록해 두고, 화면이 짧은 주기로 읽어 가게 한다.
PROG = {}
PROG_LOCK = threading.Lock()
PROG_MAX = 40          # 오래된 기록은 버린다. 화면 하나가 쓰는 것이 몇 개뿐이다.


class Prog:
    """한 요청의 단계 기록. rid 가 없으면 아무 일도 하지 않는다."""

    def __init__(self, rid=None):
        # 단계 기록은 rid 와 무관하게 남긴다. rid 는 화면이 진행 상황을
        # 읽어 가기 위한 열쇠일 뿐이고, 답변에 싣는 시간 내역은 항상 필요하다.
        self.rid = rid or None
        self.done = []
        self.now = None
        self.t = None
        if self.rid:
            with PROG_LOCK:
                if len(PROG) >= PROG_MAX:
                    for k in list(PROG)[:len(PROG) - PROG_MAX + 1]:
                        PROG.pop(k, None)
                PROG[self.rid] = dict(done=[], now=None, since=time.time())

    def start(self, label):
        """앞 단계를 닫고 새 단계를 연다."""
        now = time.time()
        if self.now is not None:
            self.done.append(dict(label=self.now, sec=round(now - self.t, 1)))
        self.now, self.t = label, now
        self._flush(now)

    def finish(self):
        """마지막 단계를 닫는다."""
        now = time.time()
        if self.now is not None:
            self.done.append(dict(label=self.now, sec=round(now - self.t, 1)))
            self.now = None
        self._flush(now)

    def _flush(self, now):
        if not self.rid:
            return
        with PROG_LOCK:
            PROG[self.rid] = dict(done=list(self.done), now=self.now, since=now)

    def drop(self):
        if self.rid:
            with PROG_LOCK:
                PROG.pop(self.rid, None)


# ───────────────────────── 질의 처리 ─────────────────────────
ABSTAIN = {
    'NOT_RECORDED': ('Not recorded in this system.',
                     'Parts consumption is recorded for only 3.21% of all maintenance '
                     'documents. What was used, and how much, cannot be answered from '
                     'this data.'),
    'NO_RELIABLE_VALUE': ('Recorded, but not reliable.',
                          'Work hours and manpower appear in two places and neither is '
                          'usable. In pms, 99% of the values are 0; in master, 73% sit at '
                          '1.0, giving a correlation with work size of only 0.16.'),
}


def clean_sql(s):
    from harness import is_abstain
    s = re.sub(r'^\s*```(?:sql)?\s*', '', s.strip(), flags=re.I)
    s = re.sub(r'```\s*$', '', s.strip()).strip()
    if is_abstain(s):
        return s.strip().strip('`').strip().rstrip(';').strip().upper()
    m = re.search(r'(SELECT|WITH)\b', s, re.I)
    if m:
        s = s[m.start():]
    return (s[:s.index(';') + 1] if ';' in s else s).strip()


TABLES = ['master', 'pms', 'approval', 'consumption', 'attachments', 'vessels']


def sql_source(sql):
    """생성된 SQL 이 실제로 읽은 테이블과 컬럼을 뽑는다.

    집계형 답의 출처는 문서가 아니라 데이터베이스다. 어느 테이블의 어느 컬럼을
    보고 나온 수치인지 밝혀야 절차형과 같은 수준의 근거 표시가 된다.
    스키마 카탈로그와 대조해 실재하는 컬럼만 남긴다.
    """
    up = sql.upper()
    out = []
    for t in TABLES:
        if not re.search(rf'(?<![A-Za-z0-9_]){t.upper()}(?![A-Za-z0-9_])', up):
            continue
        try:
            cols = [r[0] for r in db().execute(f'DESCRIBE {t}').fetchall()]
        except Exception:
            continue
        hit = [c for c in cols
               if re.search(rf'(?<![A-Za-z0-9_]){re.escape(c.upper())}(?![A-Za-z0-9_])', up)]
        if hit:
            out.append(dict(table=t, cols=hit[:8]))
    return out


def ask_lang(q):
    """질문이 어느 말로 쓰였는가. 한글이 한 자라도 있으면 한국어로 본다.

    화면에는 언어 선택이 없다. 답은 늘 두 언어로 내되, 어느 쪽을 주로
    둘지와 번역 단계를 거칠지는 질문 자체를 보고 정한다.
    """
    return 'ko' if re.search(r'[가-힣]', q or '') else 'en'


def col_names(sql):
    """결과 열 이름을 읽는다.

    DESCRIBE 는 질의를 실제로 돌리지 않고 결과 스키마만 돌려주므로, 무거운
    질의를 두 번 실행하지 않는다. 화면의 표 머리행과 답변 문장에 쓴다.
    """
    try:
        body = sql.strip().rstrip(';')
        return [str(r[0]) for r in db().execute(f'DESCRIBE {body}').fetchall()]
    except Exception:
        return []


def agg_schema():
    """조건 F 의 스키마 — 전체 컬럼 설명을 주석으로 붙인 DDL.

    질문과 무관하게 고정이므로 모듈 적재 때 한 번 만들어 두고 계속 쓴다.
    고정이어야 추론 서버의 접두부 캐시가 듣는다. 질문마다 붙는 설명이
    달라지는 조건 S 는 같은 앞부분을 매번 다시 계산하게 만들어, 토큰이
    절반인데도 응답이 7배 느렸다(중앙값 76.2초 대 10.6초).
    """
    import build_prompt100 as BP
    if CFG.get('sc') is None:
        sc = BP.SC
        if sc is None:                      # 사전 파일이 없으면 조건 C 로 내려간다
            sc, ncol = BP.SCHEMA, 0
        else:
            ncol = sum(1 for ln in sc.split('\n') if '  -- ' in ln)
        CFG['sc'], CFG['ncol'] = sc, ncol
    return CFG['sc'], CFG['ncol']


def warm_prefix():
    """조건 F 의 고정 접두부를 추론 서버에 미리 올린다.

    기동 직후에는 재사용할 계산이 없어 첫 질문이 프롬프트 전체를 처리한다.
    실측 193초였다. 여기서 같은 접두부로 한 번 물어 두면 그 비용을 미리
    치르고, 이후 질문은 바뀐 끝부분만 계산한다.
    """
    import build_prompt100 as BP
    try:
        sc, _ = agg_schema()
        t = time.time()
        # 생성은 한 토큰이면 된다. 목적은 앞부분 계산을 남겨 두는 것이다.
        ask_llm(BP.HEAD + sc + '\n' + BP.RULES + BP.FEWSHOT
                + BP.TAIL.replace('{q}', '선박은 몇 척인가?'), mx=1, retries=0, allow_empty=True)
        CFG['warm'] = round(time.time() - t, 1)
        print(f"  접두부 예열 완료 ({CFG['warm']}초) — 이후 질의가 빨라집니다.")
    except Exception as e:
        CFG['warm'] = None
        print(f'  접두부 예열 건너뜀: {type(e).__name__}')


def warm_settings():
    """설정 창이 쓰는 값을 기동할 때 미리 읽어 둔다.

    GGUF 헤더 읽기와 사전 세기는 한 번이면 되는 일인데, 미리 해 두지 않으면
    처음 톱니바퀴를 누른 사람이 그 비용을 다 치르고 'Reading…' 을 본다.
    """
    for fn in (gguf_spec, dict_overview, dict_entries, mem_now):
        try:
            fn()
        except Exception:
            pass


def db():
    """요청마다 새 커서(학생 이식판). ThreadingHTTPServer 는 요청마다 스레드가 다르고,
    DuckDB 연결 하나를 여러 스레드가 함께 쓰는 것은 안전하지 않다."""
    return CFG['con'].cursor()


def run_sql(sql):
    """harness.run 에 시간 제한을 붙인다(학생 이식판). 넘기면 끊고 오류로 돌려준다.
    eval/run_eval.py 의 SqlRunner 와 같은 방식이다. 모델이 쓴 SQL 이 끝없이 돌지 않게 한다."""
    from harness import run
    cur = db()
    lim = CFG['conf'].get('sql_timeout_sec') or 0
    fired = []

    def _stop():
        fired.append(1)
        try:
            cur.interrupt()
        except Exception:
            pass
    t = threading.Timer(lim, _stop) if lim > 0 else None
    if t:
        t.daemon = True
        t.start()
    try:
        res, err = run(cur, sql)
    finally:
        if t:
            t.cancel()
    if fired and res is None:
        err = f'SQL 실행 시간 초과({lim}초)'
    return res, err


def do_agg(q, pr=None, bi=False):
    from harness import run, repair_dialect
    import build_prompt100 as BP
    pr = pr or Prog(None)
    sc, ncol = agg_schema()
    pr.start('Generating SQL')
    raw, dt = ask_llm(BP.HEAD + sc + '\n' + BP.RULES + BP.FEWSHOT
                      + BP.TAIL.replace('{q}', q), mx=600, rid=pr.rid)
    if raw.startswith('__ERROR__'):
        return dict(kind='error', msg='Model call failed: ' + raw[:160])
    sql = clean_sql(raw)
    if sql in ABSTAIN:
        h, w = ABSTAIN[sql]
        return dict(kind='abstain', token=sql, head=h, why=w, sec=round(dt, 1),
                    t_gen=round(dt, 1))
    pr.start('Running SQL')
    _t1 = time.time()
    pred, err = run_sql(sql)
    if pred is None:
        s2 = repair_dialect(sql)
        if s2 != sql:
            pred, err = run_sql(s2)
            if pred is not None:
                sql = s2
    t_exec = time.time() - _t1
    if pred is None:
        return dict(kind='sqlerr', sql=sql, msg=str(err)[:300], sec=round(dt, 1))
    rows = [[('' if v is None else str(v)) for v in r] for r in pred[:50]]
    hdr = col_names(sql)
    return dict(kind='table', sql=sql, cols=ncol, sec=round(dt, 1),
                answer=compose(q, sql, hdr, rows, len(pred), 'en'),
                answer_ko=(compose(q, sql, hdr, rows, len(pred), 'ko')
                           if bi else None),
                hdr=hdr,
                src=sql_source(sql),
                t_gen=round(dt, 1),
                t_exec=round(t_exec, 2),
                rows=rows, total=len(pred))


# 직책의 선상 약칭. 시연 DB 에 실재하는 값만 넣는다.
# 화면 문구를 영어로 통일했으므로 직책도 배에서 쓰는 표기로 낸다.
RANK = {
    '기관장': 'C/E',              # Chief Engineer
    '1등기관사': '1/E',
    '2등기관사': '2/E',
    '3등기관사': '3/E',
    '4등기관사': '4/E',
    # C/E 는 기관장(Chief Engineer) 의 약칭이므로 화물기관사에는 쓰지 않는다.
    '화물기관사': 'CGO/E',        # Cargo Engineer
    '선장': 'Master',
    '수석항해사': 'C/O',           # Chief Officer
    '1등항해사': 'C/O',
    '2등항해사': '2/O',
    '3등항해사': '3/O',
    '통신항해사': 'R/O',           # Radio Officer
}


def rank_abbr(v):
    """직책을 약칭으로. 모르는 값은 원문을 그대로 둔다."""
    s = str(v or '').strip()
    return RANK.get(s, s)


REF = 'QA_REF'     # 학생 이식판: 색인에서 참고 기록(qa_proc_context)을 가리키는 표지(rag.REF 와 같다)


def _split_unit(v):
    """'MAIN ENGINE(DIESEL) 2' → ('MAIN ENGINE(DIESEL)', 2). 끝에 호기 번호가 없으면 (원문, None)."""
    s = str(v or '').strip()
    if s in ('', 'None'):
        return '', None
    m = re.match(r'^(.*?)\s+(\d+)$', s)
    return (m.group(1), int(m.group(2))) if m else (s, None)


def _unit_text(nums):
    """호기 번호 목록 → 화면 글(학생 이식판): [1] → '1', [1, 2, 3, 4] → '1–4', [1, 3] → '1, 3'. 번호가 없으면 None."""
    ns = sorted({n for n in nums if n is not None})
    if not ns:
        return None
    if len(ns) == 1:
        return str(ns[0])
    if ns == list(range(ns[0], ns[-1] + 1)):
        return f'{ns[0]}–{ns[-1]}'
    return ', '.join(str(n) for n in ns)


def _named_units(values):
    """'MOORING WINCH 1' · 'MOORING WINCH 2' … → [{'name': 'MOORING WINCH', 'units': '1–2'}] (처음 나온 순서)."""
    order, units = [], {}
    for v in values:
        name, no = _split_unit(v)
        if not name:
            continue
        if name not in units:
            order.append(name)
            units[name] = []
        units[name].append(no)
    return [dict(name=n, units=_unit_text(units[n])) for n in order]


def ref_provenance(i, meta):
    """참고 기록의 출처(학생 이식판에서 더했다).

    참고 기록은 master 의 행이 아니라 절차형 100문항의 고정 검색 문맥(qa_proc_context)이다. 실제 정비기록
    원문을 가명처리한 것이라 선박·담당 직책·정비 항목은 남아 있지 않다. 본문이 같은 기록을 모두 찾아
    제목·기기·장비·작업일과 그 기록을 문맥으로 쓰는 문항ID(문항-기록번호)를 채운다.
    같은 본문이 여러 행이면(예: 같은 점검을 적은 윈치 1~4호기) 기기·장비의 호기를 모두 모으고(machs · equips),
    문항ID 는 문항별로 묶는다(groups: [{'qid': 'P063', 'recs': [1, 2, 3, 4]}]). mach_no · equip_no · date · qids 는
    첫 행 기준 값으로 그대로 둔다.
    """
    cur = db()
    rows = cur.execute(
        "SELECT qid, rec_no, title, machine, equipment, work_date FROM qa_proc_context "
        "WHERE content = ? ORDER BY qid, rec_no", [meta[6]]).fetchall()
    first = rows[0] if rows else (None, None, meta[2], meta[3], meta[4], meta[5])
    mach, mach_no = _split_unit(first[3])
    equip, equip_no = _split_unit(first[4])
    groups = []
    for q, n, *_ in rows:
        if groups and groups[-1]['qid'] == q:
            groups[-1]['recs'].append(n)
        else:
            groups.append(dict(qid=q, recs=[n]))
    for g in groups:
        g['recs_text'] = _unit_text(g['recs'])
    dates = sorted({str(r[5])[:10] for r in rows if r[5]})
    return dict(rec=i, ref=True, src='qa_proc_context', vessel='', vessel_name='',
                doc=meta[1], title=first[2] or meta[2], mach=mach, mach_no=mach_no, mach_code='',
                equip=equip, equip_no=equip_no, equip_code='', date=str(first[5] or '')[:10],
                machs=_named_units([r[3] for r in rows] or [first[3]]),
                equips=_named_units([r[4] for r in rows] or [first[4]]),
                dates=dates, groups=groups, qids=[f'{q}-{n}' for q, n, *_ in rows], items=[])


def provenance(hits):
    """검색된 기록의 출처 계층을 DB 에서 채운다.

    BM25 색인에는 선박·문서·제목·기기명·장비명까지만 들어 있다. 호기 번호와
    코드, 정비 항목은 master/pms 에서 (선박, 문서번호)로 다시 읽는다.
    화면에 "어느 배 · 어느 기기 · 몇 호기 · 어느 장비"까지 보이려면 필요하다.
    학생 이식판: 참고 기록(meta[0] == QA_REF)은 qa_proc_context 에서 채운다(ref_provenance).
    정비 문서 기록은 원판과 같은 값을 읽는다(커서를 변수로 잡아 두고 같은 커서로 읽는다 — duckdb 판에 따라
    임시 커서 위의 relation 은 커서가 먼저 닫혀 읽지 못한다).
    """
    out = []
    for i, h in enumerate(hits, 1):
        vc, dn = h[1]['meta'][0], h[1]['meta'][1]
        if vc == REF:
            out.append(ref_provenance(i, h[1]['meta']))
            continue
        cur = db()
        row = cur.execute(
            "SELECT VESSELCODE, VESSEL_NAME, DOC_NO, MACH_CODE, MACH_NO, MACH_DESC, "
            "EQUIP_CODE, EQUIP_NO, EQUIP_DESC, WORK_DATE, DEPT, PIC, WORK_REASON, TITLE "
            "FROM master WHERE VESSELCODE = ? AND DOC_NO = ? LIMIT 1", [vc, dn]).fetchone()
        items = cur.execute(
            "SELECT PMS_CODE, PMS_DESC, PMS_TYPE, PMS_INTERVAL, CRITICAL_YN "
            "FROM pms WHERE VESSELCODE = ? AND DOC_NO = ? LIMIT 6", [vc, dn]).fetchall()
        if row is None:
            out.append(dict(rec=i, vessel=vc, doc=dn, title=h[1]['meta'][2]))
            continue
        # 기기명·장비명 끝에 호기 번호가 붙어 있다(master 의 특성). 떼어 낸다.
        mach = re.sub(r'\s+\d+$', '', str(row[5] or ''))
        equip = re.sub(r'\s+\d+$', '', str(row[8] or ''))
        out.append(dict(
            rec=i, vessel=row[0], vessel_name=row[1], doc=row[2],
            mach_code=row[3], mach_no=row[4], mach=mach,
            equip_code=row[6], equip_no=row[7], equip=equip,
            date=(str(row[9])[:10] if row[9] else ''), dept=row[10], pic=rank_abbr(row[11]),
            reason=row[12], title=row[13],
            items=[dict(code=x[0], desc=x[1], type=x[2], interval=x[3], critical=x[4])
                   for x in items]))
    return out


TRANS = ("Translate the following Korean question about ship maintenance into "
         "English. Output only the translation.\n\n{q}")

# 용어 대응을 실은 번역 프롬프트.
# 코퍼스가 쓰는 설비 표기를 알려 주지 않으면 모델이 제 나름의 영어를 쓴다.
# "발전기"를 "power generator" 로 옮기면 BM25 가 GENERATOR ENGINE(DIESEL) 을
# 못 찾는다. 검색이 어긋나면 그 뒤 생성과 인용 검증이 통째로 의미를 잃는다.
TRANS_D = ("Translate the following Korean question about ship maintenance into "
           "English. Output only the translation.\n"
           "{hints}\n{q}")


# 병기 모드에서 질문의 반대쪽 언어를 만들 때 쓴다. 화면 표시용이며
# 검색이나 생성에는 들어가지 않는다 — 그쪽은 늘 영어로 돈다.
TRANS_KO = ("다음 선박 정비 질문을 자연스러운 한국어로 옮겨라. "
            "번역문 한 줄만 출력해라.\n\n{q}")


def one_line(v):
    """번역 결과에서 한 줄만 추린다."""
    if not v or v.startswith('__ERROR__'):
        return ''
    return v.strip().strip('"').strip("'").split('\n')[0].strip()


def naming_note(pairs):
    """이 코퍼스가 같은 기계를 어떤 이름으로 적는지 알려 주는 한 문단.

    모델이 기록의 이름과 질문의 이름이 다른 것을 실제 공백으로 읽는 일이
    있었다. 값을 인용해 놓고도 "그 기기에 대해서는 기록이 없다"고 적는다.
    사실을 알려 줄 뿐이며, 없는 값을 지어내라는 지시가 아니다.
    """
    if not pairs:
        return ''
    lines = '\n'.join(f'- "{ko}" is written as "{en}" in these records.'
                       for ko, en in pairs)
    return ('\n[EQUIPMENT NAMING]\n' + lines +
            '\nThese refer to the same equipment. Do not treat the different '
            'wording as a gap in the records.\n')


def snap(q_en, extra):
    """번역문에 코퍼스 표기를 덧붙여 검색어를 넓힌다.

    번역이 다른 말을 골랐더라도 대응표가 아는 표기를 함께 실어 주면 BM25 가
    올바른 기록에 닿는다. 원문을 지우지 않고 더하기만 하므로, 대응표가 틀린
    경우에도 원래 번역으로 찾던 것을 잃지 않는다. 모델 호출이 없다.
    """
    add = [e for e in extra if e.lower() not in q_en.lower()]
    return (q_en + ' ' + ' '.join(add)).strip() if add else q_en


# 영어 전용 답변 프롬프트. 기본값이다.
# 병기는 생성 토큰을 늘려 그만큼 느려지므로, 켤 때만 한국어를 받는다.
ANSWER_EN = """You answer using ONLY the ship maintenance records below.

Output one JSON object. No markdown, no explanation.
{{"steps":[{{"step":"one step of the procedure, in English","record":<record number>,"quote":"the exact English sentence from that record, copied character for character"}}],
 "missing":"what the records do not cover, in English"}}

Rules:
- "quote" MUST be copied verbatim from the record. Do not summarise, translate or edit it.
- If you cannot find a supporting sentence, do not write that step at all.
- When you state a number (clearance, pressure, time, load), the quote must contain that number.
- Answer every part of the question that the records DO cover. If a record states a
  value, write it as a step with its quote. Do not leave out a fact that is present.
- "missing" is only for what the records genuinely do not contain. Never write a value
  into "missing" that you could have quoted from a record.
{naming}
[RECORDS]
{ctx}

[QUESTION]
{q}

[JSON]
"""


# 시연 도구 전용 답변 프롬프트.
# 측정에 쓴 rag.ANSWER 는 한국어 절차문 하나만 받는다. 화면에서 한국어와
# 영어를 나란히 보여 달라는 요구가 있어 영어 절차문을 한 칸 더 받는다.
# 인용(quote)의 규칙은 그대로 두었다 — 검증이 대조하는 대상이기 때문이다.
ANSWER_BI = """You answer using ONLY the ship maintenance records below.

Output one JSON object. No markdown, no explanation.
{{"steps":[{{"step":"one step of the procedure, in English","step_ko":"the same step in Korean","record":<record number>,"quote":"the exact English sentence from that record, copied character for character"}}],
 "missing":"what the records do not cover, in English"}}

Rules:
- "quote" MUST be copied verbatim from the record. Do not summarise, translate or edit it.
- "step" and "step_ko" must say the same thing in the two languages.
- Write "missing" in English only. Do not add a Korean version of it.
- If you cannot find a supporting sentence, do not write that step at all.
- When you state a number (clearance, pressure, time, load), the quote must contain that number.
- Answer every part of the question that the records DO cover. If a record states a
  value, write it as a step with its quote. Do not leave out a fact that is present.
- "missing" is only for what the records genuinely do not contain. Never write a value
  into "missing" that you could have quoted from a record.
{naming}
[RECORDS]
{ctx}

[QUESTION]
{q}

[JSON]
"""


def do_proc(q, k=4, pr=None, bi=False):
    from rag import bm25, sq
    import term_map
    pr = pr or Prog(None)
    q_en, t_tr = q, 0.0
    terms = [en for _ko, en in term_map.hints(q)]
    # 정비기록 본문과 검색 색인이 영어이므로 검색·생성은 영어로 한다.
    # 영어로 물으면 번역 단계가 없어 그만큼 실패 지점이 하나 줄어든다.
    if ask_lang(q) == 'ko':
        pr.start('Translating question')
        t, t_tr = ask_llm(TRANS_D.format(q=q, hints=term_map.hint_block(q)),
                          mx=200, rid=pr.rid)
        if not t.startswith('__ERROR__'):
            q_en = t.strip().strip('"').split('\n')[0]
    q_search = snap(q_en, terms)
    pr.start('Searching records')
    _t0 = time.time()
    hits = bm25(CFG['idx'], q_search, k=k)
    t_search = time.time() - _t0
    if not hits:
        return dict(kind='norec')
    bodies = {str(i): h[1]['meta'][6] for i, h in enumerate(hits, 1)}
    titles = [h[1]['meta'][2] for h in hits]
    ctx = '\n\n'.join(
        f"Record {i}. Title: {h[1]['meta'][2]}\nMachine: {h[1]['meta'][3]} / "
        f"{h[1]['meta'][4]}\nDate: {h[1]['meta'][5]}\n{h[1]['meta'][6]}"
        for i, h in enumerate(hits, 1))
    pr.start('Generating answer')
    tmpl = ANSWER_BI if bi else ANSWER_EN
    mx = 2600 if bi else 1600
    nm = naming_note(term_map.hints(q))
    raw, dt = ask_llm(tmpl.format(ctx=ctx, q=q_en, naming=nm), mx=mx, rid=pr.rid)
    obj = parse_json(raw)
    if obj is None:
        raw, d2 = ask_llm(tmpl.format(ctx=ctx, q=q_en, naming=nm)
                          + '\n\nReturn ONLY valid JSON.', mx=mx, rid=pr.rid)
        dt += d2
        obj = parse_json(raw)
    if obj is None:
        return dict(kind='badfmt', raw=raw[:1500], titles=titles, query=q_search,
                    prov=provenance(hits), sec=round(dt, 1))
    pr.start('Checking citations')
    steps, out = obj.get('steps') or [], []
    for s in steps:
        if not isinstance(s, dict):
            continue
        txt, quote = str(s.get('step', '')).strip(), str(s.get('quote', '')).strip()
        txt_ko = str(s.get('step_ko', '')).strip()
        try:
            rec = int(str(s.get('record')).strip())
        except (TypeError, ValueError):
            rec = None
        ok = bool(quote and sq(quote) and sq(quote) in sq(bodies.get(str(rec), '')))
        where = rec
        if not ok and quote:
            for kk, b in bodies.items():
                if sq(quote) and sq(quote) in sq(b):
                    ok, where = True, int(kk)
                    break
        out.append(dict(text=txt, text_ko=txt_ko, quote=quote, ok=ok, rec=where))
    return dict(kind='proc', steps=out, titles=titles, bodies=bodies, k=k,
                prov=provenance(hits), query=q_search, query_raw=q_en,
                terms=[dict(ko=a, en=b) for a, b in term_map.hints(q)],

                t_trans=round(t_tr, 1), t_search=round(t_search, 2),
                t_gen=round(dt, 1),
                scores=[round(h[0], 2) for h in hits],
                missing=(obj.get('missing') or '').strip(), sec=round(dt, 1),
                verified=sum(1 for x in out if x['ok']))


def parse_json(raw):
    t = re.sub(r'```\w*', '', raw).strip()
    m = re.search(r'\{.*\}', t, re.S)
    try:
        o = json.loads(m.group(0)) if m else None
    except Exception:
        o = None
    return o if isinstance(o, dict) else None


# ── 대화 이력 ────────────────────────────────────────────
# 앞선 문답을 답변 프롬프트에 섞으면 인용 검증이 대조하는 문맥이 오염된다.
# 그래서 이력을 답변에 쓰지 않고, 후속 질문을 "앞 대화 없이도 뜻이 통하는
# 하나의 질문"으로 다시 쓰는 데에만 쓴다. 다시 쓴 뒤로는 기존 경로가
# 그대로 돌아가므로, 측정한 파이프라인이 바뀌지 않는다.

FOLLOWUP = re.compile(
    r'(그거|그건|그게|그걸|그런|그럼|그러면|그때|그쪽|거기|저거|저건|이거|이건|'
    r'해당|방금|아까|앞서|위에서|그중|그 중|그 외|그외|나머지|반대로|같은 것|'
    r'같은 걸|더 자세|자세히|왜 그런|이유는|\bit\b|\bthat\b|\bthose\b|'
    r'\bsame\b|\bwhy\b|\bmore detail)', re.I)

REWRITE_KO = (
    '아래는 선박 정비 질의응답의 직전 대화다.\n\n{h}\n\n'
    '이어서 사용자가 물었다: "{q}"\n\n'
    '이 질문을 앞 대화를 보지 않고도 뜻이 통하는 하나의 완결된 질문으로 '
    '다시 써라. 앞 대화에 나온 대상만 채워 넣고 새로운 내용을 지어내지 마라. '
    '다시 쓴 질문 한 줄만 출력해라.')

REWRITE_EN = (
    'Below is the preceding turn of a ship maintenance Q&A.\n\n{h}\n\n'
    'The user then asked: "{q}"\n\n'
    'Rewrite this as one self-contained question that makes sense without the '
    'preceding turns. Fill in only what appeared above; invent nothing. '
    'Output the rewritten question on one line only.')


def needs_context(q, hist):
    """다시 쓸 필요가 있는지 싸게 먼저 거른다.

    모델 호출은 이 하드웨어에서 수십 초가 걸린다. 지시어가 있거나 문장이
    아주 짧을 때만 호출하고, 그 외에는 원문을 그대로 쓴다.
    """
    if not hist:
        return False
    q = (q or '').strip()
    if not q:
        return False
    # 한국어는 밀도가 높아 "중요 정비는 몇 건인가?" 도 13자에 그친다.
    # 길이만으로 거르면 완결된 질문까지 다시 쓰게 되므로 기준을 낮게 둔다.
    return bool(FOLLOWUP.search(q)) or len(q) <= 8


def resolve_followup(q, hist, rid=None):
    """후속 질문을 독립 질문으로 다시 쓴다. (질문, 바뀌었는가, 소요초)"""
    if not needs_context(q, hist):
        return q, False, 0.0
    lines = []
    for t in hist[-3:]:
        qq = str(t.get('q', '')).strip()
        aa = re.sub(r'\s+', ' ', str(t.get('a', '')).strip())[:200]
        if qq:
            lines.append(f'Q: {qq}\nA: {aa or "(답변 생략)"}')
    if not lines:
        return q, False, 0.0
    tmpl = REWRITE_KO if ask_lang(q) == 'ko' else REWRITE_EN
    out, dt = ask_llm(tmpl.format(h='\n\n'.join(lines), q=q), mx=160, rid=rid)
    if out.startswith('__ERROR__'):
        return q, False, round(dt, 1)
    cand = out.strip().strip('"').strip("'").split('\n')[0].strip()
    cand = re.sub(r'^(다시\s*쓴\s*질문|Rewritten question|Question)\s*[:：]\s*', '', cand)
    # 빈 값·과도하게 긴 문장·원문 그대로면 다시 쓰지 않은 것으로 본다
    if not cand or len(cand) > 200 or cand == q.strip():
        return q, False, round(dt, 1)
    return cand, True, round(dt, 1)


def summarize(r):
    """다음 턴에 넘길 답변 요약. 이력 프롬프트가 길어지지 않게 한 줄로 줄인다."""
    k = r.get('kind')
    if k == 'table':
        return r.get('answer') or ''
    if k == 'abstain':
        return r.get('head') or ''
    if k == 'proc':
        st = [s.get('text', '') for s in (r.get('steps') or []) if s.get('text')]
        return (st[0] if st else (r.get('missing') or ''))[:200]
    if k == 'norec':
        return '해당 기록을 찾지 못했습니다.'
    return ''


def handle(q, force=None, k=4, hist=None, rid=None, bi=False):
    from router import route_rule
    pr = Prog(rid)
    q0 = q
    if needs_context(q, hist):
        pr.start('Resolving context')
    q, rewritten, t_ctx = resolve_followup(q, hist, rid)
    kind, conf = route_rule(q)
    if force:
        kind = force
    r = do_agg(q, pr, bi) if kind == 'AGG' else do_proc(q, k=k, pr=pr, bi=bi)
    pr.finish()
    check_stop(rid)
    r['route'] = kind
    r['route_sure'] = conf >= 1.0
    r['lang'] = ask_lang(q)
    r['bi'] = bool(bi)
    if bi:
        # 질문도 두 언어로 보인다. 이미 있는 번역은 다시 만들지 않는다.
        if ask_lang(q) == 'ko':
            r['q_ko'] = q
            en = r.get('query_raw')          # 절차형이 검색하려고 만든 영어판
            if not en:
                en = one_line(ask_llm(TRANS.format(q=q), mx=200, rid=rid)[0])
            r['q_en'] = en or q
        else:
            r['q_en'] = q
            r['q_ko'] = one_line(
                ask_llm(TRANS_KO.format(q=q), mx=200, rid=rid)[0])
    if rewritten:
        r['asked'] = q0
        r['resolved'] = q
        r['t_ctx'] = t_ctx
    r['summary'] = summarize(r)
    r['stages'] = list(pr.done)
    pr.drop()
    return r


# ───────────────────────── HTTP ─────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        b = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        # 화면 파일을 브라우저가 캐시하면 도구를 고쳐도 옛 화면이 뜬다.
        # 실제로 한국어 병기를 켰는데 나오지 않는 일이 있었다 — 서버는
        # 새 칸을 보내는데 캐시된 화면이 그 칸을 몰랐기 때문이다.
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        # /c/<id> 는 대화 하나를 가리키는 주소다. 서버는 같은 화면을 주고,
        # 어느 대화를 열지는 화면이 주소를 읽어 정한다.
        # 물음표 뒤는 화면이 읽는 값이다(예: ?seed=1). 길을 고를 때는 뗀다.
        path = self.path.split('?', 1)[0]
        if (path in ('/', '/index.html')
                or re.match(r'^/c/[0-9a-fA-F]{8,32}$', path)):
            self._send(200, open(f'{HERE}/maintqa_web.html', encoding='utf-8').read(),
                       'text/html; charset=utf-8')
        elif self.path == '/api/settings':
            self._send(200, json.dumps(dict(
                model=gguf_spec(), mem=mem_now(), dict=dict_overview(),
                runtime=runtime_info(),
            ), ensure_ascii=False))
        elif self.path == '/api/dict':
            self._send(200, json.dumps(dict_entries(), ensure_ascii=False))
        elif self.path.startswith('/api/cancel'):
            rid = ''
            if '?' in self.path:
                from urllib.parse import parse_qs
                rid = (parse_qs(self.path.split('?', 1)[1]).get('id') or [''])[0]
            hit = stop_request(rid)
            self._send(200, json.dumps(dict(stopped=bool(rid), closed=hit)))
        elif self.path.startswith('/api/progress'):
            rid = ''
            if '?' in self.path:
                from urllib.parse import parse_qs
                rid = (parse_qs(self.path.split('?', 1)[1]).get('id') or [''])[0]
            with PROG_LOCK:
                d = PROG.get(rid)
            self._send(200, json.dumps(d or {}, ensure_ascii=False))
        elif self.path == '/api/info':
            lab = model_label()
            q = quant_of(lab)
            self._send(200, json.dumps(dict(
                app=CONF.APP_ID, version=CONF.VERSION,
                model=lab, model_file=gguf_spec().get('file') or '',
                quant=q, quant_text=quant_text(q),
                preset=CFG['conf']['preset'], base_url=CFG['conf']['base_url'],
                remote=not CONF.is_local(CFG['conf']['base_url']),
                demo=CFG['demo'], db=os.path.basename(CFG['conf']['db']),
                docs=CFG['idx']['N'], docs_ref=index_refs(), ready=health(),
                # 질문 파일은 요청마다 다시 읽는다. 화면 파일과 마찬가지로
                # 목록만 고칠 때 서버를 내렸다 올릴 이유가 없다.
                samples=load_samples()), ensure_ascii=False))
        else:
            self._send(404, '{}')

    def do_POST(self):
        if self.path != '/api/ask':
            return self._send(404, '{}')
        n = int(self.headers.get('Content-Length', 0))
        try:
            d = json.loads(self.rfile.read(n) or b'{}')
            hist = d.get('hist') or []
            hist = hist if isinstance(hist, list) else []
            r = handle(d.get('q', '').strip(), d.get('force'),
                       int(d.get('k', 4)), hist[-3:],
                       str(d.get('rid') or '')[:64], bool(d.get('bi')))
        except Stopped:
            r = dict(kind='stopped')
        except Exception as e:
            r = dict(kind='error', msg=f'{type(e).__name__}: {e}')
        finally:
            with STOP_LOCK:
                STOPPED.discard(str(d.get('rid') or '') if isinstance(d, dict) else '')
        self._send(200, json.dumps(r, ensure_ascii=False))


def load_samples():
    p = (CFG.get('conf') or {}).get('samples') or os.path.join(CONF.PKG, 'data', 'demo_questions.txt')
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding='utf-8'):
        m = re.match(r'\s*\d+\.\s+(.+?)(\s{2,}←.*)?$', ln.rstrip())
        if m:
            out.append(m.group(1).strip())
    return out


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--config', default=None, help='설정 파일(기본: 패키지의 config.json)')
    ap.add_argument('--port', type=int, default=None, help='화면 포트(기본: config.json 의 web_port)')
    ap.add_argument('--no-open', action='store_true')
    ap.add_argument('--no-warmup', action='store_true', help='접두부 예열을 건너뛴다')
    ap.add_argument('--hide', action='store_true',
                    help='콘솔 창을 감추고 돌린다 (START.bat 이 쓴다)')
    ap.add_argument('--log', default='', help='찍히는 글을 이 파일에도 남긴다')
    ap.add_argument('--pid-file', default=None,
                    help='번호표(이 서버의 프로세스 번호) 파일. 기본 logs/web.pid — STOP.bat 이 이 번호로 끈다. '
                         'tools/selftest.py 의 임시 서버는 임시 폴더에 쓴다(실제 서버의 번호표를 덮어쓰지 않게)')
    ap.add_argument('-h', '--help', action='store_true')
    a = ap.parse_args()
    if a.help:
        print(__doc__)
        return 0

    # 기록이 먼저다. 창을 감추기 전에 붙여야 감춘 뒤의 글도 남는다.
    if a.log:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
            sys.stdout = sys.stderr = Tee(sys.stdout, a.log)
        except Exception:
            pass
    if a.hide:
        hide_console()

    try:
        conf = CONF.load(a.config)
    except CONF.ConfigError as e:
        print('  [!] 설정 오류\n  ' + str(e).replace('\n', '\n  '))
        return 2
    CFG['conf'] = conf
    CFG['llm'] = LLM.Client(conf)
    LLM.default_client(conf)
    port = a.port or conf['web_port']
    db_path, idxf = conf['db'], conf['index']
    if not os.path.exists(db_path):
        print(f'  [!] DB 파일이 없습니다: {db_path}')
        return 2
    # 하위 모듈(harness · rag · sel_dict · build_prompt100)이 같은 파일을 보도록 고정한다
    os.environ['MAINT_DB'] = db_path
    os.environ['BM25_IDX'] = idxf
    os.environ['MAI_SMA_DATADICT'] = conf['datadict']
    print('─' * 54)
    print('  선박 정비이력 질의 도구 — 웹 화면 (학생 이식판)')
    print(f'  데이터: 학생용 DB {os.path.basename(db_path)} (정비 문서 1,000건 + 질의응답 200문항, 코드 가명처리)')
    print(f'  LLM: {CONF.summary(conf)}')
    print('─' * 54)
    for w in conf.get('_warnings') or []:
        print('  주의: ' + w)
    dk_ok, dk_msg = CONF.duckdb_status()
    if not dk_ok:
        print('  주의: ' + dk_msg)
    print('  참고: 연구에서 배포 기준(기권 지시이행 · 인용 충실도)을 통과한 것은 12B · 14B 급 모델이었습니다.')
    print('        더 작은 모델은 기록이 없는 항목에도 값을 만들거나 출력 형식을 자주 어깁니다.')

    ok, msg = start_server()
    print('  ' + msg)
    if not ok:
        return 3
    _ok, ids, _err = CFG['llm'].list_models()
    if not conf['model']:
        # model 을 비운 설정(llama.cpp 등): 화면의 모델 이름은 실행기가 알려 준 id 의 파일 이름
        CFG['server_model'] = server_model_name(ids)
    hit = LLM.model_listed(conf['model'], ids)
    if hit is False:
        listed = ', '.join(ids[:8]) or '(비어 있음)'
        if conf['preset'] in ('ollama', 'lmstudio'):
            print(f'  [!] 모델 "{conf["model"]}" 이(가) 서버 목록에 없습니다. 목록: {listed}')
            print('      ' + LLM.model_hint(conf['preset'], conf['model']))
            stop_embedded()
            return 4
        print(f'  주의: 모델 "{conf["model"]}" 이(가) 서버 목록에 없습니다(목록: {listed}). 그대로 진행합니다.')

    import pickle
    from harness import schema_ddl, get_con
    if not os.path.exists(idxf):
        print('  검색 색인이 없어 새로 만듭니다(몇 초)…')
        from rag import build_index
        build_index(db_path, idxf)
    CFG.update(demo=True, con=get_con(),
               idx=pickle.load(open(idxf, 'rb')),
               samples=load_samples(), schema=schema_ddl())
    print(f"  정비기록 색인 {CFG['idx']['N']:,}건 (정비 문서 기록 {CFG['idx']['N'] - index_refs():,} + "
          f"참고 기록 {index_refs():,})")
    # 첫 질문이 프롬프트 전체를 계산하지 않도록 배경에서 접두부를 올려 둔다
    if conf.get('warmup', True) and not a.no_warmup:
        threading.Thread(target=warm_prefix, daemon=True).start()
    # 설정 창의 값도 미리 읽어 둔다. 눌렀을 때 곧바로 떠야 한다.
    threading.Thread(target=warm_settings, daemon=True).start()

    class Srv(ThreadingHTTPServer):
        # 윈도우에서는 이 값이 켜져 있으면 이미 듣고 있는 포트에도 두 번째
        # 프로세스가 '성공적으로' 묶인다. 그러면 요청은 먼저 뜬 쪽으로만
        # 가고 새 판은 조용히 살아 있어, 어느 것이 답하는지 알 수 없게 된다.
        allow_reuse_address = False

    try:
        srv = Srv(('127.0.0.1', port), H)
    except OSError:
        print(f'  [!] {port} 번 포트를 이미 쓰고 있습니다.')
        print('      STOP.bat 으로 먼저 끝내거나 config.json 의 web_port 를 바꾸십시오.')
        stop_embedded()
        return 5
    pidf = os.path.abspath(a.pid_file) if a.pid_file else os.path.join(CONF.LOGS, 'web.pid')
    try:
        os.makedirs(os.path.dirname(pidf), exist_ok=True)
        with open(pidf, 'w') as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    url = f'http://127.0.0.1:{port}/'
    print(f'\n  화면 주소: {url}')
    print('  종료하려면 STOP.bat (또는 이 창에서 Ctrl+C)\n')
    if not a.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_embedded()
        try:
            os.remove(pidf)
        except OSError:
            pass
        print('\n  종료했습니다.')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
