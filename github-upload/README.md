# MAI SMA · Qwen3 14B 실행 및 예시 평가

수업에서 제공된 MAI SMA 학생 패키지를 로컬 Qwen3 14B에 연결하고 예시 15문항을 평가한 제출용 소스 정리다.
기반 프로그램은 수업 제공 코드이며, 수행한 작업은 실행 환경 준비, 모델·Vulkan 연결 설정, 점검, 평가 및 결과 정리다.
제공된 DB·연구 프롬프트·채점 함수는 원래 작업에서 변경하지 않았다.

이 폴더는 **비공개(Private) 수업 제출용**으로 준비했다. 원본 안내의 수업 밖 재배포 제한을 따른다.
공개 저장소 제출을 요구받은 경우 제공 코드의 공개 범위를 담당자에게 확인한다.

## 구성

- `MAI_SMA_student/`: 제공 프로그램의 소스·프롬프트 템플릿·평가기·설명 문서.
- `MAI_SMA_student/config.example.json`: Qwen3 14B + Vulkan 설정 예시. 경로는 일반적인 자리표시값이다.
- `작업도구/`: 원래 작업의 점검·예시 평가 및 보고서 생성 보조 코드.
- `reports/evaluation_summary.md`: 응답 원문을 제외한 실측 요약과 해석.
- `reports/evaluation_summary.json`: 실측 수치.
- `reports/source_hashes.json`: 제공받은 파일과 비교할 수 있는 소스 해시.
- `requirements.txt`: 이번 실행에서 사용한 Python 패키지 버전.

## 평가 결과

Qwen3 14B(Q4_K_M)를 사용했다. 자체 점검 76/76 PASS, 실제 모델 점검 PASS.
집계형 10문항은 7개 정답, 절차형 5문항은 17개 단계 중 인용 14개 일치와 의도적 기권 1문항이다.
자세한 조건과 한계는 [평가 요약](reports/evaluation_summary.md)을 참조한다.
전체 200문항 평가는 아직 수행하지 않았다.

## 로컬 실행 재현

이 저장소에는 DB·검색 색인·평가 문항과 고정 문맥을 넣지 않았다.
따라서 저장소만 내려받은 상태에서는 웹 실행·자체 점검·채점을 완료할 수 없다.

1. 수업에서 제공받은 원본 ZIP을 **로컬 PC에서만** 이 저장소 폴더에 복원해 제한 자료를 준비한다.
   `MAI_SMA_student/data/`와 `eval/`의 DB·문항·문맥·양식·이미지 등이 필요하다.
2. Python 3.10 이상과 의존 패키지를 준비한다. 이번 실측의 Python은 3.12.10이다.
3. `config.example.json`을 `config.json`으로 복사하고 실행 파일·GGUF 모델 경로를 자기 PC에 맞춘다.
   모델은 Qwen3 **14B** Q4_K_M, 문맥 길이는 8192 이상으로 둔다.
4. Vulkan을 사용하는 경우 실행 전에 모듈 경로를 지정한다. 원래 PC의 예시는 다음과 같다.

   ```powershell
   $env:GGML_BACKEND_PATH = "$env:LOCALAPPDATA/Programs/Ollama/lib/ollama/vulkan/ggml-vulkan.dll"
   $env:PATH = "$env:LOCALAPPDATA/Programs/Ollama/lib/ollama/vulkan;" + $env:PATH
   ```

   설정 예시의 `exe`와 `model_path`는 실제 존재하는 파일로 바꿔야 한다.
   GPU 층 수 28과 `Vulkan0`은 측정 PC 기준이므로 다른 PC에서는 장치·메모리에 맞춘다.
5. 아래 명령은 `MAI_SMA_student` 폴더에서, 의존 패키지를 설치한 같은 Python으로 실행한다.

   ```text
   python tools/selftest.py
   python app/maintqa_web.py
   python tools/check_llm.py
   python eval/eval_from_config.py --task agg --ids Q001-Q010
   python eval/eval_from_config.py --task proc --ids P001-P005
   ```

실제 데이터와 원문 응답이 들어 있는 결과 폴더는 다시 업로드하지 않는다.
복원한 원본과 이번에 준비한 소스가 같은지 확인할 때 `source_hashes.json`을 사용할 수 있다.
실행 환경을 이 업로드용 사본으로 이전한 것은 아니며, 기존 PC의 실행 폴더는 그대로 유지된다.

## GitHub 업로드

GitHub에서 이름 `mai-sma-qwen3-14b`인 **Private** 저장소를 만든 뒤 이 폴더 안의 내용을 업로드한다.
기존 과제용 저장소 주소를 받았다면 그 저장소를 사용한다.
웹 화면에서 올릴 때는 준비한 폴더만 선택한다. `.gitignore`는 Git 명령을 사용할 때의 제외 규칙이며,
웹에서 파일을 직접 선택해 올리는 작업의 보안 필터로 삼지 않는다.

원본 ZIP, DB, 검색 색인, 문항별 문맥, 엑셀, 논문, 응답 로그, 모델 가중치, Python 실행 파일은 제외했다.
별도의 재배포 라이선스는 임의로 추가하지 않았다.

공식 안내: [저장소 만들기](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository),
[파일 업로드](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).
