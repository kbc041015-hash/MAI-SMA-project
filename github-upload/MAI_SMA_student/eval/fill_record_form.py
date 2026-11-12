# -*- coding: utf-8 -*-
"""평가 결과를 엑셀 기록양식(기록양식_200문항.xlsx 의 ⑥ChatGPT_기록양식 시트)에 옮겨 적는다.

  python eval/fill_record_form.py eval/results/agg_F_NL_kit_<모델>.csv eval/results/proc_en_<모델>.csv
  → eval/results/기록양식_<모델>_<날짜>.xlsx   (원본 엑셀은 그대로 두고 사본에 적는다)

  --out 파일.xlsx      저장할 곳
  --form 엑셀.xlsx     바탕이 될 기록양식(기본: eval/기록양식_200문항.xlsx — 이미 채운 사본을 줘도 된다)

run_eval.py 의 결과 CSV 앞 11열(모델명 · 버전 · 시험일 · 문항ID · 조건 · 문형 · 응답 원문 · 추출 SQL ·
실행 결과 · 정답 여부 · 비고)은 ⑥ 시트와 같다. (문항ID · 조건 · 문형)이 같고 아직 비어 있는 행을 채우고,
이미 다른 모델로 채워진 행이거나 없는 조합(예: 조건 C)이면 시트 끝에 행을 더한다.
필요: openpyxl (python -m pip install openpyxl)
"""
import csv
import datetime
import os
import re
import sys

sys.dont_write_bytecode = True
EVAL = os.path.dirname(os.path.abspath(__file__))
SHEET = '⑥ChatGPT_기록양식'
COLS = ['모델명', '버전', '시험일', '문항ID', '조건', '문형', '응답 원문', '추출 SQL', '실행 결과', '정답 여부', '비고']
CELL_MAX = 32000
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def read_rows(path):
    """run_eval.py 결과 CSV(UTF-8 BOM) → ⑥ 열 순서의 값 목록."""
    with open(path, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        head = next(rd, [])
        idx = {h: i for i, h in enumerate(head)}
        miss = [c for c in COLS if c not in idx]
        if miss:
            raise SystemExit(f'{path}: run_eval.py 결과 CSV 가 아닌 것 같다(없는 열: {miss})')
        out = []
        for row in rd:
            vals = []
            for c in COLS:
                v = row[idx[c]] if idx[c] < len(row) else ''
                # run_eval.py 는 엑셀이 수식으로 읽지 않게 = + - @ 앞에 ' 를 붙여 두었다. 엑셀에 직접 쓸 때는 뗀다.
                if v[:1] == "'" and v[1:2] in ('=', '+', '-', '@'):
                    v = v[1:]
                vals.append(v)
            if vals[3]:
                out.append(vals)
        return out


def put(cell, v):
    if isinstance(v, str) and len(v) > CELL_MAX:
        v = v[:CELL_MAX] + ' …(잘림: 원문은 결과 CSV·JSONL 에 있다)'
    cell.value = v if v != '' else None
    if isinstance(v, str) and v[:1] in ('=', '+', '-', '@'):
        cell.data_type = 's'          # 수식으로 읽히지 않게 글자로 둔다


def main():
    a = sys.argv[1:]
    out_path, form = None, os.path.join(EVAL, '기록양식_200문항.xlsx')
    if '--out' in a:
        i = a.index('--out')
        out_path = a[i + 1]
        del a[i:i + 2]
    if '--form' in a:
        i = a.index('--form')
        form = a[i + 1]
        del a[i:i + 2]
    if not a:
        print(__doc__)
        return 2
    try:
        import openpyxl
    except ImportError:
        print('openpyxl 이 없습니다. 먼저 설치하십시오:  python -m pip install openpyxl')
        return 2
    rows = []
    for p in a:
        rows += read_rows(p)
    if not rows:
        print('옮겨 적을 결과가 없습니다.')
        return 1
    wb = openpyxl.load_workbook(form)
    ws = wb[SHEET]
    slot = {}
    for r in range(2, ws.max_row + 1):
        key = tuple(str(ws.cell(r, c).value or '').strip() for c in (4, 5, 6))
        if key[0] and not (ws.cell(r, 1).value or ws.cell(r, 7).value):
            slot.setdefault(key, r)
    filled = added = 0
    for vals in rows:
        key = (vals[3], vals[4], vals[5])
        r = slot.pop(key, None)
        if r is None:
            r = ws.max_row + 1
            added += 1
        else:
            filled += 1
        for c, v in enumerate(vals, 1):
            put(ws.cell(r, c), v)
    # 파일 이름의 모델: 모델명 칸. 모델명이 없거나 '(로컬)'(config.json 의 model 을 비운 llama.cpp 등)이면 버전 칸
    # (실행기가 돌려준 모델 파일 이름)을 쓴다.
    model = rows[0][0] or ''
    if not model or model == '(로컬)':
        model = rows[0][1] or model
    model = re.sub(r'\.gguf$', '', model, flags=re.I)
    model = re.sub(r'[^\w.-]+', '_', model).strip('_')[:40] or 'model'
    out_path = out_path or os.path.join(EVAL, 'results', f'기록양식_{model}_{datetime.date.today():%Y%m%d}.xlsx')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
    print(f'기록양식에 {filled}행을 채우고 {added}행을 더했다 → {os.path.relpath(out_path, os.path.dirname(EVAL))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
