# 轻量化交易管理系统 Web

这是从 Windows 桌面版迁移到 Web 版的第一版框架。

## 当前已实现

- Web 登录页。
- 主工作台布局。
- 左侧菜单保持原系统结构：台账管理、信息预警管理、后台管理。
- 后端 SQLite 数据库初始化。
- 默认管理员账号：`管理员 / admin`。
- 上海均能台账的查询、筛选、新增、编辑、删除、平仓、价格刷新、CSV 导出。
- 风险预警规则的新增、编辑、启停、删除。
- 风险预警历史查看。
- 事中风险监控的策略组和持仓接口骨架。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

## 测试版验证

- 测试版地址：`https://ltm-web-staging.onrender.com`
- 推送 `staging` 后，优先用 Codex 内置浏览器打开 `https://ltm-web-staging.onrender.com/?codex=<commit>` 做页面验证。
- 每次开始新的浏览器验证前，先关闭之前打开过的本项目测试页签，再新开干净页签测试；不要复用旧测试页签判断最新结果。
- 验证重点：页面 URL 和标题正确、控制台无应用报错、前端静态资源版本已更新（例如 `/static/app.js?v=...`、`/static/styles.css?v=...`）、目标页面功能可见可操作。
- `curl` 或 `python3 scripts/check_staging_health.py` 只作为辅助连通性检查；如果命令行外网探测失败，不应直接判定测试版部署失败，应先用内置浏览器复验。

## 后续方向

- 接入原桌面版 `risk_alert.py` 中的真实指标计算和预警扫描逻辑。
- 接入行情数据源。
- 将 SQLite 替换或迁移到 PostgreSQL/Supabase Postgres。
- 增加用户管理页面和权限管理页面。
- 逐步迁移台账模块。
