# 上海钧能批量调整开平关系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Staging 上海钧能平仓记录页面内，交付可审计的多开仓数量池与多平仓数量池原子批量调整。

**Architecture:** 在 `backend/app/trading_management.py` 中增加批量调整组查询、预览、确认和恢复函数，复用现有业务分配表、合约乘数、版本号与审计表。前端继续使用现有 `tm-page` 和右侧抽屉，只为批量矩阵增加加宽变体；矩阵显示聚合数量池，后端确定性地拆回原始事实身份。

**Tech Stack:** FastAPI、Pydantic、SQLite/PostgreSQL 兼容 SQL、原生 JavaScript、CSS、pytest、Node `node:test`、Codex 内置浏览器。

## Global Constraints

- 只修改并部署 `staging`，不合并 `main`，不操作 Production。
- 事实成交、事实平仓、事实默认分配、事实盈亏和手续费不可修改。
- 同一批量组必须同账户、合约、方向、业务归属、业务类型和策略。
- 每个平仓池必须完整分配，每个开仓池不得超量，未分配开仓继续作为持仓。
- 不新增数据库表；同一批量操作使用同一 `override_group_id` 并保留完整审计。
- 不触碰工作区现有无关修改和未跟踪文件。

---

### Task 1: 批量数量池查询契约

**Files:**
- Modify: `tests/test_trading_management.py`
- Modify: `backend/app/trading_management.py`

**Interfaces:**
- Produces: `get_business_rematch_group(close_identity_id: int, start_date: str = "", end_date: str = "") -> dict`
- Result keys: `scope`, `open_pools`, `close_pools`, `matrix`, `versions`, `fact_pnl`, `business_pnl`

- [ ] **Step 1: 写失败测试**

构造同一 RB 合约的开仓碎片 10、3、2、2、1 和平仓碎片 5、15，断言查询结果聚合为一个18手开仓池和一个20手平仓池，同时保留原始身份列表。

- [ ] **Step 2: 验证测试因函数缺失而失败**

Run: `.venv/bin/pytest tests/test_trading_management.py -k "batch_rematch_group" -v`

- [ ] **Step 3: 实现最小查询函数**

查询入口平仓事实的固定范围和当前业务配置，使用稳定业务字段生成开仓、平仓池键，返回当前业务分配聚合矩阵和每条平仓事实版本。

- [ ] **Step 4: 验证测试通过并提交**

Run: `.venv/bin/pytest tests/test_trading_management.py -k "batch_rematch_group" -v`

Commit: `feat: add business close rematch groups`

### Task 2: 批量预览、原子确认和恢复

**Files:**
- Modify: `tests/test_trading_management.py`
- Modify: `backend/app/trading_management.py`

**Interfaces:**
- Consumes: `get_business_rematch_group(...)`
- Produces: `preview_business_batch_rematch(close_identity_id, targets, versions, start_date="", end_date="") -> dict`
- Produces: `confirm_business_batch_rematch(close_identity_id, preview_token, versions, actor, reason="") -> dict`
- Produces: `restore_default_business_batch(close_identity_id, versions, actor, start_date="", end_date="", reason="") -> dict`
- `targets` shape: `{open_pool_id: {close_pool_id: quantity}}`

- [ ] **Step 1: 写300手原子交换失败测试**

构造七个开仓池合计300手和两个平仓池100/200手；交换3091×100与3171×100后，断言预览有效、确认后所有平仓事实完整分配、事实盈亏未变、业务关系正确回显。

- [ ] **Step 2: 写防错失败测试**

分别断言平仓列少1手、开仓池超量、开仓晚于平仓、版本冲突会拒绝；确认中任一写入失败时事务不产生部分结果。

- [ ] **Step 3: 写碎片和部分平仓失败测试**

断言聚合池的目标数量按日期、时间和身份顺序拆回事实；部分平仓后剩余持仓正确，范围外人工分配保持不变。

- [ ] **Step 4: 逐项验证红灯**

Run: `.venv/bin/pytest tests/test_trading_management.py -k "batch_rematch" -v`

- [ ] **Step 5: 实现预览、确认和恢复**

预览在内存中校验完整矩阵并生成一次性令牌；确认在单事务内复核全部版本、释放组内旧关系、确定性拆分目标、写入共享组号和逐平仓审计；恢复从事实默认分配重建同一范围。

- [ ] **Step 6: 运行批量和既有单条关系测试并提交**

Run: `.venv/bin/pytest tests/test_trading_management.py -k "rematch or business_close" -v`

Commit: `feat: support atomic batch close rematching`

### Task 3: FastAPI 批量接口

**Files:**
- Modify: `tests/test_trading_management.py`
- Modify: `backend/app/trading_management.py`

**Interfaces:**
- `GET /api/trading-management/business-close-groups/{close_identity_id}`
- `POST /api/trading-management/business-close-groups/{close_identity_id}/preview`
- `POST /api/trading-management/business-close-groups/{close_identity_id}/confirm`
- `POST /api/trading-management/business-close-groups/{close_identity_id}/restore-default`

- [ ] **Step 1: 扩展路由契约测试并验证失败**

断言四条路由存在，读取需要查看权限，写入需要 `trading.config` 编辑权限，Pydantic 载荷包含日期范围、版本集合、矩阵和原因。

- [ ] **Step 2: 增加请求模型和路由适配**

路由只负责权限、载荷转换和业务异常转HTTP错误，不复制分配算法。

- [ ] **Step 3: 运行路由与权限测试并提交**

Run: `.venv/bin/pytest tests/test_trading_management.py tests/test_auth_permissions.py -k "trading_management or rematch" -v`

Commit: `feat: expose batch close rematch api`

### Task 4: 测试版同页批量矩阵抽屉

**Files:**
- Modify: `tests/trading_management_frontend.test.mjs`
- Modify: `frontend/trading_management.js`
- Modify: `frontend/trading_management.css`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: Task 3 的四条接口。
- Preserves: `上海钧能台账 → 平仓记录 → 调整开平 →` 入口和现有 `tm-drawer`。

- [ ] **Step 1: 写前端契约失败测试**

断言平仓行继续使用原入口；抽屉包含开仓池×平仓池矩阵、原始碎片数、行列合计、数量错误、预览、确认、原因和恢复默认；不出现独立模拟器页面或新的一级导航。

- [ ] **Step 2: 运行 Node 测试验证失败**

Run: `node --test tests/trading_management_frontend.test.mjs`

- [ ] **Step 3: 实现矩阵渲染和状态管理**

`openRematch` 改为读取批量组；按返回池渲染数字输入；输入后本地计算行列合计，错误时禁用预览和确认；日期不合规格子禁用。

- [ ] **Step 4: 实现预览、确认、恢复和页面回显**

预览成功显示事实盈亏不变、业务盈亏变化和持仓影响；确认成功关闭抽屉并刷新当前台账；恢复默认使用批量接口并刷新。

- [ ] **Step 5: 增加抽屉加宽样式和静态资源版本**

只为批量关系抽屉增加宽度变体和矩阵样式，保留测试版颜色、字号、边框、按钮和响应式规则。

- [ ] **Step 6: 运行前端测试与语法检查并提交**

Run: `node --test tests/trading_management_frontend.test.mjs`

Run: `node --check frontend/trading_management.js`

Commit: `feat: add batch rematch matrix to junneng ledger`

### Task 5: 回归、Staging 部署和真实页面验收

**Files:**
- Modify: `版本更新记录.md`
- Modify: `docs/superpowers/plans/2026-07-22-trading-business-close-batch-rematch-implementation.md`

**Interfaces:**
- Target: branch `staging`, Render `ltm-web-staging`, Supabase `LTM WEB STAGING`。

- [ ] **Step 1: 运行完整质量门**

Run: `.venv/bin/pytest tests/test_trading_management.py tests/test_auth_permissions.py -q`

Run: `node --test tests/trading_management_frontend.test.mjs`

Run: `node --check frontend/trading_management.js && git diff --check`

- [ ] **Step 2: 备份Staging验收范围**

只备份目标RB合约与日期范围内的 `trading_business_close_allocations` 和相关审计标识；记录恢复条件，不读取或输出密钥。

- [ ] **Step 3: 推送Staging并等待真实版本生效**

提交剩余文档，推送 `staging`，确认 Render 加载本次 commit 对应静态资源版本。

- [ ] **Step 4: 浏览器执行300手全流程**

在测试版登录后进入上海钧能台账平仓记录，筛选 RB2610 七月，点击错误关系的“调整开平”，核对7个开仓池、2个平仓池、300手；完成100手交换、预览、确认和主表回显；验证数量少1手时无法确认。

- [ ] **Step 5: 恢复原关系并检查回归**

使用页面恢复默认或备份恢复原始300手关系；确认事实盈亏不变、控制台无应用错误、其它交易管理页可打开。

- [ ] **Step 6: 更新版本记录并完成Staging交付**

记录 commit、真实浏览器证据、数据库影响、恢复结果和 Production 未发布边界；任务状态更新为 `staging_delivered`。
