# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/model_specs.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · KV 캐시: 레이어별 헤드 차원(key_length_swa)을 반영(_kv_bytes). 슬라이딩 윈도우 모델은 상한(≤)으로 표시
#   · spec_path(경로): 설정의 GGUF 파일 하나를 읽는다
#   · __main__: 연구용 모델 6종 대신 인자로 받은 GGUF 하나
"""모델 제원 수집 — GGUF 파일 헤더에서 직접 읽는다.

기억이나 공개 자료에 의존하지 않고 실제로 실험에 쓴 파일에서 읽는다.
GGUF 는 파일 앞부분에 키-값 메타데이터를 담고 있어 별도 도구 없이 파싱할 수 있다.

읽는 것
  architecture / 파라미터 수 / 학습 컨텍스트 길이 / 임베딩 차원 /
  레이어 수 / 어텐션 헤드 / 어휘 크기 / 양자화 종류 / 파일 크기

계산하는 것
  적재 시 필요한 메모리 = 가중치 + KV 캐시(실험에 쓴 컨텍스트 8,192 기준)

사용: python model_specs.py
"""
import os, struct, json

HERE = os.path.dirname(os.path.abspath(__file__)).replace('\\', '/')
MDIR = f'{HERE}/../models'

MODELS = [
    ('e2b',  'gemma-4-E2B-it-Q4_K_M.gguf',            'Gemma-4 E2B',   'Google'),
    ('qw17', 'Qwen3-1.7B-Q8_0.gguf',                  'Qwen3-1.7B',    'Alibaba'),
    ('ll32', 'Llama-3.2-3B-Instruct-Q4_K_M.gguf',     'Llama-3.2-3B',  'Meta'),
    ('ll31', 'Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf', 'Llama-3.1-8B', 'Meta'),
    ('12b',  'gemma-4-12B-it-Q4_K_M.gguf',            'Gemma-4 12B',   'Google'),
    ('qw14', 'Qwen3-14B-Q4_K_M.gguf',                 'Qwen3-14B',     'Alibaba'),
]

# GGUF 값 타입 코드
U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
_FMT = {U8: '<B', I8: '<b', U16: '<H', I16: '<h', U32: '<I', I32: '<i',
        F32: '<f', BOOL: '<?', U64: '<Q', I64: '<q', F64: '<d'}
_SZ = {U8: 1, I8: 1, U16: 2, I16: 2, U32: 4, I32: 4, F32: 4, BOOL: 1,
       U64: 8, I64: 8, F64: 8}


def _read_val(f, t):
    if t == STR:
        n = struct.unpack('<Q', f.read(8))[0]
        return f.read(n).decode('utf-8', 'replace')
    if t == ARR:
        et = struct.unpack('<I', f.read(4))[0]
        n = struct.unpack('<Q', f.read(8))[0]
        if et == STR:
            # 어휘 배열은 수십만 개다. 내용은 버리되 길이만큼 반드시 넘겨야
            # 다음 키의 위치가 맞는다. 중간에 끊으면 파일 위치가 어긋난다.
            for _ in range(n):
                ln = struct.unpack('<Q', f.read(8))[0]
                f.seek(ln, 1)
            return f'<{n:,} strings>'
        if et == ARR:
            raise ValueError('nested arrays are not supported')
        if n <= 512 and et in _FMT:
            # 레이어별로 값이 다른 항목이 있다(Gemma-4 12B 의 KV 헤드 수).
            # 512개 이하면 실제 값을 읽어 둔다.
            return [struct.unpack(_FMT[et], f.read(_SZ[et]))[0] for _ in range(n)]
        f.seek(_SZ.get(et, 4) * n, 1)
        return f'<{n:,} values>'
    return struct.unpack(_FMT[t], f.read(_SZ[t]))[0]


def read_gguf(path, limit=400):
    """헤더의 키-값을 읽어 dict 로 돌려준다. 텐서 데이터는 건드리지 않는다."""
    with open(path, 'rb') as f:
        if f.read(4) != b'GGUF':
            return None
        ver, n_tensor, n_kv = struct.unpack('<IQQ', f.read(20))
        kv = {'_gguf_version': ver, '_n_tensors': n_tensor}
        for _ in range(min(n_kv, limit)):
            kl = struct.unpack('<Q', f.read(8))[0]
            key = f.read(kl).decode('utf-8', 'replace')
            t = struct.unpack('<I', f.read(4))[0]
            try:
                kv[key] = _read_val(f, t)
            except Exception:
                break
        return kv


def _kv_bytes(g, n_layer, n_kvh, n_embd, n_head, ctx=8192):
    """KV 캐시 바이트(f16). 학생 이식판에서 더한 함수.

    원 식은 모든 레이어에 같은 헤드 차원(key_length)을 곱해, 슬라이딩 윈도우 레이어의 작은 헤드
    차원(key_length_swa)을 반영하지 못했다(Gemma-4 12B 적재량이 12.9GB 로 과대 계산된 원인).
    여기서는 레이어마다 KV 헤드 수와 K·V 차원을 따로 곱한다. 슬라이딩 윈도우 레이어도 문맥 전체를
    담는다고 보므로 그런 모델에서는 상한(≤)이다. 반환: (바이트 또는 None, 상한 표시문).
    """
    if not isinstance(n_layer, int) or n_layer <= 0:
        return None, ''
    kvh = n_kvh if isinstance(n_kvh, list) else [n_kvh] * n_layer
    if len(kvh) != n_layer or not all(isinstance(x, int) for x in kvh):
        return None, ''
    k_len = g('attention.key_length') or (n_embd // n_head if isinstance(n_embd, int) and n_head else None)
    v_len = g('attention.value_length') or k_len
    k_swa = g('attention.key_length_swa')
    v_swa = g('attention.value_length_swa') or k_swa
    pat = g('attention.sliding_window_pattern')
    if not isinstance(k_len, int) or not isinstance(v_len, int):
        return None, ''
    total = 0
    for i in range(n_layer):
        swa = isinstance(pat, list) and i < len(pat) and bool(pat[i]) and isinstance(k_swa, int)
        kd, vd = (k_swa, v_swa) if swa else (k_len, v_len)
        total += kvh[i] * (kd + vd) * ctx * 2
    upper = 'upper bound: sliding-window layers counted at the full context' if g('attention.sliding_window') else ''
    return total, upper


def spec_path(path, vendor=''):
    """GGUF 파일 경로 하나로 제원을 읽는다(학생 이식판에서 더한 함수)."""
    global MDIR
    d, fn = os.path.split(os.path.abspath(path))
    kv = read_gguf(path) or {}
    name = kv.get('general.name') or os.path.splitext(fn)[0]
    vendor = vendor or kv.get('general.organization') or ''
    old, MDIR = MDIR, d.replace('\\', '/')
    try:
        return spec('gguf', fn, name, vendor)
    finally:
        MDIR = old


def spec(tag, fn, name, vendor):
    p = f'{MDIR}/{fn}'
    if not os.path.exists(p):
        return None
    kv = read_gguf(p) or {}
    arch = kv.get('general.architecture', '')
    g = lambda k, d=None: kv.get(f'{arch}.{k}', d)
    size = os.path.getsize(p)
    n_layer = g('block_count')
    n_embd = g('embedding_length')
    n_head = g('attention.head_count')
    n_kvh = g('attention.head_count_kv', n_head)
    ctx_train = g('context_length')
    # KV 캐시 = 2(K,V) × 레이어 × KV헤드 × 헤드차원 × 컨텍스트 × 2바이트(f16)
    kvc = None
    kv_note = ''
    if isinstance(n_kvh, list):          # 레이어마다 다른 경우 합으로 계산한다
        kv_note = f'{min(n_kvh)}-{max(n_kvh)} by layer'
        kvh_sum = sum(n_kvh)
    elif isinstance(n_kvh, int) and isinstance(n_layer, int):
        kvh_sum = n_kvh * n_layer
    else:
        kvh_sum = None
    if kvh_sum and isinstance(n_embd, int) and isinstance(n_head, int) and n_head:
        head_dim = g('attention.key_length') or (n_embd // n_head)
        if isinstance(head_dim, int):
            kvc = 2 * kvh_sum * head_dim * 8192 * 2
    # 학생 이식판: 레이어별 헤드 차원(슬라이딩 윈도우 레이어의 key_length_swa)을 반영해 다시 센다
    kv2, kv_upper = _kv_bytes(g, n_layer, n_kvh, n_embd, n_head)
    if kv2:
        kvc = kv2
    return dict(
        tag=tag, name=name, vendor=vendor, file=fn,
        arch=arch,
        params=kv.get('general.size_label') or '',
        quant=fn.split('-')[-1].replace('.gguf', ''),
        n_tensors=kv.get('_n_tensors'),
        n_layer=n_layer, n_embd=n_embd, n_head=n_head,
        n_kv_head=(kv_note or n_kvh),
        ctx_train=ctx_train,
        vocab=kv.get('tokenizer.ggml.model', ''),
        size_gb=round(size / 1000 ** 3, 2),
        kv_cache_gb=(round(kvc / 1000 ** 3, 2) if kvc else None),
        need_gb=(round((size + (kvc or 0)) / 1000 ** 3, 1) if kvc else None),
        kv_note=kv_upper,
    )


def all_specs():
    out = []
    for t, fn, n, v in MODELS:
        s = spec(t, fn, n, v)
        if s:
            out.append(s)
    return out


if __name__ == '__main__':
    # 학생 이식판: GGUF 파일을 인자로 받아 제원만 보인다(파일을 쓰지 않는다).
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    if len(sys.argv) < 2:
        print('사용: python app/model_specs.py <모델.gguf>')
        raise SystemExit(0)
    s = spec_path(sys.argv[1])
    for k, v in (s or {}).items():
        print(f'  {k:<12} {v}')
