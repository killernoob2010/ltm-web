#!/bin/zsh

set -euo pipefail

PROJECT_DIR="/Users/wangjingze/Documents/轻量化交易管理系统WEB"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
HOST="127.0.0.1"
PORT="8000"
URL="http://$HOST:$PORT"
LOG_DIR="$PROJECT_DIR/.runtime"
LOG_FILE="$LOG_DIR/uvicorn.log"
CHROME_APP="/Applications/Google Chrome.app"

mkdir -p "$LOG_DIR"

if [ ! -x "$PYTHON_BIN" ]; then
  osascript -e 'display alert "启动失败" message "找不到虚拟环境 Python：.venv/bin/python" as critical'
  exit 1
fi

if [ ! -d "$CHROME_APP" ]; then
  osascript -e 'display alert "启动失败" message "找不到 Google Chrome.app" as critical'
  exit 1
fi

if ! lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  cd "$PROJECT_DIR"
  nohup "$PYTHON_BIN" -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &

  for _ in {1..20}; do
    if curl -fsS "$URL" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

open -na "$CHROME_APP" --args --new-window "$URL"
