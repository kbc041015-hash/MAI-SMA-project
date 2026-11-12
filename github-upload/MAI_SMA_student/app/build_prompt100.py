# -*- coding: utf-8 -*-
# 학생 이식판 — 원본: DemoTool/build_prompt100.py. 교수자용 빌드 스크립트(build_student_package.py — 학생 전달물에는 들어 있지 않다)가 고쳐 만들었다.
#   · 데이터 사전: data/datadict_deid.txt, DDL: 키트 DB 카탈로그(EQUIP_TAG 포함)
#   · RULES: 규칙 3 의 (선박, 날짜) 여섯 짝을 키트의 선박 코드 순열로(eval/prompts/cond_F.txt 와 글자까지 같다)
#   · __main__: 프롬프트 파일을 logs/prompt100/ 에 쓴다
"""조건 A(스키마) / B(+암묵규칙) / C(+퓨샷) 프롬프트 생성.
   DDL은 실제 DuckDB 카탈로그에서 뽑는다 — 선언 타입과 실행 타입이 항상 일치."""
import os as _os0
import sys
# 같은 폴더를 먼저 본다. 예전에는 벤치 폴더의 절대경로를 맨 앞에 끼워 넣었는데,
# 그러면 harness·rag·router 가 벤치 사본으로 적재되고 schema_ddl() 이 실제
# 데이터베이스의 카탈로그를 읽는다. 시연 패키지는 제 폴더 안에서 완결되어야 한다.
sys.path.insert(0, _os0.path.dirname(_os0.path.abspath(__file__)))
from harness import schema_ddl
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


OUT = _HERE
DATADICT = _pick('MAI_SMA_DATADICT', '../data/datadict_deid.txt')   # 학생 이식판
SCHEMA = schema_ddl()

HEAD = """당신은 선박 정비 데이터베이스의 SQL 생성기다. DuckDB SQL 문법을 쓴다.
아래 스키마를 보고 사용자 질문에 답하는 SELECT 문 하나만 출력하라.
설명, 주석, 마크다운 코드펜스 없이 SQL 한 줄만 출력한다. 세미콜론으로 끝낸다.

[SQL 방언 안내 — DuckDB]
- 여러 열의 고유 조합을 셀 때는 반드시 괄호로 묶는다: count(DISTINCT (a, b))
- TIMESTAMP 컬럼에 LIKE를 쓰지 않는다. CAST(x AS DATE) 또는 year(x)를 쓴다.
- 같은 이름의 컬럼이 여러 테이블에 있을 수 있다. 질문이 지정한 테이블을 쓴다.

[스키마]
"""

RULES = """
[이 데이터베이스의 문서화되지 않은 규칙]
1. 조인키는 반드시 (VESSELCODE, DOC_NO) 두 컬럼이다. DOC_NO는 선박마다 독립 채번이라 단독으로 쓰면 6척이 섞인다. 문서를 셀 때는 count(DISTINCT (VESSELCODE, DOC_NO))를 쓴다.
2. master 1행 = 결재문서 1건. pms 1행 = 정비라인 1건.
3. pms.LAST_WORK_DATE에는 시스템 도입 시 일괄 입력된 인공값이 섞여 있다. 인공값은 (선박, 날짜) 짝으로만 정의된다 — 아래 여섯 짝에 정확히 해당할 때만 인공값이다. 다른 선박의 같은 날짜는 실제 정비일이다.
   (VESSELCODE='V03' AND 날짜=2024-02-01), (V01, 2024-06-03), (V02, 2024-07-01),
   (V06, 2024-04-30), (V04, 2024-10-04), (V05, 2025-01-10)
   "실제 기록된" 정비일을 물으면 이 여섯 짝을 각각 (VESSELCODE=... AND CAST(LAST_WORK_DATE AS DATE)=DATE '...') 형태로 묶어 제외한다.
4. 소요시간·투입인원은 두 곳에 있고 둘 다 쓸 수 없다. pms.WORK_HOUR와 pms.MEN은 99%가 0이며, 이는 선박별 PMS 마스터의 공수가 0으로 등록돼 그대로 복사된 결과다. master.WORK_BY_HOUR와 master.WORK_BY_MEN은 100% 채워져 있으나 소요시간의 73%가 값 1.0에 몰려 있고 작업 규모와의 상관이 0.16에 불과해 신뢰할 수 없다. 소요시간·투입인원을 물으면 합산·평균 내지 말고 SQL 대신 정확히 NO_RELIABLE_VALUE 라고만 답한다.
5. master.WORK_BY는 100% 공란이다. 담당자·수행자를 물으면 master.PIC(직책) 컬럼을 쓴다.
6. consumption(소모부품)은 전체 문서의 3.21%에만 있다. 특정 정비에 쓴 부품·수량을 물으면 SQL 대신 정확히 NOT_RECORDED 라고만 답한다. 단 "소모 기록이 있는 문서 수"처럼 기록 자체를 세는 질문은 SQL로 답하되, master.CONS_YN 플래그는 실제 기록과 43건 어긋나므로 consumption 테이블을 직접 세야 한다.
7. 동의어: 발전기 = D/G = DG = G/E = GENERATOR ENGINE(DIESEL). 주기관 = M/E = MAIN ENGINE(DIESEL). 이 값은 pms.MACH_DESC에 들어 있으며 정확히 일치(=)로 비교한다. LIKE '%GENERATOR%'는 N2 GENERATOR, SHAFT GENERATOR 등 다른 기기를 잡으므로 쓰지 않는다.
8. 호기 번호는 MACH_NO다. 호기별 집계는 MACH_NO로 GROUP BY 한다. 값의 범위는 1~9이며 발전기·주기관은 1~3이지만 계류윈치처럼 여러 대가 설치된 기기는 4 이상도 있다. 범위를 조건으로 걸지 않는다.
9. VESSELCODE 컬럼에는 V01, V02, V03, V04, V05, V06 여섯 개의 코드만 들어 있다. 선박의 영문명(VESSEL-A, VESSEL-B, VESSEL-C, VESSEL-D, VESSEL-E, VESSEL-F)은 VESSELCODE가 아니라 vessels.VESSEL_SHORT 컬럼에 있다. 질문이 영문명을 말해도 VESSELCODE에는 코드를 넣는다: VESSEL-A→V01, VESSEL-B→V02, VESSEL-C→V03, VESSEL-D→V04, VESSEL-E→V05, VESSEL-F→V06.
10. 특정 값에 해당하는 행을 물으면 LIKE가 아니라 = 로 비교한다. LIKE는 더 긴 이름(N2 GENERATOR, FUEL OIL V/V 등)까지 잡아 과다 집계된다.
11. 중요 정비 여부는 pms.CRITICAL_YN을 쓴다(Y 10,752 / N 40,963으로 100% 채움). master.CRITICALPMS_YN은 Y와 NULL만 있어 ='N' 조건이 0건을 낸다. 또한 이 값은 문서에 속한 라인들의 OR 집계이고 조회 시점 마스터를 참조하므로, 라인 단위 중요도를 물을 때 쓰면 안 된다.
12. 기기명·장비명으로 정확일치 검색은 pms 테이블에서 한다. master.MACH_DESC는 값 끝에 호기번호가 붙어 있어(예: 'GENERATOR ENGINE(DIESEL) 1') MACH_DESC='GENERATOR ENGINE(DIESEL)' 조건이 master에서는 0건을 낸다. pms.MACH_DESC에는 호기번호가 붙지 않는다.
"""

FEWSHOT = """
[예시]
질문: 정비 문서는 서로 다른 것이 몇 건인가?
SQL: SELECT count(DISTINCT (VESSELCODE, DOC_NO)) AS n FROM master;

질문: 정비 한 건당 평균 소요 시간은?
SQL: NO_RELIABLE_VALUE

질문: 이 정비에 무슨 부품을 썼나?
SQL: NOT_RECORDED

질문: 중요 정비는 몇 건인가?
SQL: SELECT count(*) AS n FROM pms WHERE CRITICAL_YN='Y';

질문: 발전기 정비 라인은 몇 건인가?
SQL: SELECT count(*) AS n FROM pms WHERE MACH_DESC='GENERATOR ENGINE(DIESEL)';

질문: 정비를 담당한 직책별 문서 수는?
SQL: SELECT PIC, count(*) AS n FROM master WHERE PIC IS NOT NULL GROUP BY PIC ORDER BY n DESC;
"""

import os
import re


def schema_with_comments(dictfile=None):
    """조건 F: 컬럼별 한글 설명(데이터 사전)을 DDL 주석으로 붙인다.
       dictfile 을 바꾸면 같은 정보를 다른 문장으로 쓴 판이 된다 — 문안 변동 통제."""
    d = dictfile or DATADICT
    if not os.path.exists(d):
        return None
    desc, tbl = {}, None
    for line in open(d, encoding='utf-8'):
        line = line.rstrip()
        m = re.match(r'^###\s*(\w+)', line)
        if m:
            tbl = m.group(1)
            continue
        if tbl and '|' in line:
            parts = [x.strip() for x in line.split('|')]
            if len(parts) >= 2 and parts[0]:
                syn = parts[2] if len(parts) > 2 else ''
                desc[(tbl, parts[0])] = (parts[1] + (f' / 표현: {syn}' if syn else ''))[:160]
    out = []
    for blk in SCHEMA.split('\n\n'):
        t = re.search(r'CREATE TABLE (\w+)', blk).group(1)
        lines = []
        for ln in blk.split('\n'):
            cm = re.match(r'^  (\w+) ', ln)
            key = (t, cm.group(1)) if cm else None
            lines.append(f'{ln}  -- {desc[key]}' if key in desc else ln)
        out.append('\n'.join(lines))
    return '\n\n'.join(out)


def schema_shuffled(seed=42, dictfile=None):
    """조건 P(위약): 사전 설명을 컬럼끼리 무작위로 뒤섞어 붙인다.
       길이·어휘·토큰수는 F와 사실상 같고 올바른 매핑만 없앤다.
       seed 를 바꾸면 다른 순열 — 위약 효과가 특정 순열의 산물인지 검정한다."""
    import random
    sc = schema_with_comments(dictfile)
    if sc is None:
        return None
    lines = sc.split(chr(10))
    idx = [i for i, ln in enumerate(lines) if "  -- " in ln]
    cms = [lines[i].split("  -- ", 1)[1] for i in idx]
    rnd = random.Random(seed)
    perm = cms[:]
    while True:                      # 제자리에 남는 설명이 없도록
        rnd.shuffle(perm)
        if all(a != b for a, b in zip(cms, perm)):
            break
    for i, c in zip(idx, perm):
        lines[i] = lines[i].split("  -- ", 1)[0] + "  -- " + c
    return chr(10).join(lines)

TAIL = "\n[질문]\n{q}\n\n[SQL]\n"
pa = HEAD + SCHEMA + TAIL
pb = HEAD + SCHEMA + "\n" + RULES + TAIL
pc = HEAD + SCHEMA + "\n" + RULES + FEWSHOT + TAIL
variants = [('A', pa), ('B', pb), ('C', pc)]
SC = schema_with_comments()
if SC:
    variants.append(('F', HEAD + SC + "\n" + RULES + FEWSHOT + TAIL))
    variants.append(('P', HEAD + schema_shuffled(42) + chr(10) + RULES + FEWSHOT + TAIL))
    # ── 문안 변동 통제 ───────────────────────────────────────
    # P2·P3 : 같은 사전, 다른 순열 → 위약 효과가 순열 하나의 산물인지 검정
    for tg, sd in [('P2', 43), ('P3', 44)]:
        variants.append((tg, HEAD + schema_shuffled(sd) + chr(10) + RULES + FEWSHOT + TAIL))
    # F2·F3 : 같은 정보, 다른 문장 → 사전 효과가 문안 하나의 산물인지 검정
    #         각 문안에 짝이 되는 위약(P4·P5)도 함께 만든다
    for tg, fn, pg in [('F2', 'datadict_v2.txt', 'P4'), ('F3', 'datadict_v3.txt', 'P5')]:
        path = f'{OUT}/{fn}'
        if os.path.exists(path):
            variants.append((tg, HEAD + schema_with_comments(path) + chr(10)
                             + RULES + FEWSHOT + TAIL))
            variants.append((pg, HEAD + schema_shuffled(42, path) + chr(10)
                             + RULES + FEWSHOT + TAIL))
else:
    print('  (data/datadict_deid.txt 없음 — 조건 F 생략)')
# import 만으로 프롬프트 파일을 덮어쓰지 않는다.
# 실험에 쓰인 프롬프트는 실험 기록이므로 의도적으로 실행했을 때만 다시 쓴다.
if __name__ == '__main__':
    _d = os.path.join(os.path.dirname(OUT), 'logs', 'prompt100')   # 학생 이식판: app/ 대신 logs/
    os.makedirs(_d, exist_ok=True)
    for name, p in variants:
        open(f'{_d}/prompt100_{name}.txt', 'w', encoding='utf-8').write(p)
        print(f'조건 {name}: {len(p):,}자')
