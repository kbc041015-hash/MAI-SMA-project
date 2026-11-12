# -*- coding: utf-8 -*-
"""템플릿 × 질문 → 완성 프롬프트. 원 실행(run100.py)과 같이 template.format(q=질문) 으로 만든다.

사용 예
  python tool/make_prompts.py prompts/variant_asrun/cond_F.txt NL                 # JSONL 을 표준출력 대신 파일로
  python tool/make_prompts.py prompts/cond_C.txt L1 --dir out_C_L1                # 문항별 txt 100개
  python tool/make_prompts.py prompts/cond_A.txt orig --out A_orig.jsonl
문형: orig(원판) · L2 · L1 · NL(자연어)
"""
import argparse, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('template')
    ap.add_argument('form', choices=['orig', 'L2', 'L1', 'NL'])
    ap.add_argument('--out', default=None, help='JSONL 경로(기본: <템플릿이름>_<문형>.jsonl)')
    ap.add_argument('--dir', default=None, help='문항별 txt 를 쓸 폴더')
    a = ap.parse_args()
    tpath = a.template if os.path.isabs(a.template) else os.path.join(KIT, a.template)
    tmpl = open(tpath, encoding='utf-8').read()
    if tmpl.count('{q}') != 1:
        raise SystemExit('템플릿에 {q} 자리가 정확히 하나 있어야 한다')
    qs = [json.loads(x) for x in open(os.path.join(KIT, 'questions', 'aggregate100.jsonl'), encoding='utf-8') if x.strip()]
    rows = [dict(id=q['id'], form=a.form, template=os.path.basename(tpath), prompt=tmpl.format(q=q['questions'][a.form]))
            for q in qs]
    if a.dir:
        os.makedirs(a.dir, exist_ok=True)
        for r in rows:
            open(os.path.join(a.dir, f"{r['id']}.txt"), 'w', encoding='utf-8', newline='\n').write(r['prompt'])
        print(f'{len(rows)}개 → {a.dir}')
    out = a.out or f"{os.path.splitext(os.path.basename(tpath))[0]}_{a.form}.jsonl"
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'{len(rows)}개 → {out}')


if __name__ == '__main__':
    main()
