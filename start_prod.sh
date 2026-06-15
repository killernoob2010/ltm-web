#!/bin/bash
# 启动生产版 (端口 8000) — 连接到 Supabase PostgreSQL
cd "$(dirname "$0")"
echo "=== 启动生产版 (8000) ==="
source .venv/bin/activate

export DATABASE_URL='postgresql://postgres:Wrmfw123%21%40%23@db.xuwxyvafomussargruxn.supabase.co:5432/postgres'

uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
