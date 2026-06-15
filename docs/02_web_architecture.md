# Web 版目标架构设计

## 1. 设计目标

Web 版不是简单把 exe 套进浏览器，而是把现有桌面程序拆成三层：

1. 前端页面：负责浏览器交互、表格、表单、筛选、导出入口、通知。
2. 后端服务：负责认证权限、业务规则、行情、预警、导出、备份。
3. 数据层：负责统一数据库访问、事务、审计字段、迁移脚本。

菜单层级保持现有结构：

- 台账管理
- 信息预警管理
- 后台管理

## 2. 推荐技术路线

后端建议：Python + FastAPI

原因：

- 当前业务逻辑主要是 Python，实现迁移时可逐步抽离复用。
- FastAPI 适合提供结构清晰的 REST API，并能自动生成接口文档。
- 后续可接入后台任务、权限中间件、数据库 ORM 和导出服务。

前端建议：React 或 Vue

建议优先 React + TypeScript，原因：

- 适合复杂表格、筛选、弹窗、状态切换和后台管理页面。
- TypeScript 能减少字段名和接口参数错误。
- 后续可接入成熟表格组件、权限路由、状态管理和图表。

数据库策略：

- 当前阶段先完成数据结构设计。
- 开发初期可用 SQLite 快速验证。
- 面向多人长期使用时，建议落到 PostgreSQL 或 MySQL。
- 数据模型应按 PostgreSQL/MySQL 的约束、索引、时间字段、事务能力来设计，避免被 SQLite 的宽松类型限制住。

## 3. 运行方式

目标运行方式：

1. 在内网服务器或指定主机上运行后端服务。
2. 后端连接统一数据库。
3. 前端作为静态页面部署，或由后端统一托管。
4. 用户通过浏览器访问系统地址。
5. 所有用户共享同一套权限、数据、日志和备份。

示例部署形态：

```text
浏览器
  |
  | HTTPS/HTTP
  v
Web 前端
  |
  | REST API / WebSocket
  v
FastAPI 后端
  |
  | ORM / SQL
  v
PostgreSQL/MySQL
  |
  +-- 文件存储：导出文件、备份文件、导入文件
  +-- 后台任务：行情刷新、预警扫描、自动备份
```

## 4. 后端模块划分

建议后端目录按业务域组织：

```text
backend/
  app/
    main.py
    core/
      config.py
      security.py
      database.py
      permissions.py
    auth/
      routes.py
      service.py
      schemas.py
    users/
      routes.py
      service.py
      models.py
      schemas.py
    ledgers/
      sh_junneng/
      steel_export/
      subsidiary/
      options/
    alerts/
      info_summary/
      risk_alert/
      mid_event/
    market_data/
      service.py
      providers/
    exports/
      service.py
    backups/
      service.py
    audit/
      service.py
    tasks/
      scheduler.py
```

### 4.1 认证和权限

职责：

- 登录、登出、刷新会话。
- 获取当前用户。
- 按模块校验查看和编辑权限。
- 管理员默认拥有全部权限。
- 写入操作日志。

推荐 API：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | 登录 |
| `POST` | `/api/auth/logout` | 登出 |
| `GET` | `/api/auth/me` | 当前用户 |
| `GET` | `/api/auth/modules` | 当前用户可见菜单 |
| `GET` | `/api/users` | 用户列表 |
| `POST` | `/api/users` | 新增用户 |
| `PUT` | `/api/users/{id}` | 编辑用户 |
| `DELETE` | `/api/users/{id}` | 删除用户 |
| `PUT` | `/api/users/{id}/permissions` | 设置模块权限 |
| `GET` | `/api/audit/logs` | 操作日志 |

### 4.2 台账 API

台账 API 保持业务模块边界，避免一开始过度抽象。

上海均能台账：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/ledgers/sh-junneng/trades` | 查询交易 |
| `POST` | `/api/ledgers/sh-junneng/trades` | 新增交易 |
| `PUT` | `/api/ledgers/sh-junneng/trades/{id}` | 编辑交易 |
| `DELETE` | `/api/ledgers/sh-junneng/trades/{id}` | 删除交易 |
| `POST` | `/api/ledgers/sh-junneng/trades/{id}/close` | 平仓 |
| `POST` | `/api/ledgers/sh-junneng/prices/refresh` | 刷新价格 |
| `GET` | `/api/ledgers/sh-junneng/export` | 导出 |

钢材出口套保和子公司套保：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/ledgers/{ledger_type}/orders` | 挂单列表 |
| `GET` | `/api/ledgers/{ledger_type}/positions` | 持仓列表 |
| `GET` | `/api/ledgers/{ledger_type}/settled` | 已平仓列表 |
| `POST` | `/api/ledgers/{ledger_type}/orders` | 新增开仓或平仓挂单 |
| `POST` | `/api/ledgers/{ledger_type}/orders/{id}/confirm-open` | 确认开仓成交 |
| `POST` | `/api/ledgers/{ledger_type}/orders/{id}/confirm-close` | 确认平仓成交 |
| `PUT` | `/api/ledgers/{ledger_type}/trades/{id}` | 编辑开仓记录 |
| `PUT` | `/api/ledgers/{ledger_type}/close-trades/{id}` | 编辑平仓记录 |
| `DELETE` | `/api/ledgers/{ledger_type}/trades/{id}` | 删除交易 |
| `GET` | `/api/ledgers/{ledger_type}/overview` | 汇总概览 |
| `GET` | `/api/ledgers/{ledger_type}/export` | 导出 |

其中 `ledger_type` 第一版限定为：

- `steel-export`
- `subsidiary`

期权交易：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/options/trades` | 查询交易 |
| `POST` | `/api/options/trades` | 新增交易 |
| `PUT` | `/api/options/trades/{id}` | 编辑交易 |
| `DELETE` | `/api/options/trades/{id}` | 删除交易 |
| `POST` | `/api/options/trades/{id}/close` | 平仓 |
| `POST` | `/api/options/trades/{id}/greeks` | 更新 Greeks |
| `GET` | `/api/options/quotes` | 获取行情 |
| `GET` | `/api/options/export` | 导出 |

### 4.3 信息预警 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/info-summary/indicators` | 指标列表和最新值 |
| `POST` | `/api/info-summary/calculate` | 手动计算指标 |
| `GET` | `/api/info-summary/history` | 历史计算结果 |
| `GET` | `/api/risk-alert/settings` | 预警规则 |
| `POST` | `/api/risk-alert/settings` | 新增预警 |
| `PUT` | `/api/risk-alert/settings/{id}` | 编辑预警 |
| `POST` | `/api/risk-alert/settings/batch-enable` | 批量启用 |
| `POST` | `/api/risk-alert/settings/batch-disable` | 批量停用 |
| `DELETE` | `/api/risk-alert/settings/{id}` | 删除预警 |
| `GET` | `/api/risk-alert/history` | 预警历史 |
| `GET` | `/api/mid-event/groups` | 策略组 |
| `POST` | `/api/mid-event/groups` | 新增策略组 |
| `GET` | `/api/mid-event/groups/{id}/positions` | 策略组持仓 |
| `POST` | `/api/mid-event/groups/{id}/positions` | 新增策略持仓 |
| `POST` | `/api/mid-event/prices/refresh` | 刷新价格 |

### 4.4 后台任务

桌面版中的 `threading` 和 `after()` 应替换为后端任务：

| 任务 | 触发方式 | 结果 |
| --- | --- | --- |
| 行情刷新 | 定时或手动 | 更新价格缓存 |
| 风险预警扫描 | 定时 | 写入 `alert_history` 并推送通知 |
| 信息指标计算 | 定时或手动 | 写入 `calculated_data` |
| 自动备份 | 定时 | 生成备份文件和元数据 |
| 导出文件清理 | 定时 | 删除过期临时文件 |

第一版可以使用 APScheduler。任务变复杂后，再考虑 Celery 或其他任务队列。

## 5. 前端页面结构

建议前端保持工作台式布局，不做营销页：

```text
登录页
主框架
  顶部栏：系统名、当前用户、通知、退出
  左侧菜单
    台账管理
    信息预警管理
    后台管理
  内容区
    列表页
    表单弹窗
    详情抽屉
    汇总区
```

### 5.1 通用页面能力

- 表格分页、排序、筛选。
- 批量操作。
- 新增、编辑、删除、确认。
- 数据导出。
- 权限驱动按钮显示。
- 操作成功和失败通知。
- 表单校验。
- 操作日志可追溯。

### 5.2 权限呈现规则

- 无查看权限：菜单不可见，直接访问接口返回 403。
- 有查看无编辑：页面可查看，新增、编辑、删除、确认成交、恢复备份等按钮禁用或隐藏。
- 管理员：所有菜单和操作可见。

## 6. 数据层设计原则

- 所有表增加统一审计字段：`created_by`、`created_at`、`updated_by`、`updated_at`。
- 关键业务删除优先软删除，保留历史可追溯。
- 金额、价格、汇率字段在数据库中使用定点数类型，避免浮点误差。
- 所有写操作走服务层，不允许页面直接拼接 SQL。
- 业务状态使用受控枚举，例如 `准备挂单`、`挂单中`、`已成交`、`部分平仓`、`已平仓`。
- 后端服务负责计算字段，前端只展示计算结果。

## 7. 迁移顺序建议

1. 建立 Web 项目骨架和登录权限。
2. 迁移用户、权限、操作日志。
3. 迁移上海均能台账，验证最小闭环：新增、编辑、平仓、查询、导出。
4. 迁移钢材出口套保和子公司套保。
5. 迁移期权交易台账和 Greeks 计算。
6. 迁移信息汇总、风险预警、事中监控。
7. 迁移后台数据管理、备份恢复、导入导出。

这个顺序的理由是先把权限和一个台账跑通，再迁移结构相似的复杂台账，最后处理后台任务和实时能力。
