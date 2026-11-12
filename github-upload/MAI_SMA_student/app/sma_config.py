# -*- coding: utf-8 -*-
"""설정 읽기 — config.json 하나로 LLM 연결·화면 포트·데이터 경로를 정한다.

학생 이식판에서 새로 만든 모듈이다. 원 시연판은 llama-server 실행 파일과 GGUF 모델
파일을 직접 띄웠지만, 이식판은 학생 PC 에 이미 설치된 LLM 실행기(Ollama · LM Studio ·
llama.cpp · 그 밖의 OpenAI 호환 서버)에 붙는다. 무엇에 붙을지는 config.json 이 정한다.

설정 파일 위치: --config 인자 > 환경변수 MAI_SMA_CONFIG > <패키지>/config.json
상대경로는 모두 패키지 폴더(이 파일의 한 단계 위) 기준이다.
"""
import ipaddress
import json
import os
import re
import urllib.parse

APP = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(APP)
LOGS = os.path.join(PKG, 'logs')
APP_ID = 'MAI_SMA_student'
VERSION = 'v2 (2026-11-10, 정비 문서 1,000건 DB)'

PRESETS = ('llamacpp', 'ollama', 'lmstudio', 'openai')
PRESET_ALIAS = {
    'llama': 'llamacpp', 'llama.cpp': 'llamacpp', 'llama-server': 'llamacpp', 'llama_cpp': 'llamacpp',
    'lm-studio': 'lmstudio', 'lm_studio': 'lmstudio', 'lm studio': 'lmstudio',
    'openai_compatible': 'openai', 'openai-compatible': 'openai', 'compatible': 'openai',
}
DEFAULT_BASE = {
    'llamacpp': 'http://127.0.0.1:8080/v1',
    'ollama': 'http://localhost:11434/v1',
    'lmstudio': 'http://localhost:1234/v1',
    'openai': 'http://127.0.0.1:8000/v1',
}
# 실행기별 이름. 화면·안내문에 쓴다.
PRESET_LABEL = {
    'llamacpp': 'llama.cpp (llama-server)',
    'ollama': 'Ollama',
    'lmstudio': 'LM Studio',
    'openai': 'OpenAI-compatible server',
}

DEFAULTS = {
    'preset': 'ollama',
    'base_url': '',
    'model': '',
    'api_key': '',
    'api_key_env': '',
    'context_tokens': 8192,
    'timeout_sec': 600,
    'retries': 1,
    'temperature': 0,
    'seed': None,
    'extra_body': {},
    'strip_think': True,
    'web_port': 8780,
    'warmup': True,
    'sql_timeout_sec': 60,
    'allow_remote': False,
    'gguf_path': '',
    'llama_server': {
        'exe': '',
        'model_path': '',
        'ngl': 99,
        'extra_args': ['--cache-reuse', '256', '-np', '1'],
    },
    'db': 'data/MAI_SMA_학생용DB_1000.duckdb',      # 학생용 DB(정비 문서 1,000건 + 질의응답 200문항)
    'index': 'data/bm25_1000.pkl',                  # 검색 색인(정비 문서 기록 + 참고 기록). 없으면 기동할 때 만든다
    'datadict': 'data/datadict_deid.txt',
    'samples': 'data/demo_questions.txt',
}
KNOWN = set(DEFAULTS)
PATH_KEYS = ('db', 'index', 'datadict', 'samples', 'gguf_path')

# 자리표시값. 이 글자가 남아 있으면 아직 채우지 않은 것이다.
PLACEHOLDER = re.compile(r'<<|>>|여기에|MODEL_NAME_HERE', re.I)

# 이 패키지가 시험한 duckdb 판: 1.5 이상 2 미만(requirements.txt 와 같다).
#   2.x   relation 에 fetchone 이 없다(평가기의 키트판 코드 등이 쓴다)
#   ~1.4  임시 커서 위에 만든 relation 은 커서가 먼저 닫혀 읽지 못한다('Connection has already been closed')
DUCKDB_RANGE = ((1, 5), (2, 0))
DUCKDB_FIX = 'python -m pip install "duckdb>=1.5,<2"'


class ConfigError(Exception):
    """설정 파일을 읽을 수 없거나 꼭 필요한 값이 빠졌다."""


def duckdb_version():
    """(설치된 duckdb 판 글자 또는 None, (주, 부) 또는 None)."""
    try:
        import duckdb
    except ImportError:
        return None, None
    v = str(getattr(duckdb, '__version__', '') or '')
    m = re.match(r'\s*(\d+)\.(\d+)', v)
    return v, ((int(m.group(1)), int(m.group(2))) if m else None)


def duckdb_status():
    """(맞는 판인가, 한 줄 설명). selftest · check_llm --check-duckdb · START.bat · start.sh · 웹 서버가 쓴다."""
    v, t = duckdb_version()
    if v is None:
        return False, 'duckdb 가 설치되어 있지 않습니다. 패키지 폴더에서: python -m pip install -r requirements.txt'
    lo, hi = DUCKDB_RANGE
    if t is None or not (lo <= t < hi):
        return False, f'duckdb {v} — 이 패키지는 duckdb 1.5 이상 2 미만에서 돈다. 판을 맞추십시오: {DUCKDB_FIX}'
    return True, f'duckdb {v} — 맞는 판입니다(1.5 이상 2 미만)'


def config_path(arg=None):
    """설정 파일 경로. 인자 > 환경변수 > 패키지 기본."""
    p = arg or os.environ.get('MAI_SMA_CONFIG') or os.path.join(PKG, 'config.json')
    return os.path.abspath(p)


def resolve(p):
    """패키지 기준 상대경로를 절대경로로. 빈 값은 그대로."""
    if not p:
        return ''
    p = os.path.expandvars(os.path.expanduser(str(p)))
    return os.path.abspath(p if os.path.isabs(p) else os.path.join(PKG, p))


def norm_preset(v):
    s = str(v or '').strip().lower()
    return PRESET_ALIAS.get(s, s)


def norm_base(b):
    """기반 주소를 고른다. '127.0.0.1:8080' 처럼 적어도 되고, 끝에 /v1 이 없으면 붙인다."""
    b = str(b or '').strip().rstrip('/')
    if not b:
        return ''
    if '://' not in b:
        b = 'http://' + b
    for tail in ('/chat/completions', '/models'):
        if b.endswith(tail):
            b = b[:-len(tail)]
    if urllib.parse.urlsplit(b).path in ('', '/'):
        b += '/v1'
    return b


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or '').lower()


def port_of(url):
    u = urllib.parse.urlsplit(url)
    return u.port or (443 if u.scheme == 'https' else 80)


def is_local(url):
    """이 PC 또는 같은 사내망(사설 주소)인가. 아니면 데이터가 밖으로 나간다."""
    h = host_of(url)
    if h in ('localhost', '::1') or h.endswith('.local') or h.endswith('.localhost'):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def is_loopback(url):
    h = host_of(url)
    return h in ('localhost', '::1') or h.startswith('127.')


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None, require_model=True):
    """config.json 을 읽어 기본값과 합친다. 문제가 있으면 ConfigError."""
    p = config_path(path)
    if not os.path.exists(p):
        raise ConfigError(f'설정 파일이 없습니다: {p}\n'
                          '  config_examples 폴더의 예시 하나를 패키지 폴더에 config.json 으로 복사해 고치십시오.')
    try:
        with open(p, encoding='utf-8-sig') as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        tip = '  쉼표·따옴표·중괄호를 확인하십시오. JSON 에는 주석(//, #)을 쓸 수 없습니다.'
        if 'escape' in e.msg.lower():
            # 윈도 경로를 그대로 적으면 C:\llama 의 \l 같은 것이 JSON 의 이스케이프로 읽힌다
            tip = ('  경로의 역슬래시(\\)를 그대로 적은 것 같습니다. JSON 에서는 / 로 바꾸거나 \\\\ 로 두 번 적으십시오\n'
                   '  (예: "C:/llama/llama-server.exe" 또는 "C:\\\\llama\\\\llama-server.exe").\n'
                   '  llama.cpp 의 model 은 비워 두어도 됩니다(실행기 목록의 id 는 전체 경로라 그대로 붙이지 않습니다).')
        raise ConfigError(f'config.json 을 JSON 으로 읽지 못했습니다(줄 {e.lineno}, 칸 {e.colno}): {e.msg}\n' + tip)
    if not isinstance(raw, dict):
        raise ConfigError('config.json 의 맨 바깥은 { } 로 된 객체여야 합니다.')
    cfg = _merge(DEFAULTS, raw)
    cfg['_path'] = p
    cfg['_unknown'] = sorted(k for k in raw if k not in KNOWN and not k.startswith('_'))
    cfg['preset'] = norm_preset(cfg.get('preset'))
    if cfg['preset'] not in PRESETS:
        raise ConfigError(f'preset 값 "{raw.get("preset")}" 을 모릅니다. '
                          f'{", ".join(PRESETS)} 중 하나를 쓰십시오.')
    cfg['base_url'] = norm_base(cfg.get('base_url')) or DEFAULT_BASE[cfg['preset']]
    cfg['model'] = str(cfg.get('model') or '').strip()
    key_env = str(cfg.get('api_key_env') or '').strip()
    if not cfg.get('api_key') and key_env:
        cfg['api_key'] = os.environ.get(key_env, '')
    for k in PATH_KEYS:
        cfg[k] = resolve(cfg.get(k))
    ls = cfg.get('llama_server') or {}
    for k in ('exe', 'model_path'):
        ls[k] = resolve(ls.get(k)) if ls.get(k) else ''
    cfg['llama_server'] = ls
    if not isinstance(cfg.get('extra_body'), dict):
        raise ConfigError('extra_body 는 { } 로 된 JSON 객체여야 합니다(없으면 {}).')
    for k in ('context_tokens', 'timeout_sec', 'web_port', 'retries', 'sql_timeout_sec'):
        try:
            cfg[k] = int(cfg[k])
        except (TypeError, ValueError):
            raise ConfigError(f'{k} 는 숫자여야 합니다: {cfg[k]!r}')
    issues = problems(cfg, require_model)
    hard = [m for lvl, m in issues if lvl == 'error']
    if hard:
        raise ConfigError('\n'.join(hard))
    cfg['_warnings'] = [m for lvl, m in issues if lvl == 'warn']
    return cfg


def problems(cfg, require_model=True):
    """[(수준, 문장)] — 수준은 'error' 또는 'warn'."""
    out = []
    pr, model, base = cfg['preset'], cfg['model'], cfg['base_url']
    if PLACEHOLDER.search(model):
        out.append(('error', f'config.json 의 model 이 아직 자리표시값입니다: "{model}"\n'
                             '  실행기에 설치된 모델 이름을 그대로 적으십시오'
                             '(Ollama: ollama list / LM Studio: 내 모델 목록 / llama.cpp: 생략 가능).'))
    elif require_model and not model and pr in ('ollama', 'lmstudio', 'openai'):
        out.append(('error', f'preset "{pr}" 은 model 이름이 꼭 필요합니다. config.json 의 model 을 채우십시오.'))
    if not urllib.parse.urlsplit(base).scheme in ('http', 'https'):
        out.append(('error', f'base_url 형식이 이상합니다: {base}  (예: {DEFAULT_BASE[pr]})'))
    if not is_local(base) and not cfg.get('allow_remote'):
        out.append(('error', f'base_url 이 이 PC 나 사내망 주소가 아닙니다: {base}\n'
                             '  이 시스템은 온프레미스(외부 전송 없음)가 원칙입니다. 정비기록이 밖으로 나가도 되는지\n'
                             '  담당 교수와 데이터 제공처의 허락을 받은 경우에만 "allow_remote": true 로 바꾸십시오.'))
    if cfg['context_tokens'] < 8192:
        out.append(('warn', f'context_tokens={cfg["context_tokens"]} — 집계형 프롬프트(약 6~7천 토큰)가 잘릴 수 있습니다.'
                            ' 실행기의 문맥 길이를 8192 이상으로 두십시오.'))
    if cfg['_unknown']:
        out.append(('warn', f'모르는 설정 이름: {", ".join(cfg["_unknown"])} (오타인지 확인하십시오)'))
    return out


def summary(cfg):
    """한 줄 요약(비밀 키는 빼고)."""
    m = cfg['model'] or '(서버 기본 모델)'
    return f'{PRESET_LABEL[cfg["preset"]]} · {cfg["base_url"]} · 모델 {m}'
