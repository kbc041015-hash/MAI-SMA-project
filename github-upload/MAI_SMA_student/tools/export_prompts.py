# -*- coding: utf-8 -*-
"""시스템 프롬프트 원문 내보내기 — app/ 의 코드에서 prompts/01~05 를 다시 쓴다.

prompts/ 의 파일은 사람이 읽으라고 만든 사본이다. 실제로 보내는 글은 app/ 의 코드에 있다.
코드의 프롬프트를 고쳤으면 이 스크립트로 사본을 다시 맞춘다(tools/selftest.py 가 어긋남을 알려 준다).

  python tools/export_prompts.py           prompts/01~05 를 쓴다
  python tools/export_prompts.py --check   지금 파일이 코드와 같은지만 본다(쓰지 않음)
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
OUT = os.path.join(PKG, 'prompts')
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BAR = '=' * 78


def _sec(title):
    return f'{BAR}\n{title}\n{BAR}\n'


def render():
    """{파일 이름: 내용}."""
    os.environ.setdefault('MAINT_DB', os.path.join(PKG, 'data', 'MAI_SMA_학생용DB_1000.duckdb'))
    os.environ.setdefault('MAI_SMA_DATADICT', os.path.join(PKG, 'data', 'datadict_deid.txt'))
    sys.path.insert(0, os.path.join(PKG, 'app'))
    import build_prompt100 as BP
    import maintqa_web as W
    import rag
    import maintqa_cli as CLI
    import term_map

    files = {}
    # 01 — 실제로 보내는 전문. 질문 자리 {q} 만 남긴다. 머리말을 붙이지 않는다(글자까지 같아야 한다).
    files['01_집계형_SQL생성_조건F.txt'] = BP.HEAD + BP.SC + '\n' + BP.RULES + BP.FEWSHOT + BP.TAIL

    ph = dict(ctx='{ctx}', q='{q}', naming='{naming}')
    ex_q = '발전기 과급기 물세척할 때 엔진 부하는 얼마로 두나?'
    files['02_절차형_답변.txt'] = (
        '절차형 답변 프롬프트 — 검색된 정비기록 4건 + 질문 → JSON(steps · missing)\n'
        '사람이 읽는 사본이다. 실제로 보내는 글은 ===== 줄 사이의 본문이고, {naming} {ctx} {q} 자리에\n'
        '맨 아래 "자리 설명"의 값이 들어간다. 답의 quote 는 인용 검증(원문 문자 대조)의 대상이다.\n\n'
        + _sec('[A] 웹 화면 기본(영어 단계) — app/maintqa_web.py ANSWER_EN · max_tokens 1600')
        + W.ANSWER_EN.format(**ph) + '\n'
        + _sec('[B] 웹 화면 "Korean alongside" 를 켰을 때(영·한 병기) — ANSWER_BI · max_tokens 2600')
        + W.ANSWER_BI.format(**ph) + '\n'
        + _sec('[C] 평가기·터미널판(한국어 단계) — app/rag.py ANSWER = eval/prompts/proc_answer_template.txt · '
               'max_tokens 2200')
        + rag.ANSWER.format(ctx='{ctx}', q='{q}') + '\n'
        + _sec('자리 설명')
        + '{q}      영어 질문. 한국어로 물으면 03_질문번역.txt 의 번역 결과가 들어간다.\n\n'
        + '{ctx}    BM25 로 찾은 기록 4건. 웹 화면([A][B])은 다음 형식이다.\n'
        + '         Record 1. Title: <제목>\\nMachine: <기기명> / <장비명>\\nDate: <작업일>\\n<본문>\n'
        + '         평가기·터미널판([C])은 원 실험(proc_run.py) 형식이다.\n'
        + '         Record 1. Title: <제목>\\nMachine: <기기명> / Equipment: <장비명> / Date: <작업일>\\n'
        + 'Content: <본문>\n'
        + '         기록 사이는 빈 줄 하나다.\n\n'
        + '{naming} 질문에 나온 설비 이름을 기록이 어떻게 적는지 알려 주는 문단(웹 화면만). 해당 없으면 빈 줄.\n'
        + f'         예(질문: "{ex_q}")\n'
        + '\n'.join('         ' + ln for ln in W.naming_note(term_map.hints(ex_q)).strip('\n').split('\n')) + '\n')

    files['03_질문번역.txt'] = (
        '질문 번역 프롬프트 — 절차형 한국어 질문을 영어로(기록 본문과 검색 색인이 영어라서)\n'
        '영어로 물으면 이 단계를 건너뛴다. 사람이 읽는 사본이며 {q} 에 질문, {hints} 에 용어 대응이 들어간다.\n\n'
        + _sec('[A] 웹 화면 — app/maintqa_web.py TRANS_D · max_tokens 200 (용어 대응표 힌트를 함께 준다)')
        + W.TRANS_D + '\n'
        + _sec('{hints} 예시 — app/term_map.py hint_block()')
        + f'질문: {ex_q}\n' + (term_map.hint_block(ex_q) or '(해당 없음)\n')
        + '\n번역 결과에 대응표의 기록 표기를 덧붙여(snap) 검색어로 쓴다. 대응표는 82항목이며 질문에 나온 말만 넣는다.\n\n'
        + _sec('[B] 터미널판 — app/maintqa_cli.py TRANS_P · max_tokens 200')
        + CLI.TRANS_P + '\n\n'
        + _sec('[C] app/rag.py 단독 실행 — TRANS · max_tokens 120')
        + rag.TRANS)

    files['04_후속질문_재작성.txt'] = (
        '후속 질문 재작성 프롬프트 — "그건 몇 건이야?" 같은 질문을 앞 대화 없이도 통하는 질문으로 다시 쓴다\n'
        '대화 이력은 이 재작성에만 쓰고 답변 생성 프롬프트에는 넣지 않는다(인용 검증 대상이 흐려지지 않게).\n'
        '다시 쓴 뒤로는 라우터 → 집계형/절차형 경로가 그대로 돈다.\n\n'
        + '언제 부르나: 직전 대화가 있고, 질문에 지시어가 있거나(아래 FOLLOWUP 정규식) 8자 이하일 때만.\n'
        + 'FOLLOWUP = ' + W.FOLLOWUP.pattern + '\n'
        + '{h} = 직전 최대 3턴. 턴마다 "Q: <질문>\\nA: <답 요약 200자>" 를 빈 줄로 잇는다. {q} = 새 질문.\n\n'
        + _sec('[A] 한국어 질문 — app/maintqa_web.py REWRITE_KO · max_tokens 160')
        + W.REWRITE_KO + '\n\n'
        + _sec('[B] 영어 질문 — REWRITE_EN · max_tokens 160')
        + W.REWRITE_EN + '\n')

    files['05_병기번역.txt'] = (
        '병기 번역 프롬프트 — 화면 상단 "Korean alongside" 를 켰을 때 질문을 두 언어로 보이려고 쓴다\n'
        '화면 표시용이다. 검색·SQL 생성에는 들어가지 않는다. 절차형 한국어 질문은 03 번역 결과를 다시 쓰고\n'
        '새로 부르지 않는다. 집계형 답 문장의 한국어판은 모델 없이 app/answer_text.py 가 만든다.\n\n'
        + _sec('[A] 영어 질문 → 한국어 — app/maintqa_web.py TRANS_KO · max_tokens 200')
        + W.TRANS_KO + '\n\n'
        + _sec('[B] 한국어 질문(집계형) → 영어 — TRANS · max_tokens 200')
        + W.TRANS + '\n')
    return files


def main():
    files = render()
    check = '--check' in sys.argv
    bad = []
    for name, text in files.items():
        p = os.path.join(OUT, name)
        if check:
            try:
                cur = open(p, encoding='utf-8').read()
            except OSError:
                cur = None
            if cur != text:
                bad.append(name)
        else:
            os.makedirs(OUT, exist_ok=True)
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
    if check:
        print('프롬프트 사본: ' + ('코드와 같음' if not bad else '다름 — ' + ', '.join(bad)))
        return 0 if not bad else 1
    print(f'프롬프트 원문 {len(files)}개를 prompts/ 에 썼다')
    return 0


if __name__ == '__main__':
    sys.exit(main())
