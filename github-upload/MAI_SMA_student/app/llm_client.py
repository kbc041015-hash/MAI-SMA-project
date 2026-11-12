# -*- coding: utf-8 -*-
"""OpenAI 호환 LLM 호출 — 요청 프리셋 · 상태 확인 · 오류 해석.

학생 이식판에서 새로 만든 모듈이다. 이 시스템이 LLM 에 바라는 것은 하나뿐이다.

    POST {base_url}/chat/completions  {"messages":[{"role":"user","content":<프롬프트>}], ...}
    → choices[0].message.content

Ollama · LM Studio · llama.cpp(llama-server) · vLLM 등 대부분의 로컬 실행기가 이 방식
(OpenAI 호환)을 지원한다. 상태 확인은 GET {base_url}/models 로 한다.

요청 본문은 비식별 키트 평가기(eval/run_eval.py)의 build_body 와 같은 규칙으로 만든다.
  llamacpp  원 실험·원 시연판과 같은 본문: temperature 0 · top_k 1 · seed 42 · 사고(thinking) 끔
            (chat_template_kwargs). model 은 설정에 있을 때만 넣는다.
  ollama    temperature 0 · seed 42. Ollama 의 OpenAI 호환 층은 top_k·chat_template_kwargs 를
            받지 않으므로 뺀다. model 필수.
  lmstudio  temperature 0 · top_k 1 · seed 42. chat_template_kwargs 는 뺀다. model 필수.
  openai    그 밖의 OpenAI 호환 서버. 표준 필드(model · messages · temperature · max_tokens)만.
config.json 의 seed(숫자/false) · temperature(null 이면 안 보냄) · extra_body 로 덧붙이거나 바꿀 수 있다.

오류 처리는 run_eval.py 의 chat() 과 같은 원칙을 따른다.
  401·403(인증) · 404(주소·모델) · 400·422(지원하지 않는 필드·문맥 초과) → 다시 보내도 같다. 바로 알린다.
  429 · 5xx · 연결 오류 → 잠시 뒤 다시 보낸다(retries 회).
"""
import http.client
import json
import os
import re
import select
import socket
import ssl
import subprocess
import time
import urllib.parse

import sma_config as CONF

PRESET_FIELDS = {
    'llamacpp': {'top_k': 1, 'seed': 42, 'chat_template_kwargs': {'enable_thinking': False}},
    'ollama': {'seed': 42},
    'lmstudio': {'top_k': 1, 'seed': 42},
    'openai': {},
}

# 사고형 모델이 본문에 섞어 내는 생각 덩어리. 집계형은 첫 SELECT 부터 자르므로
# 생각 속 'SELECT' 를 집으면 엉뚱한 SQL 이 된다. 본문에서 걷어 낸다.
_THINK = re.compile(r'<think>.*?</think>\s*', re.S | re.I)
_THINK_OPEN = re.compile(r'^\s*<think>', re.I)


class Cancelled(Exception):
    """호출 쪽이 중단을 요청했다."""


def endpoint(base):
    """기반 주소 → chat/completions 주소. run_eval.py endpoint() 와 같은 규칙."""
    b = CONF.norm_base(base)
    return b + '/chat/completions'


def strip_think(text):
    """(<think> 를 걷어 낸 본문, 생각 덩어리가 있었는가)."""
    if not text:
        return text or '', False
    t, n = _THINK.subn('', text)
    if _THINK_OPEN.match(t):          # 닫히지 않은 생각 — 출력 한도에 걸려 잘린 것
        return '', True
    return t, n > 0


def context_hint(preset):
    return {
        'ollama': ('Ollama 의 문맥 길이를 8192 이상으로 올리십시오. ① Ollama 앱 설정의 Context length 를 8k 이상으로, '
                   '또는 ② 환경변수 OLLAMA_CONTEXT_LENGTH=8192 를 설정하고 Ollama 를 다시 시작, 또는 '
                   '③ Modelfile(FROM <모델> / PARAMETER num_ctx 8192)로 ollama create 한 모델 이름을 쓰십시오.'),
        'lmstudio': ('LM Studio 에서 모델을 불러올 때 Context Length 를 8192 이상으로 두십시오'
                     '(명령줄: lms load <모델> --context-length 8192).'),
        'llamacpp': 'llama-server 를 -c 8192 이상으로 띄우십시오(-np 1 권장. 슬롯이 여럿이면 문맥이 슬롯 수로 나뉩니다).',
        'openai': '서버의 최대 문맥 길이(max_model_len 등)를 8192 이상으로 두십시오.',
    }.get(preset, '실행기의 문맥 길이를 8192 이상으로 두십시오.')


def start_hint(preset):
    return {
        'ollama': 'Ollama 를 켜십시오(시작 메뉴의 Ollama 앱, 또는 터미널에서 ollama serve).',
        'lmstudio': ('LM Studio 의 Developer(개발자) 화면에서 로컬 서버를 켜고(Status: Running, 또는 lms server start) '
                     '모델을 불러오십시오.'),
        'llamacpp': ('llama-server -m <모델.gguf> --port 8080 -c 8192 -np 1 을 실행하십시오. 또는 config.json 의 '
                     '"llama_server" 에 "exe"(llama-server 실행 파일)와 "model_path"(.gguf)를 둘 다 채우면 웹 서버가 직접 '
                     '띄웁니다 — 경로는 / 로 적으십시오(예: "C:/llama/llama-server.exe". 00_먼저_읽기.md 4절).'),
        'openai': 'OpenAI 호환 서버를 켜고 base_url(주소·포트·/v1)을 확인하십시오.',
    }.get(preset, 'LLM 실행기를 켜십시오.')


def model_hint(preset, model):
    return {
        'ollama': f'ollama list 에 보이는 이름을 그대로 적으십시오(없으면 ollama pull {model or "<모델>"}).',
        'lmstudio': 'LM Studio 에서 모델을 내려받아 불러온 뒤, 모델 정보의 API identifier 를 그대로 적으십시오.',
        'llamacpp': 'llama-server 는 model 이름을 보지 않습니다. 비워 두어도 됩니다.',
        'openai': '서버의 /v1/models 목록에 있는 id 를 그대로 적으십시오.',
    }.get(preset, '')


def _retry_after(headers):
    v = {k.lower(): x for k, x in (headers or {}).items()}.get('retry-after')
    try:
        return float(v) if v else None
    except ValueError:
        return None


_CTX_PAT = re.compile(r'context|exceed|too long|maximum.*tokens|n_ctx|prompt is too|truncat', re.I)
_FIELD_PAT = re.compile(r'temperature|top_k|seed|chat_template_kwargs|unrecogni[sz]ed|unsupported|unknown (parameter|field)'
                        r'|extra (inputs|fields)', re.I)


def explain_http(status, text, cfg):
    """(다시 보내도 같은가, 사람이 읽는 설명)."""
    pr, model = cfg['preset'], cfg['model']
    t = (text or '')[:400]
    if status in (401, 403):
        return True, f'인증 거부(HTTP {status}). api_key 를 확인하십시오(로컬 실행기는 보통 키가 필요 없습니다). {t}'
    if status == 404:
        if re.search(r'model', t, re.I):
            return True, f'모델 "{model}" 을(를) 찾지 못했습니다(HTTP 404). {model_hint(pr, model)} {t}'
        return True, f'주소를 찾지 못했습니다(HTTP 404). base_url 이 …/v1 로 끝나는지 확인하십시오. {t}'
    if status in (400, 413, 422, 500) and _CTX_PAT.search(t):
        return True, f'프롬프트가 실행기의 문맥 길이보다 깁니다(HTTP {status}). {context_hint(pr)} {t}'
    if status in (400, 422):
        if _FIELD_PAT.search(t):
            return True, (f'실행기가 요청의 일부 필드를 받지 않습니다(HTTP {status}). config.json 의 preset 이 실행기와 '
                          f'맞는지 확인하고, extra_body 를 비워 보십시오. {t}')
        return True, f'요청이 거부되었습니다(HTTP {status}). {t}'
    if status in (408, 409, 425, 429) or status >= 500:
        return False, f'일시 오류(HTTP {status}). {t}'
    return True, f'HTTP {status}. {t}'


def model_listed(model, ids):
    """설정의 모델이 서버 목록에 있는가. 모델을 안 적었으면 None."""
    if not model:
        return None
    m = model.strip().lower()
    low = [str(i).strip().lower() for i in ids]
    if m in low:
        return True
    if ':' not in m and (m + ':latest') in low:          # Ollama 는 태그를 빼면 :latest
        return True
    names = {os.path.basename(i.replace('\\', '/')) for i in low}   # llama.cpp 는 경로를 id 로 쓴다
    return os.path.basename(m.replace('\\', '/')) in names


class Client:
    """config.json 으로 만든 OpenAI 호환 호출기."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.preset = cfg['preset']
        self.base = CONF.norm_base(cfg['base_url'])
        self.url = endpoint(self.base)
        u = urllib.parse.urlsplit(self.url)
        self.scheme = u.scheme
        self.host = u.hostname or '127.0.0.1'
        self.port = u.port or (443 if u.scheme == 'https' else 80)
        self.chat_path = u.path + (('?' + u.query) if u.query else '')
        self.models_path = urllib.parse.urlsplit(self.base).path.rstrip('/') + '/models'
        # 윈도우에서 localhost 는 IPv6(::1)부터 시도한다. 실행기가 IPv4 에서만 듣고 있으면
        # 거절당한 뒤 넘어가기까지 요청마다 2초가량 늦어진다. 127.0.0.1 부터 붙는다.
        self.connect_host = '127.0.0.1' if self.host == 'localhost' else self.host
        self.timeout = cfg.get('timeout_sec') or 600
        self.retries = cfg.get('retries', 1)
        self.headers = {'Content-Type': 'application/json'}
        if cfg.get('api_key'):
            self.headers['Authorization'] = 'Bearer ' + cfg['api_key']
        host = (self.host or '').lower()
        self.max_param = ('max_completion_tokens'
                          if self.preset == 'openai' and host.endswith('openai.com') else 'max_tokens')
        self._health = (0.0, False)

    # ── 요청 본문 ─────────────────────────────────────────
    def body(self, prompt, max_tokens=None):
        cfg, pf = self.cfg, PRESET_FIELDS[self.preset]
        b = {}
        if cfg.get('model'):
            b['model'] = cfg['model']
        b['messages'] = [{'role': 'user', 'content': prompt}]
        if cfg.get('temperature', 0) is not None:
            b['temperature'] = cfg.get('temperature', 0)
        if 'top_k' in pf:
            b['top_k'] = pf['top_k']
        seed = cfg.get('seed')
        if seed is None:
            seed = pf.get('seed')
        if seed is not None and seed is not False:
            b['seed'] = seed
        if max_tokens:
            b[self.max_param] = max_tokens
        if 'chat_template_kwargs' in pf:
            b['chat_template_kwargs'] = dict(pf['chat_template_kwargs'])
        b.update(cfg.get('extra_body') or {})
        return b

    def request_summary(self, max_tokens=600):
        """화면·점검용. 프롬프트 자리는 비우고 실제로 보내는 필드만 보인다."""
        b = self.body('<prompt>', max_tokens)
        b.pop('messages', None)
        return b

    # ── 전송 ─────────────────────────────────────────────
    def _conn(self, timeout):
        if self.scheme == 'https':
            return http.client.HTTPSConnection(self.connect_host, self.port, timeout=timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self.connect_host, self.port, timeout=timeout)

    def _request(self, method, path, body=None, timeout=None, conn_hook=None, wait_check=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers = dict(self.headers)
        if self.connect_host != self.host:
            headers['Host'] = f'{self.host}:{self.port}'
        if data is None:
            headers.pop('Content-Type', None)
        for attempt in (0, 1):
            conn = self._conn(timeout or self.timeout)
            if conn_hook:
                conn_hook(conn)
            try:
                conn.request(method, path, body=data, headers=headers)
                if wait_check is not None and conn.sock is not None:
                    # 답을 기다리는 동안 0.5초마다 중단 요청을 본다. 중단이면 wait_check 가 예외를
                    # 던지고, 아래 finally 가 연결을 닫아 실행기도 생성을 멈춘다(서버가 연결 끊김을
                    # 알아채지 못하는 경우에도 화면은 곧바로 풀린다).
                    deadline = time.time() + (timeout or self.timeout)
                    while not select.select([conn.sock], [], [], 0.5)[0]:
                        wait_check()
                        if time.time() > deadline:
                            raise socket.timeout('응답 시간 초과')
                r = conn.getresponse()
                return r.status, dict(r.getheaders()), r.read()
            except ConnectionRefusedError:
                # localhost 를 127.0.0.1 로 붙였는데 거절되면 IPv6 로 한 번 더 본다
                if attempt == 0 and self.host == 'localhost' and self.connect_host == '127.0.0.1':
                    self.connect_host = '::1'
                    continue
                raise
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def list_models(self, timeout=5):
        """(응답했는가, 모델 id 목록, 오류문)."""
        try:
            status, _h, raw = self._request('GET', self.models_path, timeout=timeout)
        except (OSError, http.client.HTTPException) as e:
            return False, [], f'{type(e).__name__}: {e}'
        if status != 200:
            return False, [], f'HTTP {status}: {raw[:200].decode("utf-8", "replace")}'
        try:
            d = json.loads(raw.decode('utf-8'))
        except ValueError:
            return True, [], '모델 목록이 JSON 이 아닙니다'
        ids = []
        items = (d.get('data') or d.get('models') or []) if isinstance(d, dict) else []
        for m in items:
            if isinstance(m, dict):
                v = m.get('id') or m.get('name') or m.get('model')
                if v:
                    ids.append(str(v))
        return True, ids, ''

    def health(self, max_age=2.0):
        """GET {base_url}/models 가 200 이면 준비된 것으로 본다. 2초 동안은 앞 결과를 쓴다."""
        t, ok = self._health
        if time.time() - t < max_age:
            return ok
        ok = self.list_models(timeout=3)[0]
        self._health = (time.time(), ok)
        return ok

    def chat(self, prompt, max_tokens=600, retries=None, conn_hook=None, stop_check=None, allow_empty=False):
        """한 번 묻는다. 반환: dict(content, error, fatal, sec, ...). 예외는 stop_check 가 던진 것만 나간다."""
        body = self.body(prompt, max_tokens)
        retries = self.retries if retries is None else retries
        t_all, err = time.time(), '알 수 없는 오류'
        for attempt in range(retries + 1):
            if stop_check:
                stop_check()
            t = time.time()
            wait = 2.0 * (attempt + 1)
            try:
                status, headers, raw = self._request('POST', self.chat_path, body, conn_hook=conn_hook,
                                                     wait_check=stop_check)
            except (OSError, http.client.HTTPException) as e:
                if stop_check:
                    stop_check()                  # 중단으로 끊긴 것이면 여기서 빠져나간다
                err = (f'LLM 서버에 연결하지 못했습니다({type(e).__name__}: {e}). '
                       f'{self.base} — {start_hint(self.preset)}')
            else:
                dt = time.time() - t
                text = raw.decode('utf-8', 'replace')
                if status != 200:
                    fatal, msg = explain_http(status, text, self.cfg)
                    if fatal:
                        return dict(content='', error=msg, fatal=True, sec=0.0, attempts=attempt + 1)
                    err, ra = msg, _retry_after(headers)
                    if ra is not None:
                        wait = min(30.0, ra)
                else:
                    try:
                        d = json.loads(text)
                    except ValueError:
                        d = None
                    if not isinstance(d, dict) or 'choices' not in d:
                        e2 = str((d or {}).get('error') if isinstance(d, dict) else text)[:300]
                        fatal, msg = explain_http(500 if _CTX_PAT.search(e2) else 400, e2, self.cfg)
                        return dict(content='', error='응답 형식이 OpenAI 호환이 아닙니다: ' + msg,
                                    fatal=True, sec=0.0, attempts=attempt + 1)
                    ch = (d.get('choices') or [{}])[0] or {}
                    msg = ch.get('message') or {}
                    content = msg.get('content')
                    if isinstance(content, list):
                        content = ''.join(p.get('text', '') for p in content if isinstance(p, dict))
                    content = content or ''
                    thinking = bool(msg.get('reasoning_content') or msg.get('reasoning'))
                    if self.cfg.get('strip_think', True):
                        content, had = strip_think(content)
                        thinking = thinking or had
                    usage = d.get('usage') or {}
                    out = dict(content=content, error=None, fatal=False, sec=round(dt, 2),
                               total_sec=round(time.time() - t_all, 2), attempts=attempt + 1,
                               finish_reason=ch.get('finish_reason'), thinking=thinking,
                               prompt_tokens=usage.get('prompt_tokens'),
                               completion_tokens=usage.get('completion_tokens'),
                               model_returned=d.get('model'))
                    if not content.strip() and not allow_empty:
                        if thinking or ch.get('finish_reason') == 'length':
                            out['error'] = ('모델이 사고(thinking)에 출력 한도를 다 써서 답이 비었습니다. 사고를 끄는 '
                                            '설정을 쓰거나(00_먼저_읽기.md 6절) 사고하지 않는 모델을 고르십시오.')
                        else:
                            out['error'] = '모델이 빈 답을 돌려주었습니다.'
                        out['fatal'] = True
                    return out
            if attempt < retries:
                time.sleep(wait)
        return dict(content='', error=err, fatal=False, sec=0.0, attempts=retries + 1)


# ── 단독 실행용 기본 호출기(rag.py · router.py · maintqa_cli.py) ──────────
_DEFAULT = {}


def default_client(cfg=None):
    if cfg is not None:
        _DEFAULT['c'] = Client(cfg)
    if 'c' not in _DEFAULT:
        _DEFAULT['c'] = Client(CONF.load())
    return _DEFAULT['c']


def ask(prompt, mx=1200, retries=None):
    """(본문, 초). 실패하면 '__ERROR__ …' — 원 시연판 ask_llm 과 같은 약속."""
    r = default_client().chat(prompt, mx, retries=retries)
    if r['error']:
        return f"__ERROR__ {r['error']}", 0.0
    return r['content'], r['sec']


# ── 선택 기능: llama-server 직접 띄우기 ───────────────────────────
def embedded_ready(cfg):
    ls = cfg.get('llama_server') or {}
    return bool(ls.get('exe') and ls.get('model_path'))


def launch_llama_server(cfg, log_path, pid_path=None):
    """config.json 의 llama_server 로 추론 서버를 띄운다. 창은 만들지 않는다. Popen 을 돌려준다."""
    ls = cfg['llama_server']
    exe, mp = ls['exe'], ls['model_path']
    if not os.path.exists(exe):
        raise FileNotFoundError(f'llama-server 실행 파일이 없습니다: {exe}')
    if not os.path.exists(mp):
        raise FileNotFoundError(f'모델 파일이 없습니다: {mp}')
    if not CONF.is_loopback(cfg['base_url']):
        raise ValueError('llama_server 를 직접 띄울 때는 base_url 이 127.0.0.1 이나 localhost 여야 합니다.')
    port = CONF.port_of(cfg['base_url'])
    args = [exe, '-m', mp, '--host', '127.0.0.1', '--port', str(port),
            '-ngl', str(ls.get('ngl', 99)), '-c', str(cfg.get('context_tokens', 8192))]
    args += [str(x) for x in (ls.get('extra_args') or [])]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log = open(log_path, 'w', encoding='utf-8', errors='replace')
    p = subprocess.Popen(args, cwd=os.path.dirname(exe), stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if pid_path:
        with open(pid_path, 'w') as f:
            f.write(str(p.pid))
    return p
