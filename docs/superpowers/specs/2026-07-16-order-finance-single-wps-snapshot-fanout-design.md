# 订单融资单一 WPS 授权双环境快照同步设计

## 1. 目标与已确认选择

同一份 WPS 订单融资文档继续使用已经审批通过的企业自建应用和现有用户授权。Production 与 Staging 保持两个隔离数据库，按每天北京时间 09:00、17:00 两个业务时点取得同一份 WPS 事实快照；两边不得同时刷新同一个可轮换 refresh token，也不得互相连接对方数据库。

采用“Production 唯一读取 WPS，Staging 通过受保护的内部快照接口跟随”的方案：

```text
同一份 WPS 文档
        ↓ 现有应用、现有用户授权，只在一处刷新 token
Production WPS 同步 → Production 数据库
        ↓ 鉴权的只读事实快照
Staging 快照跟随 → Staging 数据库
```

不采用以下方案：

- 不新建测试版 WPS 应用，避免重复申请权限和管理员审批。
- 不要求第二个 WPS 用户，避免额外账号和文档授权依赖。
- 不把同一个 refresh token 同时配置给两个 Render 服务，避免轮换后互相使旧 token 失效。
- 不建设第三个付费同步服务，不让任何服务同时持有两套数据库连接。

本设计定向替代 `2026-07-16-order-finance-post-document-risk-dual-db-wps-sync-design.md` 第 4.2、4.3、5、6.2 节中“两边分别读取 WPS、分别持有 refresh token”的内容；其余风险、字段覆盖、缩量确认和失败保护规则继续有效。

## 2. 角色与配置边界

### 2.1 Production：WPS 源端

- 继续使用现有 `WPS_APP_ID`、`WPS_APP_SECRET`、`WPS_USER_REFRESH_TOKEN`、drive ID 和 file ID。
- 继续在 09:00、17:00 读取 WPS，刷新并加密保存轮换后的 refresh token。
- WPS 同步成功并事务性写入正式数据库后，向内部快照接口提供当前有效的 WPS 事实记录。
- 不持有 Staging 的 `DATABASE_URL`，不直接写测试数据库。

### 2.2 Staging：快照跟随端

- 不调用 WPS token、元数据或文件下载接口，不参与 refresh token 轮换。
- 在相同的 09:00、17:00 业务时点开始检查 Production 最新快照；如果源端本时点尚未完成，则每 5 分钟重试，直到取得该时点或更新版本。
- 取得完整快照并通过校验后，只通过现有 `apply_order_finance_snapshot` 事务写入 Staging 数据库。
- 不持有 Production 的 `DATABASE_URL`，不访问正式 Supabase SQL 或 Data API。

### 2.3 模式与秘密配置

新增服务端配置名称：

- `ORDER_FINANCE_SYNC_MODE`：仅允许 `wps_source` 或 `snapshot_follower`。
- `ORDER_FINANCE_SNAPSHOT_SHARED_SECRET`：源端和跟随端共享的高强度随机秘密，只放 Render Secret。
- `ORDER_FINANCE_SNAPSHOT_UPSTREAM_URL`：仅跟随端配置，指向 Production 内部快照接口。

Production 使用 `wps_source`；Staging 使用 `snapshot_follower`。启动时如果模式所需配置不完整，同步调度不启动并写脱敏错误日志。秘密、Authorization 头和快照正文不得进入日志、前端或版本记录。

## 3. 内部快照契约

### 3.1 接口

源端提供服务间只读接口：

```text
GET /api/internal/order-finance/snapshot
Authorization: Bearer <ORDER_FINANCE_SNAPSHOT_SHARED_SECRET>
```

使用常量时间比较验证 Bearer token。缺失或错误凭据统一返回 404，避免公开确认接口和数据存在；接口不使用普通网页用户会话，不向浏览器暴露共享秘密。

### 3.2 响应

响应只包含同步所需内容：

- `schema_version`：固定为 `1`。
- `source_version`：最近成功 WPS 文件版本或哈希。
- `source_success_at`：源端最近成功同步时间。
- `facts_hash`：对排序后的业务键和全部事实字段做确定性 SHA-256。
- `record_count`：有效 WPS 事实记录数。
- `records`：当前未归档、来源不是“手动新增”的记录，字段严格限制为现有 `FACT_FIELDS`。

不包含数据库 ID、创建/更新时间、用户、权限、会话、操作日志和管理字段。`planned_drawdown_date`、`repayment_requirement`、`next_action`、`manager_note`、人工装船确认及其审计信息都不进入快照。

### 3.3 确定性与完整性

- 记录按 `business_key` 排序后输出。
- `facts_hash` 使用稳定 JSON 序列化，字段顺序固定，空值语义固定。
- 跟随端验证 schema、源版本、记录数、非空业务键、业务键唯一性和 hash。
- 任一验证失败时不写数据库，不覆盖 Staging 上次成功状态。

## 4. 调度、重试与幂等

- 两个环境的业务时点均为北京时间 09:00、17:00，包括周末和节假日。
- Production 在时点到达后执行一次 WPS 同步；现有同 slot 去重和缩量两次确认机制保持不变。
- Staging 在时点到达后请求快照。若 `source_success_at` 早于当前 slot，返回“等待源端”并保留该 slot 未完成；后台每 5 分钟重试。
- 跟随成功后才把 Staging 的当前 slot 标记为已完成。同一个 `source_version + facts_hash` 重复跟随时变化条数为 0。
- Production 的 WPS 同步成功不依赖 Staging 可用性；Staging 暂时失败不影响 Production，下次轮询自动补齐。
- 服务重启后继续补做当天最近一个未完成时点，不重复应用已经成功的源版本。

## 5. 写库与冲突边界

- 两个环境继续使用各自的 `DATABASE_URL` 和 `order_finance_sync_status`。
- 跟随端复用现有事实字段事务比较：新增、事实更新、归档或无变化。
- Staging 本地管理字段不参与事实比较，也不会被 Production 快照覆盖。
- Staging 手动新增记录不进入自动归档范围。
- 快照业务键缩量继续执行现有“两次相同候选才允许归档”保护；第一次只登记待确认状态。
- 正式和测试环境允许存在不同的管理字段、测试记录、用户、权限和日志；“数据一致”只指相同源版本下的 WPS 事实记录与 `facts_hash` 一致。

## 6. 安全与失败处理

- 内部接口只读，不提供写 Production 的方法。
- 请求和响应均使用 HTTPS；共享秘密至少 32 字节随机值，并可单独轮换。
- 快照接口不允许通过查询参数传秘密，不在异常中返回内部配置。
- 上游 401/403/404、超时、非 JSON、schema 不匹配、hash 不匹配、记录为空或数据库失败，均视为跟随失败并保留原数据。
- 日志只记录 `stage`、HTTP 状态、源版本、记录数和异常类型，不记录 token、完整 URL 参数或业务正文。
- 不新增 Supabase 公共表、视图、函数或 Data API 权限，不改变现有 RLS 边界。

## 7. 验收标准

### 7.1 自动化

- `wps_source` 模式只启动现有 WPS 客户端和调度；`snapshot_follower` 模式不构造 WPS 客户端。
- 内部接口缺少或使用错误秘密时不返回快照；正确秘密只返回 FACT_FIELDS。
- 快照不含管理字段、数据库 ID 或用户数据。
- 跟随端拒绝空记录、重复/空业务键、错误记录数、错误 hash 和旧于当前 slot 的快照。
- 跟随相同版本为 0 变化；新版本事务性更新事实并保留 Staging 管理字段。
- 首次缩量不归档，第二次相同源版本和 hash 才允许归档。
- 上游或写库失败不修改现有记录和最近成功状态。

### 7.2 Staging 真实验收

- Staging 使用 `snapshot_follower`，自动同步日志中不出现 WPS token 刷新或 WPS 下载调用。
- 在 Staging 自回环验收配置下，受保护接口、鉴权、下载、校验、事务应用和页面同步状态完整通过；错误秘密和损坏 hash 均被拒绝。
- 页面仍能加载订单融资进度和融资资金监控，当前 70 条活动记录不减少，管理字段不变化，控制台无新增错误。

### 7.3 Production Gate B 后联合验收

- Production 仍能使用现有应用和现有授权完成 WPS 同步，不新增管理员审批。
- Staging 改为读取 Production 快照后，两边最近成功 `source_version`、`record_count` 和 `facts_hash` 一致。
- 连续观察一个 09:00 或 17:00 时点：Production 先完成 WPS，同一业务时点内 Staging 自动跟随；任一端失败不破坏另一端已有数据。

## 8. D/T/R/C 与 Gate A

- **D3**：变更局限于订单融资单一业务模块，覆盖该模块的 WPS 适配器、调度、内部 API、事务写入和同步状态；不跨交易管理、期现或用户权限业务模块。不是 D4，因为没有系统级数据总线或跨多个业务域。
- **T3**：需要覆盖源端快照、鉴权、跟随校验、持久化、同步状态和真实 Staging 页面。不是 T4，因为没有跨多个独立业务模块的端到端流程。
- **R2（Staging）/R3（Production）**：Staging 代码和测试数据可回滚；Production 涉及正式同步链路、秘密配置和正式事实数据，必须单独 Gate B、备份和冒烟验证。
- **C1**：一个主 Agent 可在单仓库内完成；不增加多 Agent，不做深度安全扫描或全系统重构。

未改变区域：订单融资风险口径、页面展示、额度计算、人工管理字段含义、用户权限、其他业务模块、WPS 文件内容和 WPS 应用权限。

## 9. 实施与验证边界

实施任务必须逐项映射到：模式路由、事实快照导出、鉴权接口、跟随客户端、slot 重试、事务应用、配置说明、自动化测试和真实 Staging 验收。采用测试先行，每项行为先看到失败测试再写最小实现。

本轮 Gate A 授权仅覆盖：修改代码、本地测试、提交并推送 `staging`、部署 Render Staging、配置仅用于 Staging 的非生产秘密和自回环验收。禁止操作 `main`、Production Render、正式 Supabase、正式环境变量和正式数据；进入这些动作前必须提交 Gate B。
