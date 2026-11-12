@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title MAI SMA 학생판 - 종료

rem 화면 서버는 창 없이 뒤에서 돈다. 닫을 창이 없으므로 여기서 끝낸다.
rem 이 폴더의 서버만 끈다. 같은 PC 의 다른 사본 · 다른 프로그램 · LLM 실행기(Ollama · LM Studio 등)는
rem 건드리지 않는다. 이 프로그램이 직접 띄운 llama-server(config.json 의 llama_server)만 함께 내린다.
rem
rem   (1) 번호표 logs\web.pid — 기동할 때 남긴 프로세스 번호. 그 번호의 프로세스 명령줄에
rem       maintqa_web.py 가 있는지 한 번 더 확인한 뒤 끈다.
rem   (2) 번호표로 하나도 끄지 못했을 때만 — 명령줄에 이 폴더의 절대경로 app\maintqa_web.py 가 든
rem       파이썬을 찾는다(START.bat 이 서버를 그 절대경로로 띄운다). 다른 폴더의 사본은 경로가 달라 걸리지 않는다.
rem       프로세스 이름은 python.exe 가 아닐 수 있다(스토어판은 python3.13.exe 처럼 판 번호가 붙는다).
rem   llama-server 는 번호표 logs\llama_server.pid 로만 찾는다.
set "MAI_SMA_APP=%~dp0app\maintqa_web.py"

echo.
echo   ==========================================================
echo     MAI SMA [학생 이식판] - 종료
echo   ==========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'SilentlyContinue'; $w = 0; $l = 0; $app = $env:MAI_SMA_APP; $alt = $app.Replace([char]92, [char]47);" ^
  "if (Test-Path 'logs\web.pid') { $id = [int]((Get-Content 'logs\web.pid' -Raw).Trim());" ^
  "  $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id);" ^
  "  if ($p -and $p.CommandLine -like '*maintqa_web.py*') { Stop-Process -Id $id -Force; $w++ }; Remove-Item 'logs\web.pid' }" ^
  "if ($w -eq 0 -and $app) { $rest = @(Get-CimInstance Win32_Process | Where-Object { ($_.Name -like 'python*' -or $_.Name -like 'py.exe') -and $_.CommandLine -and ($_.CommandLine.IndexOf($app, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or $_.CommandLine.IndexOf($alt, [StringComparison]::OrdinalIgnoreCase) -ge 0) });" ^
  "  foreach ($p in $rest) { Stop-Process -Id $p.ProcessId -Force; if ($p.Name -notlike 'py.exe') { $w++ } }; Remove-Item 'logs\web.pid' }" ^
  "if (Test-Path 'logs\llama_server.pid') { $id = [int]((Get-Content 'logs\llama_server.pid' -Raw).Trim());" ^
  "  $p = Get-Process -Id $id; if ($p -and $p.ProcessName -like 'llama-server*') { Stop-Process -Id $id -Force; $l++ }; Remove-Item 'logs\llama_server.pid' }" ^
  "Write-Host ('  화면 서버 {0}개, 이 프로그램이 띄운 llama-server {1}개를 종료했습니다.' -f $w, $l)"

echo.
echo   LLM 실행기(Ollama · LM Studio 등)는 그대로 둡니다. 필요하면 그 프로그램에서 끄십시오.
echo   이 창은 닫아도 됩니다.
echo.
ping -n 4 127.0.0.1 > nul
exit /b 0
