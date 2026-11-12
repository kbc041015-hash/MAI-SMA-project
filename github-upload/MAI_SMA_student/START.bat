@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title MAI SMA 학생판 - 시작

if not exist "logs" mkdir "logs"
set "LOG=%~dp0logs\maintqa_web.log"
rem 서버는 이 폴더의 절대경로로 띄운다. STOP.bat 이 번호표 없이도 '이 폴더의 서버'만 골라 끌 수 있게 한다.
set "APP=%~dp0app\maintqa_web.py"

echo.
echo   ==========================================================
echo     MAI SMA - Smart Maintenance Advisor  [학생 이식판]
echo     선박 정비이력 질의응답 - 이 PC 의 LLM 으로 동작
echo     학생용 DB: 정비 문서 1,000건 + 질의응답 200문항
echo   ==========================================================
echo.

rem ── 1. 파이썬 확인 ─────────────────────────────────────────
rem 이름이 잡히는 것과 실제로 도는 것은 다르다. 마이크로소프트 스토어의 앱 실행
rem 별칭은 파이썬을 깔지 않아도 python.exe 라는 이름만 만들어 두어, where 로는
rem 찾아지지만 실행하면 스토어가 열린다. 3.10 이상인지 한 줄 돌려 보고 통과한 것만 쓴다.
rem 고르는 순서는 00_먼저_읽기.md 와 같다(python → py -3 → python3). 학생이 안내서대로
rem python -m pip 로 꾸러미를 깐 파이썬과 서버를 띄우는 파이썬이 같아야 한다.
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" > nul 2>&1 && set "PY=python"
if not defined PY ( py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" > nul 2>&1 && set "PY=py -3" )
if not defined PY ( python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" > nul 2>&1 && set "PY=python3" )
if not defined PY goto :nopython
echo   [1/5] 파이썬 확인 - %PY%
%PY% -c "import sys; print('        ' + sys.executable + '  (' + sys.version.split()[0] + ')')"

rem ── 2. duckdb 확인(판 1.5 이상 2 미만. 아니면 requirements.txt 로 설치) ──────────
%PY% -B tools\check_llm.py --check-duckdb
if not errorlevel 1 goto :haveduck
echo.
echo   [!] duckdb 를 requirements.txt 대로(판 1.5 이상 2 미만) 설치합니다. 인터넷은 이때 한 번만 씁니다.
echo       %PY% -m pip install -r requirements.txt
choice /c YN /m "      지금 설치할까요"
if errorlevel 2 goto :end_fail
%PY% -m pip install --disable-pip-version-check -r requirements.txt
%PY% -B tools\check_llm.py --check-duckdb
if errorlevel 1 goto :pipfail
:haveduck
echo   [2/5] duckdb 확인

rem ── 3. 설정 확인(config.json) ─────────────────────────────────
set "PORT="
for /f "delims=" %%p in ('%PY% -B tools\check_llm.py --print-port 2^>nul') do set "PORT=%%p"
if not defined PORT goto :badconfig
set "URL=http://127.0.0.1:%PORT%/"
echo   [3/5] 설정 확인 - config.json · 화면 포트 %PORT%

rem 이미 돌고 있으면 화면만 연다. 다른 프로그램이 그 포트를 쓰면 알린다.
call :probe
if errorlevel 2 goto :portbusy
if not errorlevel 1 (
  echo         이미 실행 중입니다. 화면만 엽니다.
  goto :open
)

rem ── 4. LLM 주소 응답 확인 ─────────────────────────────────────
%PY% -B tools\check_llm.py --quick
set "RC=%errorlevel%"
if "%RC%"=="0" goto :llm_ok
if "%RC%"=="13" goto :llm_ok
echo.
echo   위 안내대로 LLM 실행기와 config.json 을 확인한 뒤 다시 실행하십시오.
echo   자세한 점검:  %PY% -B tools\check_llm.py
goto :end_fail
:llm_ok
echo   [4/5] LLM 응답 확인

rem ── 5. 화면 서버를 창 없이 기동 → 브라우저 열기 ───────────────
rem PowerShell 의 Start-Process 로는 스토어판 파이썬을 띄우지 못한다(앱 실행 별칭이라
rem '액세스가 거부되었습니다'로 끝난다). cmd 의 start 는 PATH 를 그대로 따라가므로 이쪽을 쓴다.
rem 창은 파이썬이 뜨자마자 스스로 감춘다(--hide). 찍히는 글은 logs\maintqa_web.log 에 남는다.
if exist "%LOG%" move /y "%LOG%" "%LOG%.prev" > nul
echo   [5/5] 화면 서버를 띄웁니다. 준비되면 브라우저가 열리고 이 창은 닫힙니다.
echo         끌 때는 STOP.bat 을 실행하십시오(이 폴더의 서버만 끕니다).
start "MAI SMA student" /min %PY% -B "%APP%" --no-open --hide --log "%LOG%"

rem 최대 10분. config.json 의 llama_server 로 모델을 직접 올릴 때가 가장 오래 걸린다.
for /l %%i in (1,1,300) do (
  call :probe
  if not errorlevel 1 goto :open
  if exist "%LOG%" findstr /l /c:"[!]" /c:"Traceback" "%LOG%" > nul 2>&1 && goto :failed
  ping -n 3 127.0.0.1 > nul
)
echo.
echo   [!] 시간 안에 준비되지 않았습니다.
goto :showlog

:open
start "" "%URL%"
exit /b 0

rem ── 화면 응답 확인: 0 = 이 프로그램 · 1 = 응답 없음 · 2 = 다른 프로그램 ──
:probe
where curl > nul 2>&1
if errorlevel 1 goto :probe_ps
curl -s -m 2 "%URL%api/info" > "%TEMP%\mai_sma_probe.txt" 2> nul
if errorlevel 1 exit /b 1
findstr /l /c:"MAI_SMA_student" "%TEMP%\mai_sma_probe.txt" > nul 2>&1
if errorlevel 1 exit /b 2
exit /b 0
:probe_ps
powershell -NoProfile -Command "try{ $r=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%URL%api/info').Content; if($r -match 'MAI_SMA_student'){exit 0}else{exit 2} }catch{ exit 1 }"
exit /b %errorlevel%

:nopython
echo   [!] 파이썬 3.10 이상을 찾지 못했습니다.
echo       https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하십시오.
echo       설치 첫 화면에서 "Add python.exe to PATH" 를 반드시 체크합니다.
goto :end_fail

:pipfail
echo   [!] duckdb 를 맞는 판으로 설치하지 못했습니다. 인터넷 연결과 위의 오류를 확인하고, 직접 설치해 보십시오:
echo       %PY% -m pip install "duckdb>=1.5,<2"
goto :end_fail

:badconfig
echo.
%PY% -B tools\check_llm.py --quick
echo.
echo   config.json 을 고친 뒤 다시 실행하십시오. 실행기별 예시는 config_examples 폴더에 있습니다.
goto :end_fail

:portbusy
echo   [!] %PORT% 번 포트를 다른 프로그램이 쓰고 있습니다. config.json 의 web_port 를 바꾸십시오.
goto :end_fail

:failed
echo.
echo   [!] 화면 서버가 시작되지 못했습니다.
:showlog
echo       기록 파일: logs\maintqa_web.log  - 마지막 부분:
echo.
powershell -NoProfile -Command "if (Test-Path -LiteralPath '%LOG%') { Get-Content -Tail 25 -Encoding UTF8 -LiteralPath '%LOG%' }"
goto :end_fail

:end_fail
echo.
pause
exit /b 1
