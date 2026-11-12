# -*- coding: utf-8 -*-
"""config.json 의 LLM 연결로 평가기(run_eval.py)를 돌린다 — 주소·모델·프리셋을 손으로 옮겨 적지 않게.

  python eval/eval_from_config.py --task agg --ids Q001-Q010     집계형 10문항(조건 F · 자연어 문형)
  python eval/eval_from_config.py --task proc --ids P001-P005    절차형 5문항(고정 문맥 4건 · 영어 질문)
  python eval/eval_from_config.py --print-only --task agg        실행할 명령만 보인다(LLM 호출 없음)
그 밖의 인자(--cond · --phrasing · --tag · --resume · --overwrite · --out-dir · --max-tokens …)는
run_eval.py 에 그대로 넘긴다(--out-dir · --db 같은 경로는 이 명령을 친 폴더 기준으로 풀어 넘긴다).
결과는 eval/results/ 에 쌓인다(채점 규칙은 eval/00_평가_안내.md).
정답은 학생용 DB(eval/db/MAI_SMA_학생용DB_1000.duckdb, 정비 문서 1,000건) 기준이다.

config.json → run_eval.py 인자
  llamacpp  --preset llama                         원 실험 본문(temperature 0 · top_k 1 · seed 42 · 사고 끔)
  ollama    --preset openai --model <모델> --seed 42
  lmstudio  --preset openai --model <모델> --seed 42 --extra {"top_k": 1}
  openai    --preset openai --model <모델>
  공통      --base-url · --timeout(timeout_sec) · --extra(extra_body) · API 키는 환경변수 OPENAI_API_KEY 로만
웹 화면(app/llm_client.py)이 보내는 요청 필드와 같다. 다른 점은 출력 상한뿐이다(평가기는 원 실험대로
집계형 400 · 절차형 2200, 웹 화면은 600 · 1600).
"""
import json
import os
import subprocess
import sys
import urllib.parse

sys.dont_write_bytecode = True
EVAL = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(EVAL)
sys.path.insert(0, os.path.join(PKG, 'app'))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import sma_config as CONF  # noqa: E402

PRESET_ARGS = {
    'llamacpp': (['--preset', 'llama'], {}),
    'ollama': (['--preset', 'openai', '--seed', '42'], {}),
    'lmstudio': (['--preset', 'openai', '--seed', '42'], {'top_k': 1}),
    'openai': (['--preset', 'openai'], {}),
}


def run_eval_args(cfg, user_args):
    """config → run_eval.py 인자 목록. 사용자가 직접 준 인자는 덮어쓰지 않는다."""
    given = {a.split('=', 1)[0] for a in user_args if a.startswith('--')}
    args, extra = list(PRESET_ARGS[cfg['preset']][0]), dict(PRESET_ARGS[cfg['preset']][1])
    if '--preset' in given:
        args = []
    base = cfg['base_url']
    u = urllib.parse.urlsplit(base)
    if u.hostname == 'localhost' and u.scheme == 'http':
        # 윈도우에서 localhost 는 IPv6 부터 시도해 요청마다 2초가량 늦을 수 있다
        base = base.replace('//localhost', '//127.0.0.1', 1)
    if '--base-url' not in given:
        args += ['--base-url', base]
    if cfg.get('model') and '--model' not in given:
        args += ['--model', cfg['model']]
    if cfg.get('seed') is not None and cfg.get('seed') is not False and '--seed' not in given:
        if '--seed' in args:
            i = args.index('--seed')
            del args[i:i + 2]
        args += ['--seed', str(cfg['seed'])]
    if cfg.get('seed') is False and '--seed' in args:
        i = args.index('--seed')
        del args[i:i + 2]
    if cfg.get('temperature', 0) is None and '--no-temperature' not in given:
        args += ['--no-temperature']
    extra.update(cfg.get('extra_body') or {})
    if extra and '--extra' not in given:
        args += ['--extra', json.dumps(extra, ensure_ascii=False)]
    if '--timeout' not in given:
        args += ['--timeout', str(cfg.get('timeout_sec') or 600)]
    return args + list(user_args)


PATH_ARGS = ('--out-dir', '--db', '--score-file', '--score-dir')


def abs_path_args(argv):
    """경로 인자(--out-dir 등)를 이 명령을 친 폴더(보통 패키지 폴더) 기준의 절대경로로 바꾼다.
    평가기는 eval/ 에서 돌기 때문에, 그대로 넘기면 상대경로가 eval/ 기준으로 풀린다(안내서의 경로는 모두 패키지 기준)."""
    out, i = [], 0
    while i < len(argv):
        x = argv[i]
        k, eq, v = x.partition('=')
        if x in PATH_ARGS and i + 1 < len(argv):
            out += [x, os.path.abspath(argv[i + 1])]
            i += 2
            continue
        out.append(f'{k}={os.path.abspath(v)}' if eq and k in PATH_ARGS and v else x)
        i += 1
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg_path = None
    if '--config' in argv:
        i = argv.index('--config')
        cfg_path = argv[i + 1]
        del argv[i:i + 2]
    only = '--print-only' in argv
    argv = abs_path_args([a for a in argv if a != '--print-only'])
    try:
        cfg = CONF.load(cfg_path)
    except CONF.ConfigError as e:
        print('[!] 설정 오류\n  ' + str(e).replace('\n', '\n  '))
        return 2
    args = run_eval_args(cfg, argv)
    print('  평가기 실행: python eval/run_eval.py ' + ' '.join(
        (f"'{a}'" if (' ' in a or '{' in a) else a) for a in args))
    if only:
        return 0
    env = dict(os.environ)
    env['OPENAI_API_KEY'] = cfg.get('api_key') or ''      # 다른 곳의 키가 로컬 서버로 새지 않게
    env.pop('OPENAI_BASE_URL', None)
    env.pop('OPENAI_MODEL', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.call([sys.executable, '-B', os.path.join(EVAL, 'run_eval.py')] + args, cwd=EVAL, env=env)


if __name__ == '__main__':
    sys.exit(main())
