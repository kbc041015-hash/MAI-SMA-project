# -*- coding: utf-8 -*-
"""LLM 연결 점검 — config.json 에 적은 실행기·모델이 이 시스템을 돌릴 수 있는지 본다.

  python tools/check_llm.py            전체 점검. 실제로 LLM 을 다섯 번 부른다(느린 PC 에서는 몇 분)
  python tools/check_llm.py --quick    설정 + 주소 응답 + 모델 목록만(LLM 호출 없음, START.bat 이 쓴다)
  python tools/check_llm.py --config 다른설정.json
  python tools/check_llm.py --print-port   설정의 화면 포트만 출력(START.bat · start.sh 가 쓴다)
  python tools/check_llm.py --check-duckdb duckdb 판만 본다(1.5 이상 2 미만이면 0, 없으면 20, 범위 밖이면 21 — START.bat 이 쓴다)

전체 점검 항목
  1 설정        config.json 문법 · preset · 주소 · 모델 이름 · 실제로 보낼 요청 필드
  2 주소 응답   GET {base_url}/models 와 모델 목록에 설정의 모델이 있는가
  3 짧은 대화   "OK" 한 마디. 응답 시간과 사고(thinking) 출력 여부
  4 문맥 길이   집계형 프롬프트만 한 긴 글의 맨 앞 낱말을 기억하는가(실행기가 앞을 잘라 버리는지)
  5 집계형 SQL  조건 F 전문으로 "선박은 몇 척인가?" → SQL 실행 → 6
  6 기권        "발전기 정비에 투입된 인원은 평균 몇 명인가?" → NO_RELIABLE_VALUE
  7 절차형 JSON 기록 1건 + 질문 → JSON 단계 · 인용이 원문에 그대로 있는가
결과는 logs/check_llm_report.txt 에도 남는다(--no-report 로 끔). 종료 코드 0 = 이대로 써도 된다.
--quick 의 종료 코드: 0 정상 · 10 설정 오류 · 11 주소 무응답 · 12 모델 없음 · 13 무응답이지만 직접 띄울 설정 있음
"""
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, 'app'))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import sma_config as CONF  # noqa: E402
import llm_client as LLM  # noqa: E402

LINES = []


def out(s=''):
    print(s, flush=True)
    LINES.append(s)


def mark(ok, name, detail=''):
    tag = {True: 'PASS', False: 'FAIL', None: 'WARN'}[ok]
    out(f'  [{tag}] {name}' + (f' — {detail}' if detail else ''))
    return ok


def quick(cfg):
    cli = LLM.Client(cfg)
    ok, ids, err = cli.list_models(timeout=5)
    if not ok:
        out(f'  [!] LLM 주소가 응답하지 않습니다: {cfg["base_url"]} ({err[:100]})')
        if LLM.embedded_ready(cfg):
            out('      config.json 의 llama_server 설정이 있어 웹 서버가 llama-server 를 직접 띄웁니다.')
            return 13
        out('      ' + LLM.start_hint(cfg['preset']))
        return 11
    hit = LLM.model_listed(cfg['model'], ids)
    shown = ', '.join(ids[:6]) + (' …' if len(ids) > 6 else '')
    if hit is False and cfg['preset'] in ('ollama', 'lmstudio'):
        out(f'  [!] 모델 "{cfg["model"]}" 이(가) 목록에 없습니다. 목록: {shown or "(비어 있음)"}')
        out('      ' + LLM.model_hint(cfg['preset'], cfg['model']))
        return 12
    note = '' if hit is not False else f' (주의: 모델 "{cfg["model"]}" 이 목록에 없음)'
    out(f'  LLM 응답 확인 — {CONF.summary(cfg)} · 모델 {len(ids)}개{note}')
    return 0


def full(cfg):
    import build_prompt100 as BP
    import harness
    from maintqa_web import ANSWER_EN, clean_sql, parse_json, ABSTAIN
    from rag import sq
    cli = LLM.Client(cfg)
    probs = []
    out('\n1. 설정')
    mark(True, CONF.summary(cfg), f'설정 파일 {os.path.basename(cfg["_path"])}')
    out(f'      보낼 요청 필드: {json.dumps(cli.request_summary(), ensure_ascii=False)}')
    for w in cfg.get('_warnings') or []:
        mark(None, w)
    out('\n2. 주소 응답')
    ok, ids, err = cli.list_models(timeout=8)
    if not mark(ok, f'GET {cfg["base_url"]}/models', err[:120] if not ok else f'모델 {len(ids)}개'):
        out('      ' + LLM.start_hint(cfg['preset']))
        return ['LLM 주소가 응답하지 않는다']
    hit = LLM.model_listed(cfg['model'], ids)
    if hit is None:
        mark(None, '모델 이름을 적지 않았다(서버 기본 모델을 쓴다)', ', '.join(ids[:4]))
    elif not mark(hit or None, f'모델 "{cfg["model"]}" 이 목록에 있음', ', '.join(ids[:6])):
        out('      ' + LLM.model_hint(cfg['preset'], cfg['model']))
        if cfg['preset'] in ('ollama', 'lmstudio'):
            return ['모델 이름이 실행기 목록에 없다']

    out('\n3. 짧은 대화')
    r = cli.chat('Reply with exactly one word: OK', 64, retries=0)
    good = not r['error'] and 'OK' in r['content'].upper()
    mark(good, f'응답 {r.get("sec")}초', (r['error'] or repr(r['content'][:40])))
    if r.get('thinking'):
        mark(None, '모델이 사고(thinking) 출력을 냈다. 느려지고 출력 한도를 다 쓸 수 있다',
             '00_먼저_읽기.md 6절의 사고 끄기를 보십시오')
        probs.append('사고(thinking) 출력')
    if r['error']:
        return probs + ['짧은 대화 실패: ' + r['error'][:120]]

    out('\n4. 문맥 길이(긴 글의 맨 앞을 기억하는가) — 집계형 프롬프트만 한 글을 보낸다. 느린 PC 에서 몇 분 걸린다')
    filler = BP.HEAD + BP.SC + '\n' + BP.RULES + BP.FEWSHOT
    probe = ('확인 낱말: KESTREL-4417\n이 낱말을 기억하라. 아래는 참고 자료다.\n\n' + filler
             + '\n\n[지시] 이 글의 맨 앞에 적힌 확인 낱말을 그대로 한 번만 출력하라. 다른 말은 쓰지 마라.\n')
    r = cli.chat(probe, 64, retries=0)
    tok = r.get('prompt_tokens')
    good = not r['error'] and 'KESTREL-4417' in (r['content'] or '')
    mark(good, f'{len(probe):,}자 프롬프트의 맨 앞 낱말' + (f' · 입력 {tok:,}토큰' if tok else ''),
         r['error'][:160] if r['error'] else f'{r.get("sec")}초 · 답 {r["content"][:30]!r}')
    if not good:
        out('      ' + LLM.context_hint(cfg['preset']))
        probs.append('문맥 길이 부족(긴 프롬프트의 앞이 잘림)')

    out('\n5. 집계형 SQL — 조건 F 전문(웹 화면과 같은 글)')
    con = harness.get_con()
    tmpl = BP.HEAD + BP.SC + '\n' + BP.RULES + BP.FEWSHOT + BP.TAIL
    r = cli.chat(tmpl.replace('{q}', '선박은 몇 척인가?'), 600, retries=0)
    if r['error']:
        mark(False, 'SQL 생성', r['error'][:160])
        probs.append('SQL 생성 실패')
    else:
        sql = clean_sql(r['content'])
        res, err = harness.run(con, sql) if sql not in ABSTAIN else (None, '기권 토큰')
        good = res is not None and len(res) == 1 and str(res[0][-1]) == '6'
        mark(good, f'"선박은 몇 척인가?" → {sql[:70]}', f'{r.get("sec")}초 · 결과 {res[:2] if res else err}')
        if not good:
            probs.append('SQL 정답이 아님(모델 성능 또는 프롬프트 잘림)')

    out('\n6. 기권 — 기록할 수 없는 값은 SQL 대신 기권 토큰')
    r = cli.chat(tmpl.replace('{q}', '발전기 정비에 투입된 인원은 평균 몇 명인가?'), 600, retries=0)
    tokn = clean_sql(r['content']) if not r['error'] else ''
    good = tokn == 'NO_RELIABLE_VALUE'
    mark(good if not r['error'] else False, f'→ {tokn[:60] or r["error"][:100]}', f'{r.get("sec")}초')
    if not good:
        mark(None, '기권 지시를 따르지 않았다. 이 모델은 기록이 없는 항목에도 숫자를 만들 수 있다(연구의 소형 모델 한계)')
        probs.append('기권 지시이행 실패')

    out('\n7. 절차형 JSON — 기록 1건으로 단계와 인용을 받는다')
    row = con.execute("SELECT TITLE, MACH_DESC, EQUIP_DESC, WORK_DATE, WORK_REMARK FROM master "
                      "WHERE TITLE = 'L.O purifier overhaul and assembly' LIMIT 1").fetchone()
    ctx = f'Record 1. Title: {row[0]}\nMachine: {row[1]} / {row[2]}\nDate: {row[3]}\n{row[4]}'
    q = 'What torque is the large bowl lock nut tightened to when assembling the purifier?'
    r = cli.chat(ANSWER_EN.format(ctx=ctx, q=q, naming=''), 1600, retries=0)
    obj = parse_json(r['content']) if not r['error'] else None
    if obj is None:
        mark(False, 'JSON 형식', (r['error'] or r['content'][:120]))
        probs.append('절차형 JSON 형식 실패(소형 모델에서 흔함)')
    else:
        steps = [s for s in (obj.get('steps') or []) if isinstance(s, dict)]
        okq = [s for s in steps if s.get('quote') and sq(str(s['quote'])) in sq(row[4])]
        has = any('280' in json.dumps(s, ensure_ascii=False) for s in okq)
        good = bool(steps) and len(okq) == len(steps) and has
        mark(good, f'단계 {len(steps)}개 · 원문과 같은 인용 {len(okq)}개 · 280 N.m 포함 {has}', f'{r.get("sec")}초')
        if not good:
            probs.append('인용이 원문과 다르거나 값이 빠짐')
    return probs


def main():
    a = sys.argv[1:]
    if '--check-duckdb' in a:           # START.bat · start.sh 가 쓴다(설정 파일과 무관하게 duckdb 판만)
        ok, msg = CONF.duckdb_status()
        print(('        ' if ok else '  [!] ') + msg, flush=True)
        return 0 if ok else (20 if CONF.duckdb_version()[0] is None else 21)
    cfg_path = a[a.index('--config') + 1] if '--config' in a else None
    try:
        cfg = CONF.load(cfg_path)
    except CONF.ConfigError as e:
        if '--print-port' in a:
            print(str(e), file=sys.stderr)
            return 10
        out('  [!] 설정 오류')
        out('  ' + str(e).replace('\n', '\n  '))
        return 10
    if '--print-port' in a:             # START.bat · start.sh 가 화면 포트를 읽는다
        print(cfg['web_port'])
        return 0
    os.environ['MAINT_DB'] = cfg['db']
    os.environ['BM25_IDX'] = cfg['index']
    os.environ['MAI_SMA_DATADICT'] = cfg['datadict']
    if '--quick' in a:
        return quick(cfg)
    t0 = time.time()
    out(f'LLM 연결 점검 — {time.strftime("%Y-%m-%d %H:%M")}')
    dk_ok, dk_msg = CONF.duckdb_status()
    if not dk_ok:                      # 판이 맞지 않으면 6분 뒤 7번에서 멈추지 말고 처음에 알린다
        out('  [!] ' + dk_msg)
        return 1
    probs = full(cfg)
    out(f'\n판정({time.time() - t0:.0f}초): ' + ('이 설정으로 웹 화면과 평가를 진행해도 됩니다.' if not probs else
                                           '아래 문제를 먼저 확인하십시오 — ' + ' / '.join(probs)))
    if '--no-report' not in a:
        try:
            os.makedirs(CONF.LOGS, exist_ok=True)
            with open(os.path.join(CONF.LOGS, 'check_llm_report.txt'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(LINES) + '\n')
            print('  기록: logs/check_llm_report.txt')
        except OSError:
            pass
    return 0 if not probs else 1


if __name__ == '__main__':
    sys.exit(main())
