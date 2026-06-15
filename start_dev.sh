#!/bin/bash
# 启动开发版 (端口 8001) — 连接到 Supabase PostgreSQL
cd "$(dirname "$0")"
echo "=== 启动开发版 (8001) ==="
source .venv/bin/activate

# 使用 Supabase PostgreSQL (直接连接 5432)
export DATABASE_URL='postgresql://postgres:Wrmfw123%21%40%23@db.xuwxyvafomussargruxn.supabase.co:5432/postgres'

uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
