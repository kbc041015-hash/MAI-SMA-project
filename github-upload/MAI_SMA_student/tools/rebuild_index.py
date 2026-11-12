# -*- coding: utf-8 -*-
"""검색 색인(BM25) 다시 만들기 — data/MAI_SMA_학생용DB_1000.duckdb → data/bm25_1000.pkl

절차형 경로는 기록 본문을 BM25 로 찾는다. 색인에는 두 종류의 기록이 들어 있다.
  ① 정비 문서 기록   master 에서 서술 300자 이상(length(WORK_REMARK) >= 300) — 원 시스템과 같은 대상
  ② 참고 기록        qa_proc_context(절차형 100문항의 고정 검색 문맥 400건, 실제 정비기록 원문을 가명처리한 것).
                     본문이 같은 기록은 하나만 넣는다. 색인 안에서 선박코드 자리는 QA_REF, 문서번호 자리는
                     '<문항ID>-<기록번호>'(예: P063-1)다. 화면의 출처 표는 이 기록을 qa_proc_context 에서 찾아 보인다.
만드는 방식은 원 시스템(rag.py · make_demo_db.py)과 같다.
  낱말   영문 소문자·숫자 토큰, 불용어·두 글자 이하 제외(rag.tok)
  가중   제목 3배 + 본문 + 기기명 + 장비명
  점수   BM25 (k1 = 1.5, b = 0.75)

사용(패키지 폴더에서)
  python tools/rebuild_index.py            data/bm25_1000.pkl 을 새로 만든다(몇 초)
  python tools/rebuild_index.py --check    지금 파일과 새로 만든 것이 같은지만 본다(파일을 바꾸지 않음)
색인 파일이 없으면 웹 화면(app/maintqa_web.py)이 기동할 때 스스로 만든다.
"""
import os
import pickle
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, 'app'))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DB_NAME = 'MAI_SMA_학생용DB_1000.duckdb'
INDEX_NAME = 'bm25_1000.pkl'


def same_index(a, b):
    """두 색인이 내용으로 같은가(파이썬 판에 따라 pickle 바이트는 달라도 된다)."""
    if a['N'] != b['N'] or a['idf'] != b['idf'] or abs(a['avg'] - b['avg']) > 1e-9:
        return False
    return all(x['meta'] == y['meta'] and x['tf'] == y['tf'] and x['ln'] == y['ln']
               for x, y in zip(a['docs'], b['docs']))


def counts(idx):
    n_ref = sum(1 for d in idx['docs'] if d['meta'][0] == 'QA_REF')
    return f"기록 {idx['N']:,}건(정비 문서 {idx['N'] - n_ref:,} + 참고 {n_ref:,}) · 어휘 {len(idx['idf']):,}종"


def main():
    try:
        import duckdb  # noqa: F401
    except ImportError:
        print('duckdb 가 없습니다. 먼저 설치하십시오:  python -m pip install duckdb')
        return 2
    db = os.path.join(PKG, 'data', DB_NAME)
    out = os.path.join(PKG, 'data', INDEX_NAME)
    os.environ['MAINT_DB'] = db
    os.environ['BM25_IDX'] = out
    import rag
    if not os.path.exists(db):
        print(f'DB 파일이 없습니다: {os.path.relpath(db, PKG)}')
        return 2
    t = time.time()
    if '--check' in sys.argv:
        if not os.path.exists(out):
            print('색인 파일이 없습니다. --check 없이 실행해 만드십시오.')
            return 1
        # 임시 폴더째 지운다(시스템 임시 폴더에 빈 폴더를 남기지 않는다)
        with tempfile.TemporaryDirectory(prefix='bm25_') as d:
            new = rag.build_index(db, os.path.join(d, 'bm25.pkl'))
        with open(out, 'rb') as f:
            cur = pickle.load(f)
        ok = same_index(cur, new)
        print(f"색인 점검: {'같음' if ok else '다름'} — {counts(new)} · {time.time() - t:.1f}초")
        return 0 if ok else 1
    idx = rag.build_index(db, out)
    print(f"색인 생성: data/{INDEX_NAME} — {counts(idx)} · {os.path.getsize(out) / 1048576:.1f} MB · {time.time() - t:.1f}초")
    return 0


if __name__ == '__main__':
    sys.exit(main())
