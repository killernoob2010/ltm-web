# 轻量化交易管理系统 Web

这是从 Windows 桌面版迁移到 Web 版的第一版框架。

## 当前已实现

- Web 登录页。
- 主工作台布局。
- 左侧菜单保留旧台账管理，并新增独立一级菜单“交易管理”。
- 后端 SQLite 数据库初始化。
- 默认管理员账号：`管理员 / admin`。
- 上海钧能台账的查询、筛选、新增、编辑、删除、平仓、价格刷新、CSV 导出。
- 风险预警规则的新增、编辑、启停、删除。
- 风险预警历史查看。
- 事中风险监控的策略组和持仓接口骨架。
- 订单融资管理：从本地订单融资 Excel 台账导入合同、融资、信用证、交单、收汇、还款和额度数据，提供 `订单融资进度` 与 `融资资金监控` 两个页面。
- 用户与权限管理：独立登录账号、用户/领导/管理员类型、部门默认权限、个人例外、查看/日常/敏感操作分级、自助改密、管理员重置和账号停用。
- 交易管理：文华成交/平仓/期末持仓三表完整预检和版本覆盖、只读事实总览、持仓与交易、整笔业务归属、上海钧能台账、全量期权台账、可审计的业务开平重配和恢复默认。首版浮动盈亏与期权风险指标统一显示“待计算”，汇总与导出保留入口暂不执行导出。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

如需只连接本地 SQLite 试用，避免读取 `.env` 中的云端 `DATABASE_URL`：

```bash
env -u DATABASE_URL .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --reload
```

打开：

```text
http://127.0.0.1:8000
```

如果使用上面的本地 SQLite 试用命令，则打开：

```text
http://127.0.0.1:8001
```

## 操作日志保留与归档

- 在线日志默认每次加载 100 条，使用游标分页；页面只在管理员打开操作日志时请求数据。
- 在线保留规则为最近 12 个月，并以 20 万条作为软上限；只归档已经结束的完整自然月。
- 归档文件为 gzip NDJSON，存放在 Supabase 私有 bucket `operation-log-archives`。只有管理员主动下载历史归档时才读取文件。
- 正式归档需要在服务端配置 `SUPABASE_URL` 和 `SUPABASE_SERVICE_ROLE_KEY`；service-role 不得进入前端、仓库、日志或接口响应。

归档命令默认只预览，不写数据库或 Storage：

```bash
.venv/bin/python scripts/archive_operation_logs.py --environment staging
```

确认 dry-run 后才使用 `--apply`。恢复命令同样默认只预览，必须显式传入归档 ID 和 `--apply`：

```bash
.venv/bin/python scripts/restore_operation_logs.py 1
.venv/bin/python scripts/restore_operation_logs.py 1 --apply
```

归档过程先上传并校验文件，再在数据库事务中写入元数据和删除对应在线日志；校验失败、删除行数不一致或恢复 ID 冲突都会停止并回滚数据库写入。当前没有自动创建 Render Cron，是否增加付费定时服务需单独确认。

订单融资 Excel 台账当前默认读取本机新模板文件，包含 2025 和 2026 全部项次：

```text
/Users/wangjingze/建龙/贸易处/YOLANDA和香港建龙出口钢材信用证台账.xlsx
```

导入时只读取 `订单`、`额度`、`预警` 三个页签：`订单`是唯一订单事实来源，`额度`提供银行授信与占用，`预警`按项次关联风险提示。工作簿中的其他页签全部忽略，不参与字段补全或状态判断。网页仍保持“订单融资进度”和“融资资金监控”两个页面。

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
- 逐步迁移台账模块。
