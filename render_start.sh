#!/bin/bash
# Render 启动脚本：Web 是可用性边界，Agent 由同实例监督器按开关启动。
# DATABASE_URL、AGENT_V2_* 在 Render 环境变量中配置；PORT 由 Render 自动注入。
set -u
cd "$(dirname "$0")"
exec python3 -m backend.app.trading_agent.runtime
