# -*- coding: utf-8 -*-
"""채점기(엑셀 출력판, 학생판) — 채점은 run_eval.py 의 함수를 그대로 쓴다(원 벤치마크와 같은 규칙).

사용(eval 폴더에서. 패키지 폴더에서는 python -B eval/tool/score_kit.py eval/기록양식_200문항.xlsx)
  python -B tool/score_kit.py                         # 기록양식_200문항.xlsx 의 ⑥ChatGPT_기록양식 을 채점
  python -B tool/score_kit.py 응답.xlsx               # 같은 열 구성(⑥ 시트 또는 첫 시트)
  python -B tool/score_kit.py 응답.csv | 응답.jsonl   # 열: 모델명,버전,시험일,문항ID,조건,문형,응답 원문(,비고,회차)
결과
  <입력이름>_채점.xlsx  — 추출 SQL·실행 결과·정답 여부를 채운 기록표 + 요약 시트
  (CSV·JSONL·요약 txt 가 필요하면 python run_eval.py --score-file 을 쓴다. 판정은 같다)

판정 규칙(run_eval.py 와 같다)
  집계형(Q001~Q100)  코드펜스 제거 → 기권 토큰 완전일치 → 첫 SELECT/WITH 부터 첫 ; 까지 → 평가 DB(db/MAI_SMA_학생용DB_1000.duckdb — 읽기 전용·외부 파일
                     접근 차단·시간 제한)에서 실행 → harness.compare. 정답 여부 O/X/E(엄격), 방언교정 판정은 비고에.
  절차형(P001~P100)  JSON 파싱 → steps 마다 exact·anywhere·수치 근거 → 분류 steps/abstain/gen_fail. 정답 여부 '인용정확 k/n'.
  null·숫자·목록 같은 이상 입력도 멈추지 않고 채점한다. 붙여넣기가 아래 행으로 퍼진 것으로 보이는 행은 비고에 경고를 단다.
"""
import sys

sys.dont_write_bytecode = True   # tool/ · 키트 폴더에 __pycache__ 를 남기지 않는다(.pyc 에는 절대경로가 들어간다)

import os  # noqa: E402
import re  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, KIT)
import run_eval as RE  # noqa: E402

COLS = RE.COLS6


def score_rows(rows, kit=None, task=None):
    """read_response_table 형식의 행 → (기록표 행 목록, 채점 기록 목록, 건너뛴 행). 행마다 따로 감싸 한 행 때문에 멈추지 않는다."""
    need_db = any(str(RE.norm_qid(d.get('qid'), task) or '').startswith('Q') for d in rows)
    if kit is None and need_db:
        kit = RE.Kit(RE.DEFAULT_DB)
    recs_all, skipped, by_row = [], [], {}
    last = None
    for d in rows:
        # 모델명·버전·시험일이 모두 빈 행은 바로 위 행 값을 이어받는다(run_eval.score_table 과 같은 규칙)
        meta = (RE._txt(d.get('model')), RE._txt(d.get('version')), RE._txt(d.get('date'))[:10])
        filled = False
        if any(meta):
            last = meta
        elif last:
            d = dict(d, model=last[0], version=last[1], date=last[2])
            filled = True
        try:
            r1, s1 = RE.score_table([d], kit, task)
        except Exception as e:       # 원 규칙 밖의 예외가 나도 다른 행은 계속 채점한다
            skipped.append((d.get('_row'), d.get('qid'), f'채점 중 오류: {type(e).__name__}: {str(e)[:120]}'))
            continue
        for r in r1:
            r['meta_filled'] = filled
            by_row[r['source_row']] = r
        recs_all += r1
        skipped += s1
    out_rows = []
    for d in rows:
        r = by_row.get(d.get('_row'))
        if r is None:
            out_rows.append([d.get('model'), d.get('version'), d.get('date'), d.get('qid'), d.get('cond'), d.get('phrasing'),
                             d.get('response'), None, None, None, d.get('note'), d.get('run')])
        else:
            out_rows.append(RE.csv_row(r)[:len(COLS)])
    return out_rows, recs_all, skipped


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else RE.F_XLSX
    if not os.path.exists(src):
        raise SystemExit(f'파일이 없다: {src}')
    try:
        from openpyxl import Workbook
    except ImportError:
        raise SystemExit('엑셀 출력에는 openpyxl 이 필요하다(pip install openpyxl). CSV 결과는 python run_eval.py --score-file 로 얻는다.')
    rows = RE.read_response_table(src)
    out_rows, recs, skipped = score_rows(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = '채점결과'
    ws.append(COLS)
    for rr in out_rows:
        ws.append([RE._unescape(v) if isinstance(v, str) else v for v in rr])
    ws.freeze_panes = 'A2'
    ws2 = wb.create_sheet('요약')
    text, _ = RE.summarize_scored(recs, skipped)
    for line in text.split('\n'):
        ws2.append([line])
    ws2.column_dimensions['A'].width = 160
    out = re.sub(r'\.(xlsx|xlsm|csv|jsonl)$', '', src, flags=re.I) + '_채점.xlsx'
    wb.save(out)
    print(text)
    print(f'\n채점 완료: {len(recs)}건 → {out}' + (f' · 건너뛴 행 {len(skipped)}' if skipped else ''))


if __name__ == '__main__':
    main()
