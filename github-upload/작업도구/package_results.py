"""Package actual reports, filled records and reproducibility evidence."""
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'MAI_SMA_student'
proof = json.loads((ROOT / '검증결과.json').read_text(encoding='utf-8'))
if proof['aggregate']['count'] != 10 or proof['procedural']['count'] != 5:
    raise SystemExit('Expected all 15 actual sample responses before packaging.')
if not proof['selftest_pass'] or not proof['llm_check_pass']:
    raise SystemExit('Required check did not pass; inspect the report before packaging.')
files = [ROOT / name for name in ['실행_및_평가_보고서.docx', '실행_및_평가_보고서.txt',
                                 '샘플_15문항_평가기록.xlsx', '검증결과.json', '이_PC에서_사용하기.txt']]
for stem in ['qwen3_14b_sample_agg', 'qwen3_14b_sample_proc']:
    for suffix in ['.csv', '.jsonl', '_summary.txt', '_summary.json']:
        files.append(PKG / 'eval/results' / (stem + suffix))
files += [PKG / 'logs/selftest_run.txt', PKG / 'logs/check_llm_report.txt',
          PKG / 'logs/actual_web_results.json', PKG / 'config.json',
          PKG / 'eval/results/qwen3_14b_sample_proc_steps.csv', ROOT / 'requirements_local.txt']
out = ROOT / '예시평가_결과모음.zip'
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(ROOT).as_posix())
print(out.name, len(files), 'files', out.stat().st_size, 'bytes')
