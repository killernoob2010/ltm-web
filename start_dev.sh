#!/bin/bash
# 启动开发版 (端口 8001) — 支持热重载
cd "$(dirname "$0")"
echo "=== 启动开发版 (8001) ==="
source .venv/bin/activate

[ -f .env ] && export $(grep -v '^#' .env | xargs)
echo "DATABASE_URL=${DATABASE_URL:0:20}... (hidden)"

uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
