#!/bin/bash
# Render 启动脚本
# DATABASE_URL 在 Render 环境变量中配置
# PORT 由 Render 自动注入
cd "$(dirname "$0")"
uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
