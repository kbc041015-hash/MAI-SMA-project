#!/bin/sh
# MAI SMA 학생 이식판 — macOS · Linux 용 시작/종료 (Windows 는 START.bat · STOP.bat)
#   sh start.sh          켜기: 파이썬 확인 → duckdb 확인(1.5 이상 2 미만) → 설정 확인 → LLM 응답 확인 → 화면 서버 기동 → 브라우저
#   sh start.sh stop     끄기: 이 폴더의 서버와, 이 프로그램이 직접 띄운 llama-server 만 끈다
cd "$(dirname "$0")" || exit 1
mkdir -p logs
# 서버는 이 폴더의 절대경로로 띄운다(번호표가 없어도 이 폴더의 서버만 골라 끌 수 있게)
APP="$(pwd)/app/maintqa_web.py"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done

if [ "$1" = "stop" ]; then
  n=0
  if [ -f logs/web.pid ]; then
    pid=$(cat logs/web.pid)
    if ps -p "$pid" -o command= 2>/dev/null | grep -q maintqa_web.py; then kill "$pid" && n=1; fi
    rm -f logs/web.pid
  fi
  if [ $n -eq 0 ]; then
    # 번호표로 끄지 못했을 때만: 명령줄에 이 폴더의 절대경로 app/maintqa_web.py 가 든 프로세스(다른 폴더의 사본은 제외)
    for pid in $(ps -eo pid=,args= 2>/dev/null | awk -v p="$APP" 'index($0, p) && $2 !~ /awk$/ {print $1}'); do
      kill "$pid" 2>/dev/null && n=$((n + 1))
    done
  fi
  if [ -f logs/llama_server.pid ]; then
    pid=$(cat logs/llama_server.pid)
    if ps -p "$pid" -o command= 2>/dev/null | grep -q llama-server; then kill "$pid"; fi
    rm -f logs/llama_server.pid
  fi
  echo "  화면 서버 $n 개를 종료했습니다. LLM 실행기(Ollama · LM Studio 등)는 그대로 둡니다."
  exit 0
fi

[ -z "$PY" ] && { echo "  [!] 파이썬 3.10 이상을 찾지 못했습니다. https://www.python.org/downloads/"; exit 1; }
echo "  [1/5] 파이썬 확인 - $PY ($("$PY" -c 'import sys; print(sys.executable)'))"
if ! "$PY" -B tools/check_llm.py --check-duckdb; then
  echo "  [!] duckdb 를 requirements.txt 대로(판 1.5 이상 2 미만) 설치한 뒤 다시 실행하십시오:"
  echo "      $PY -m pip install -r requirements.txt"
  exit 1
fi
echo "  [2/5] duckdb 확인"
PORT=$("$PY" -B tools/check_llm.py --print-port 2>/dev/null)
if [ -z "$PORT" ]; then
  "$PY" -B tools/check_llm.py --quick
  echo "  config.json 을 고친 뒤 다시 실행하십시오. 예시는 config_examples/ 에 있습니다."
  exit 1
fi
URL="http://127.0.0.1:$PORT/"
echo "  [3/5] 설정 확인 - 화면 포트 $PORT"

probe() {
  body=$(curl -s -m 2 "${URL}api/info" 2>/dev/null) || return 1
  echo "$body" | grep -q MAI_SMA_student && return 0
  return 2
}
open_url() {
  echo "  화면 주소: $URL"
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
  fi
}

probe; st=$?
if [ $st -eq 0 ]; then echo "  이미 실행 중입니다."; open_url; exit 0; fi
if [ $st -eq 2 ]; then echo "  [!] $PORT 번 포트를 다른 프로그램이 씁니다. config.json 의 web_port 를 바꾸십시오."; exit 1; fi

"$PY" -B tools/check_llm.py --quick; rc=$?
if [ $rc -ne 0 ] && [ $rc -ne 13 ]; then
  echo "  위 안내대로 LLM 실행기와 config.json 을 확인한 뒤 다시 실행하십시오. 자세한 점검: $PY -B tools/check_llm.py"
  exit 1
fi
echo "  [4/5] LLM 응답 확인"

[ -f logs/maintqa_web.log ] && mv -f logs/maintqa_web.log logs/maintqa_web.log.prev
nohup "$PY" -B "$APP" --no-open --log logs/maintqa_web.log >/dev/null 2>&1 &
echo "  [5/5] 화면 서버를 띄웠습니다. 준비를 기다립니다(끌 때: sh start.sh stop)"
i=0
while [ $i -lt 300 ]; do
  if probe; then open_url; exit 0; fi
  if [ -f logs/maintqa_web.log ] && grep -q -e '\[!\]' -e 'Traceback' logs/maintqa_web.log; then break; fi
  sleep 2; i=$((i + 1))
done
echo "  [!] 화면 서버가 준비되지 않았습니다. logs/maintqa_web.log 의 마지막 부분:"
tail -n 25 logs/maintqa_web.log 2>/dev/null
exit 1
