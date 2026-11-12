"""Run the original checks and sample evaluation while reusing the long SQL prefix.

The original Client.chat receives and returns its arguments and result unchanged.
After the fourth real check call (SQL abstention), run aggregate evaluation before
the checker switches to procedural context. This avoids processing the same long
SQL prefix an extra time on this CPU. All original package files stay unchanged.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'MAI_SMA_student'
LOGS = PKG / 'logs'
LOGS.mkdir(exist_ok=True)
sys.path.insert(0, str(PKG / 'app'))
sys.path.insert(0, str(PKG / 'tools'))
import llm_client
import check_llm
exit_codes = []


def run(args, log):
    print('START', log, flush=True)
    with (LOGS / log).open('w', encoding='utf-8') as output:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-u', '-B'] + args,
                                cwd=PKG, stdout=output, stderr=subprocess.STDOUT)
    print('DONE', log, 'exit', result.returncode, flush=True)
    exit_codes.append({'step':log, 'exit':result.returncode})
    return result.returncode


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(120):
    try:
        with opener.open('http://127.0.0.1:50082/v1/models', timeout=2) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit('Model server did not become ready.')

original_chat = llm_client.Client.chat
calls = 0
aggregate_ran = False


def checked_chat(self, *args, **kwargs):
    global calls, aggregate_ran
    result = original_chat(self, *args, **kwargs)
    calls += 1
    print('REAL CHECK CALL', calls, 'seconds', result.get('sec'), 'error', bool(result.get('error')), flush=True)
    if calls == 4 and not aggregate_ran:
        aggregate_ran = True
        run(['eval/eval_from_config.py', '--task', 'agg', '--ids', 'Q001-Q010',
             '--tag', 'qwen3_14b_sample_agg', '--resume'], 'eval_sample_agg_run.txt')
        run([str(ROOT / '작업도구/check_web.py')], 'actual_web_run.txt')
    return result


llm_client.Client.chat = checked_chat
print('START original real-model checker', flush=True)
try:
    check_code = check_llm.main()
finally:
    llm_client.Client.chat = original_chat
print('DONE original real-model checker', check_code, flush=True)
if not aggregate_ran:
    run(['eval/eval_from_config.py', '--task', 'agg', '--ids', 'Q001-Q010',
         '--tag', 'qwen3_14b_sample_agg', '--resume'], 'eval_sample_agg_run.txt')
run(['eval/eval_from_config.py', '--task', 'proc', '--ids', 'P001-P005',
     '--tag', 'qwen3_14b_sample_proc', '--resume'], 'eval_sample_proc_run.txt')
run(['eval/fill_record_form.py', 'eval/results/qwen3_14b_sample_agg.csv',
     'eval/results/qwen3_14b_sample_proc.csv', '--out', str(ROOT / '샘플_15문항_평가기록.xlsx')],
    'fill_record_run.txt')
run([str(ROOT / '작업도구/make_report.py')], 'report_build_run.txt')
(LOGS / 'work_complete.json').write_text(json.dumps({'check_exit':check_code, 'steps':exit_codes,
    'completed':check_code == 0 and all(x['exit'] == 0 for x in exit_codes)}), encoding='utf-8')
print('WORK COMPLETE', flush=True)
