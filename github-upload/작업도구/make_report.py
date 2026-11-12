"""Create a Korean Word report from actual local verification and evaluation files."""
import datetime as dt
import hashlib
import json
import pathlib
import re
import zipfile
from html import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'MAI_SMA_student'
RESULTS = PKG / 'eval/results'
NOW = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))


def read_text(path):
    if not path.exists():
        return ''
    b = path.read_bytes()
    return b.decode('utf-16' if b.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig', errors='replace')


def load_summary(tag):
    path = RESULTS / (tag + '_summary.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def make():
    cfg = json.loads((PKG / 'config.json').read_text(encoding='utf-8-sig'))
    selftest = read_text(PKG / 'logs/selftest_run.txt')
    llm = read_text(PKG / 'logs/check_llm_report.txt')
    if not llm:
        llm = read_text(PKG / 'logs/check_llm_run.txt')
    manifest = json.loads((PKG / 'build/manifest.json').read_text(encoding='utf-8'))
    changed = [x['path'] for x in manifest['items']
               if hashlib.sha256((PKG / x['path']).read_bytes()).hexdigest() != x['sha256']]
    rows = []
    for tag in ['qwen3_14b_sample_agg', 'qwen3_14b_sample_proc']:
        f = RESULTS / (tag + '.jsonl')
        if f.exists():
            rows.extend(json.loads(line) for line in f.read_text(encoding='utf-8').splitlines() if line.strip())
    agg = [x for x in rows if x['qid'].startswith('Q')]
    proc = [x for x in rows if x['qid'].startswith('P')]
    strict = {label: sum(x.get('strict') == key for x in agg)
              for key, label in [('match','O'),('mismatch','X'),('error','E')]}
    questions = {f"Q{int(x['num']):03d}":x for x in
                 map(json.loads, (PKG/'eval/questions/aggregate100.jsonl').read_text(encoding='utf-8').splitlines())}
    error_analysis = []
    for item in agg:
        if item['strict'] == 'match':
            continue
        sql = item.get('extracted') or ''
        note = '생성 SQL의 실행 결과가 정답 결과와 다르다.'
        if item['qid']=='Q002' and 'VESSEL_NAME' in sql and 'VESSELCODE' not in sql:
            note = '기록양식의 요구열인 선박 코드(VESSELCODE) 대신 선박명(VESSEL_NAME)을 출력했다.'
        elif item['qid']=='Q004' and 'DISTINCT' in sql.upper() and 'PMS_CODE' in sql:
            note = '작업 항목의 전체 행 수 대신 중복을 제거한 작업 코드 수를 셌다.'
        elif item['qid']=='Q005' and 'CONS_YN' in sql and 'master' in sql:
            note = '부품 소모 기록(consumption)의 행 수 대신 소모 플래그가 있는 정비 문서 수를 셌다.'
        error_analysis.append(dict(qid=item['qid'], sql=sql, gold_sql=questions[item['qid']]['gold_sql'],
                                   result=item.get('result_text'), gold=questions[item['qid']]['gold_result'],
                                   explanation=note))
    steps = sum(x.get('n_steps', 0) for x in proc)
    exact = sum(x.get('n_exact', 0) for x in proc)
    misrec = sum(x.get('n_misrec', 0) for x in proc)
    halluc = sum(x.get('n_halluc', 0) for x in proc)
    with_steps = sum(x.get('cls')=='steps' for x in proc)
    abstained = sum(x.get('cls')=='abstain' for x in proc)
    generation_failed = sum(x.get('cls')=='gen_fail' for x in proc)
    webpath = PKG / 'logs/actual_web_results.json'
    web = json.loads(webpath.read_text(encoding='utf-8')) if webpath.exists() else []
    web_summary = []
    for entry in web:
        value = entry.get('result') or {}
        web_summary.append(dict(case=entry['case'], kind=value.get('kind'), rows=value.get('rows'),
                                token=value.get('token'), verified=value.get('verified'),
                                steps=len(value.get('steps') or []), error=entry.get('error')))
    call_times = {int(i):float(sec) for i,sec in re.findall(r'REAL CHECK CALL (\d+) seconds ([0-9.]+)',
                                                         read_text(PKG / 'logs/work_run.txt'))}
    summary = dict(created=NOW.isoformat(timespec='seconds'), model=cfg['model'],
                   base_model='qwen3:14b / 14.8B / Q4_K_M', context_tokens=cfg['context_tokens'],
                   device='CPU + Intel Iris Xe (Vulkan)', gpu_layers=cfg['llama_server']['ngl'],
                   selftest_pass='selftest PASS' in selftest,
                   llm_check_pass='진행해도 됩니다' in llm,
                   modified_original_files=changed,
                   aggregate=dict(count=len(agg), strict=strict, error_analysis=error_analysis),
                   procedural=dict(count=len(proc), steps=steps, exact=exact, questions_with_steps=with_steps,
                                   abstained=abstained, generation_failed=generation_failed,
                                   misrec=misrec, halluc=halluc),
                   actual_web=web_summary, check_call_seconds=call_times, full_200_completed=False)
    (ROOT / '검증결과.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    paras = []
    plain = []

    def p(text, style=None):
        plain.append(text)
        st = f'<w:pStyle w:val="{style}"/>' if style else ''
        paras.append(f'<w:p><w:pPr>{st}</w:pPr><w:r><w:t xml:space="preserve">{escape(str(text))}</w:t></w:r></w:p>')

    def table(items):
        plain.extend(' | '.join(map(str, row)) for row in items)
        xml = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>']
        xml.extend(f'<w:{edge} w:val="single" w:sz="4" w:color="C9D4DE"/>'
                   for edge in ['top','left','bottom','right','insideH','insideV'])
        xml.append('</w:tblBorders></w:tblPr>')
        for index, row in enumerate(items):
            xml.append('<w:tr>')
            for value in row:
                shade = '<w:shd w:fill="E8EFF5"/>' if index == 0 else ''
                xml.append(f'<w:tc><w:tcPr>{shade}<w:tcMar><w:top w:w="80" w:type="dxa"/>'
                           f'<w:left w:w="100" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/>'
                           f'<w:right w:w="100" w:type="dxa"/></w:tcMar></w:tcPr>'
                           f'<w:p><w:r><w:t>{escape(str(value))}</w:t></w:r></w:p></w:tc>')
            xml.append('</w:tr>')
        xml.append('</w:tbl>')
        paras.append(''.join(xml))

    p('MAI SMA 학생 패키지 설치·실행 및 평가 보고서', 'Title')
    p('작성 시각: ' + NOW.strftime('%Y-%m-%d %H:%M (한국 표준시)'))
    p('1. 자료 해석과 작업 범위', 'Heading1')
    p('제공된 압축파일은 선박 정비이력 질의응답 프로그램, 학생용 DB, 200문항 평가 세트, 논문 초안으로 구성되어 있다. '
      '학생 안내서의 작업은 로컬 환경 준비 → 모델 연결 → 자체 점검 → 웹 실행 → 예시 평가 및 기록이다. '
      '이번 작업에서는 안내서에 제시된 집계형 10문항과 절차형 5문항을 평가 범위로 삼았다.')
    p('논문 초안의 기존 실험 수치를 이번 PC의 측정값으로 사용하지 않았다. 별도의 이름·학번·제출 양식은 지정되어 있지 않다.')
    p('2. 실행 환경', 'Heading1')
    table([['항목','설정'], ['운영체제 / CPU / 메모리','Windows / Intel Core i7-1360P / RAM 약 16GB'],
           ['Python / DuckDB','3.12.10 / 1.5.6'], ['실행기 / 기반 모델','llama-server 0.9.0-dev (Ollama 배포본) / Qwen3 14B Q4_K_M'],
           ['전용 모델 이름',cfg['model']], ['문맥 및 생성 설정','8192토큰 / temperature 0 / top_k 1 / seed 42 / 사고 끔'],
           ['추론 장치','CPU + Intel Iris Xe (Vulkan), GPU에 28개 층 배치'],
           ['계산 / 메모리 설정','batch 512·ubatch 256 / 스레드 8·16 / KV f16 / 추가 문맥 캐시 0MB'],
           ['웹 화면','http://127.0.0.1:8780/'], ['모델 주소',cfg['base_url']]])
    p('시스템의 Python 실행 별칭 대신 폴더에 포함한 실행 환경을 사용했다. '
      'Ollama에 설치되어 있던 Qwen3 가중치를 전용 llama-server로 직접 읽는다. '
      '긴 입력 시간 초과를 개선하기 위해 설치된 Vulkan 모듈을 지정하고 내장 GPU에 28개 층을 배치했다. '
      '계산 묶음과 추가 문맥 캐시를 줄인 실행 설정을 적용했다. KV 캐시는 기본 정밀도 f16을 사용했다. '
      '이 실행 설정을 포함해 비교해야 하며 모델 이름만으로 다른 실험과 동일 조건이라고 볼 수 없다.')
    p('3. 검증 결과', 'Heading1')
    stline = next((x.strip() for x in selftest.splitlines() if x.startswith('결과:')), '결과 파일 확인 필요')
    eval_check = next((x.partition('—')[2].strip() for x in selftest.splitlines()
                       if '[PASS] eval/run_eval.py --self-test(' in x), '점검 로그 확인 필요')
    llmline = next((x.strip() for x in reversed(llm.splitlines()) if x.strip().startswith('판정')), '모델 점검 결과 확인 필요')
    table([['검사','실제 결과'], ['패키지 자체 점검',stline], ['채점기 자체 검사',eval_check+' (평가 결과 폴더는 검사 제외)'],
           ['실제 모델 점검',llmline], ['웹 화면 / 정보 API','HTTP 200 응답 확인'],
           ['검색 색인','총 1,046건 = 정비 문서 741 + 참고 기록 305'],
           ['원본 파일 비교','변경 파일: ' + (', '.join(changed) if changed else '없음')]])
    p('DB·프롬프트·채점 함수 등 원본 파일은 배포 명세의 SHA-256과 비교했다. '
      'config.json은 이 PC의 모델 연결을 위해 변경했다. 자체 점검은 모의 모델을 사용하므로 실제 모델의 정답률과 구분한다.')
    p('긴 SQL 입력을 재사용하기 위해 실제 모델 점검의 기권 단계 직후에 집계형 평가와 웹 확인을 실행하고, '
      '그다음 절차형 검사를 진행했다. 요청과 응답은 원본 도구가 생성·검사한 그대로이며, '
      '모델 점검의 전체 경과 시간에는 중간 평가 시간도 포함된다.')
    if 2 in call_times:
        p(f'실제 긴 입력 처리 검사: {call_times[2]:.2f}초.')
    if web_summary:
        actual_rows = [['실제 웹 질문 경로','관측 결과']]
        for value in web_summary:
            observed = value['error'] or str(value['rows'] if value['case']=='aggregate' else
                        value['token'] if value['case']=='abstain' else
                        f"단계 {value['steps']} / 검증 인용 {value['verified']}")
            actual_rows.append([value['case']+' / '+str(value['kind']), observed])
        table(actual_rows)
    p('4. 실제 모델 예시 평가', 'Heading1')
    agg_text = read_text(RESULTS / 'qwen3_14b_sample_agg_summary.txt').strip()
    proc_text = read_text(RESULTS / 'qwen3_14b_sample_proc_summary.txt').strip()
    table([['구분','실행 범위','실측 결과'],
           ['집계형',f'Q001–Q010 중 {len(agg)}문항', f"정답 {strict['O']} / 오답 {strict['X']} / 오류 {strict['E']}"],
           ['절차형',f'P001–P005 중 {len(proc)}문항',f'단계 생성 {with_steps}문항 / 기권 {abstained} / 생성 실패 {generation_failed}. '
            f'인용정확 {exact}/{steps}단계, 기록번호 오기 {misrec}, 인용 환각 {halluc}']])
    for title, text in [('집계형 공식 요약', agg_text), ('절차형 공식 요약', proc_text)]:
        p(title, 'Heading2')
        for line in text.splitlines():
            p(line)
    if error_analysis:
        p('집계형 오답 해석', 'Heading2')
        table([['문항','모델 결과 / 정답','해석']] +
              [[x['qid'],str(x['result'])+' / '+str(x['gold']),x['explanation']] for x in error_analysis])
        p('위 해석은 보관된 모델 SQL과 제공된 정답 SQL을 비교한 것이다. 모델 응답이나 채점 결과를 수정하지 않았다.')
    p('해석: 이번 결과는 학생용 1,000건 표본 DB와 지정된 15문항에 대한 결과다. '
      '전체 200문항의 성능이나 논문에 실린 전체 선대 실험의 성능으로 일반화할 수 없다. '
      '절차형 인용정확은 인용이 지목한 원문에 존재하는지 검사한 값이며, 정비 조언의 안전성 또는 답 전체의 타당성 판정과는 별개다.')
    p('집계형 평가의 응답시간은 직전 점검에서 동일한 긴 프롬프트를 이미 읽어 둔 캐시가 남은 상태에서 측정했다. '
      '프로그램을 처음 실행한 뒤 첫 질문은 이보다 오래 걸릴 수 있다. 절차형 5문항은 제공된 고정 문맥 4건으로 평가했다.')
    p('5. 실행 방법과 산출물', 'Heading1')
    for text in ['웹 사용: 실행.cmd → http://127.0.0.1:8780/',
                 '웹 종료: 종료.cmd', '점검 재실행: 점검.cmd',
                 '예시 평가 재개: 샘플_15문항_평가.cmd',
                 '전체 평가 실행: 전체_200문항_평가.cmd (이번 보고서의 실측 범위에는 포함하지 않음)',
                 '평가 기록: 샘플_15문항_평가기록.xlsx',
                 '응답 원문·SQL·판정·요약: MAI_SMA_student/eval/results/',
                 '점검 로그: MAI_SMA_student/logs/',
                 '실행 설정 재현: MAI_SMA_student/config.json의 llama_server 및 extra_args 항목']:
        p(text)
    p('CPU와 내장 GPU가 시스템 메모리를 함께 사용하며 첫 긴 입력 처리에 시간이 걸릴 수 있다. '
      '평가 도중에는 웹에서 다른 질문을 동시에 보내지 않도록 하고, 평가가 중단되면 같은 실행 파일로 이어서 진행할 수 있다.')
    p('6. 참고 자료', 'Heading1')
    p('제공 자료: 읽어보기.txt, MAI_SMA_student/00_먼저_읽기.md, eval/00_평가_안내.md, build/manifest.json.')
    p('실행기 공식 문서: https://github.com/ggml-org/llama.cpp/tree/master/tools/server ; https://huggingface.co/Qwen/Qwen3-14B')
    p('Vulkan 모듈 경로 지정 근거: https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/ggml-backend-reg.cpp')

    wns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{wns}"><w:body>'
                + ''.join(paras) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>')
    styles = (f'<w:styles xmlns:w="{wns}"><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Malgun Gothic" w:hAnsi="Malgun Gothic" w:eastAsia="맑은 고딕"/>'
              '<w:sz w:val="21"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
              '<w:spacing w:after="110" w:line="320" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="260"/></w:pPr>'
              '<w:rPr><w:b/><w:color w:val="183B56"/><w:sz w:val="36"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:keepNext/>'
              '<w:spacing w:before="260" w:after="140"/></w:pPr><w:rPr><w:b/><w:color w:val="183B56"/><w:sz w:val="27"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:keepNext/></w:pPr>'
              '<w:rPr><w:b/><w:sz w:val="23"/></w:rPr></w:style></w:styles>')
    content_types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'
    root_rel = relationships.format('<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>')
    doc_rel = relationships.format('<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    with zipfile.ZipFile(ROOT / '실행_및_평가_보고서.docx', 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in {'[Content_Types].xml':content_types, '_rels/.rels':root_rel,
                           'word/document.xml':document, 'word/styles.xml':styles,
                           'word/_rels/document.xml.rels':doc_rel}.items():
            z.writestr(name, data.encode('utf-8'))
    (ROOT / '실행_및_평가_보고서.txt').write_text('\n\n'.join(plain), encoding='utf-8-sig')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    make()
