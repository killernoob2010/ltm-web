#!/bin/bash
# 启动生产版 (端口 8000) — 连接到 Supabase PostgreSQL
cd "$(dirname "$0")"
echo "=== 启动生产版 (8000) ==="
source .venv/bin/activate

[ -f .env ] && export $(grep -v '^#' .env | xargs)
echo "DATABASE_URL=${DATABASE_URL:0:20}... (hidden)"

uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
