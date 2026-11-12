# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/harness.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · DB 경로: 실제·시연 DB 대신 data/MAI_SMA_학생용DB_1000.duckdb(학생용 DB, 정비 문서 1,000건)
#   · get_con(): 외부 파일·네트워크 접근 차단 + 설정 잠금(run_eval.py 와 같은 방식)
#   · TABLES(모델에게 보이는 6개 테이블)는 바꾸지 않았다 — qa_* 표는 프롬프트 DDL 에 없다
import duckdb, re
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


DB = _pick('MAINT_DB', '../data/MAI_SMA_학생용DB_1000.duckdb')   # 학생 이식판: 학생용 DB(정비 문서 1,000건)만 연다
TABLES = ['master', 'pms', 'consumption', 'attachments', 'approval', 'vessels']
ABSTAIN = ("NOT_RECORDED", "NO_RELIABLE_VALUE")


def get_con():
    """물리 테이블로 적재된 DuckDB 파일에 읽기 전용 연결.

    학생 이식판: 모델이 쓴 SQL 을 이 PC 에서 그대로 실행하므로 비식별 키트 평가기
    (eval/run_eval.py open_kit_db)와 같이 외부 파일·네트워크 접근을 막고 설정을 잠근다.
    한 프로세스 안에서는 설정이 같은 연결만 함께 열 수 있으므로 DB 는 늘 이 함수로 연다.
    """
    try:
        con = duckdb.connect(DB, read_only=True, config={'enable_external_access': False})
    except Exception:
        con = duckdb.connect(DB, read_only=True)
    try:
        con.execute('SET lock_configuration = true')
    except Exception:
        pass            # 같은 프로세스에서 이미 잠갔다
    return con


def schema_ddl():
    """모델에게 줄 DDL을 실제 DB 카탈로그에서 생성 — 선언과 실행 환경이 항상 일치."""
    con = get_con()
    out = []
    for t in TABLES:
        cols = con.sql(f'DESCRIBE {t}').fetchall()
        body = ',\n'.join(f'  {c} {ty}' for c, ty, *_ in cols)
        out.append(f'CREATE TABLE {t} (\n{body}\n);')
    con.close()
    return '\n\n'.join(out)


def run(con, sql, limit=2000):
    try:
        r = con.sql(sql).fetchall()
        return r[:limit], None
    except Exception as e:
        return None, str(e).split('\n')[0][:160]


def _norm_val(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    s = str(v)
    # 날짜 정규화: '2026-09-01 00:00:00' → '2026-09-01'
    m = re.match(r'^(\d{4}-\d{2}-\d{2})[ T]00:00:00(?:\.0+)?$', s)
    return m.group(1) if m else s.strip()


def norm(rows):
    if rows is None:
        return None
    # 정렬 키를 문자열로 통일 — float/str 혼합 튜플 비교 오류 방지. 동치 판정은 값 자체로 한다.
    return sorted((tuple(_norm_val(v) for v in r) for r in rows),
                  key=lambda t: tuple(str(x) for x in t))


def repair_dialect(sql):
    """MySQL count(DISTINCT a, b) → DuckDB count(DISTINCT (a, b)). 양 조건 동일 적용."""
    def f(m):
        inner = m.group(1)
        if ',' in inner and not inner.strip().startswith('('):
            return f'count(DISTINCT ({inner}))'
        return m.group(0)
    return re.sub(r'count\s*\(\s*DISTINCT\s+([^()]+?)\s*\)', f, sql, flags=re.I)


def is_abstain(text):
    t = text.strip().strip('`').strip().rstrip(';').strip().upper()
    return t in ABSTAIN


def compare(gold, pred):
    """gold: 결과셋 또는 기권토큰 문자열. pred: 결과셋 또는 None(오류) 또는 기권토큰 문자열."""
    if isinstance(gold, str) and gold in ABSTAIN:
        return 'match' if (isinstance(pred, str) and pred in ABSTAIN) else ('mismatch' if pred is not None else 'error')
    if isinstance(pred, str):
        return 'mismatch'          # 정답은 결과셋인데 모델이 기권 → 과잉기권
    if pred is None:
        return 'error'
    if norm(gold) == norm(pred):
        return 'match'
    # 스칼라 정답 vs GROUP BY 키 동반: 값 다중집합 비교
    gs = sorted(tuple(sorted(map(str, r))) for r in gold)
    ps = sorted(tuple(sorted(map(str, r))) for r in pred)
    if gs == ps:
        return 'match'
    # 스칼라 정답 1개가 pred의 어느 셀에 그대로 있으면 정답 (예: (5791,) vs ('V05', 5791))
    if len(gold) == 1 and len(gold[0]) == 1 and len(pred) == 1:
        g = _norm_val(gold[0][0])
        if any(_norm_val(v) == g for v in pred[0]):
            return 'match'
    return 'mismatch'
