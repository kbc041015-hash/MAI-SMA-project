# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/sel_dict.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · 데이터 사전: data/datadict_deid.txt(비식별판)
#   · __main__: 벤치 문항 대신 질문 하나로 시연
"""조건 S — 선택적 데이터 사전.

   조건 F는 121개 컬럼 설명을 질문과 무관하게 매번 통째로 넣어 +4,400토큰을 쓴다.
   위약 대조에서 그 길이가 −5.8~−14.4%p 의 열화를 일으키는 것이 확인됐다.
   여기서는 질문과 관련된 컬럼 설명만 BM25로 골라 붙인다.

   스키마 DDL 자체는 전체를 유지하고 '주석'만 선택적으로 단다 —
   모델이 사전에 안 뽑힌 컬럼도 여전히 참조할 수 있어야 하기 때문.

   한국어 질의라 어절 토큰만으로는 매칭이 안 된다. 한글 2-gram + 라틴 단어를 함께 쓴다.
"""
import re, math
from collections import Counter

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
DATADICT = _pick('MAI_SMA_DATADICT', '../data/datadict_deid.txt')   # 학생 이식판
TOPK = 20          # 붙일 컬럼 설명 수
MIN_SCORE = 0.5    # 이 미만이면 관련 없다고 보고 버린다


def toks(s):
    """한글은 2-gram, 영문·숫자는 단어 단위."""
    s = (s or '').lower()
    out = re.findall(r'[a-z][a-z0-9_/]*|\d+', s)
    ko = re.sub(r'[^가-힣]', ' ', s)
    for w in ko.split():
        out += [w[i:i + 2] for i in range(len(w) - 1)] or [w]
    return out


def load_entries(path=None):
    """(테이블, 컬럼, 설명, 동의어) 목록. 학생 이식판: 기본은 비식별 데이터 사전."""
    path = path or DATADICT
    ent, tbl = [], None
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip()
        m = re.match(r'^###\s*(\w+)', ln)
        if m:
            tbl = m.group(1)
            continue
        if tbl and '|' in ln:
            p = [x.strip() for x in ln.split('|')]
            if len(p) >= 2 and p[0]:
                ent.append((tbl, p[0], p[1], p[2] if len(p) > 2 else ''))
    return ent


class DictIndex:
    def __init__(self, entries):
        self.ent = entries
        # 검색 대상 문자열: 컬럼명 + 설명 + 동의어. 동의어를 2배 실어 매칭력을 높인다.
        self.tf = []
        df = Counter()
        for t, c, d, syn in entries:
            tk = toks(c) + toks(d) + toks(syn) * 2
            f = Counter(tk)
            self.tf.append((f, len(tk)))
            df.update(f.keys())
        n = len(entries)
        self.idf = {w: math.log(1 + (n - v + 0.5) / (v + 0.5)) for w, v in df.items()}
        self.avg = sum(l for _, l in self.tf) / n

    def search(self, q, k=TOPK, k1=1.5, b=0.75):
        qs = toks(q)
        sc = []
        for i, (f, ln) in enumerate(self.tf):
            s = 0.0
            for w in qs:
                c = f.get(w)
                if not c:
                    continue
                s += self.idf.get(w, 0) * c * (k1 + 1) / (c + k1 * (1 - b + b * ln / self.avg))
            if s > MIN_SCORE:
                sc.append((s, i))
        sc.sort(key=lambda x: -x[0])
        return [(s, self.ent[i]) for s, i in sc[:k]]


_IDX = None


def index():
    global _IDX
    if _IDX is None:
        _IDX = DictIndex(load_entries())
    return _IDX


def schema_selective(schema_ddl, question, k=TOPK):
    """DDL 전체를 유지하되, 질문과 관련된 컬럼에만 주석을 단다."""
    sel = {}
    for s, (t, c, d, syn) in index().search(question, k):
        sel[(t, c)] = (d + (f' / 표현: {syn}' if syn else ''))[:160]
    out = []
    for blk in schema_ddl.split('\n\n'):
        m = re.search(r'CREATE TABLE (\w+)', blk)
        if not m:
            out.append(blk)
            continue
        t = m.group(1)
        lines = []
        for ln in blk.split('\n'):
            cm = re.match(r'^  (\w+) ', ln)
            key = (t, cm.group(1)) if cm else None
            lines.append(f'{ln}  -- {sel[key]}' if key in sel else ln)
        out.append('\n'.join(lines))
    return '\n\n'.join(out), len(sel)


if __name__ == '__main__':
    # 학생 이식판: 원 시연판의 검사 문항(벤치 폴더)은 없으므로, 질문 하나를 받아 고른 컬럼을 보인다.
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    q = ' '.join(sys.argv[1:]) or '발전기 정비 라인을 호기별로 세어줘'
    print(f'사전 항목 {len(load_entries())}개 · TOPK={TOPK}')
    print(f'질문: {q}')
    for s_, (t, c, d, syn) in index().search(q):
        print(f'  {s_:6.2f}  {t}.{c:<18} {d[:60]}')
