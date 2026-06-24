# 给执行模型的说明

你将接手"轻量化交易管理系统 Web" staging 环境的验证工作。
按本文执行，不要自行扩大范围。

## 当前状态

- **分支**: `staging`（已推送到 origin/staging，Render 自动部署中）
- **最新 commit**: `d1ec344` — 修复实时信息汇总自动刷新
- **Staging 网址**: `https://ltm-web-staging.onrender.com/`
- **工作目录**: `/Users/wangjingze/Documents/轻量化交易管理系统WEB`

## 这一批 commit 做了什么（`9506c7e` → `d1ec344`，共 15 个 commit）

四个功能块：

### 1. 盈亏颜色调整
按国内交易习惯，盈利/正数显示红色（`var(--danger)`），亏损/负数显示绿色（`#087443`）。
CSS 加了版本参数破坏浏览器旧缓存。
**已验证通过**，不需要再动。

### 2. 年月默认显示规则
- 新增「掉期月差」指标，放在「月差」下方
- 月差和掉期月差默认年月复原为动态规则（如 2026-09 / 2027-01）
- 螺矿比和盘面钢厂利润月份下拉仅显示 01/05/09
**已验证通过**，不需要再动。

### 3. 缓存回填机制
新增完整的「实时信息汇总」历史缓存回填：
- `backend/app/info_summary_backfill.py` — 统一回填服务（Sina 历史行情拉取、增量写入、滚动计算、自动清理 >210 天数据）
- `backend/app/cache_service.py` — 数据库层（`save_daily_prices_batch`、`get_prices_for_info_contracts`、`get_existing_calculated_dates` 等）
- `backend/app/main.py` — API 路由 `GET /api/info-summary/cache/status` + `POST /api/info-summary/cache/backfill`
- 前端新增「刷新历史缓存」按钮和状态展示
- 支持增量回填（跳过 DB 已有日期）、幂等写入、自动清理过期数据
**std 修复**：`get_existing_calculated_dates()` 加了 `AND std_value IS NOT NULL`，防止 std 为 NULL 的行被误判为"已存在"而跳过。修复了煤矿比和盘面钢厂利润标准差永远 `--` 的 bug。
**已验证通过**（18 个测试全通过），不需再动。

### 4. 自动刷新修复 ← 本轮新增
「实时更新：开启」之前是硬编码文本，实际没有定时器。新增：
- `startInfoSummaryAutoRefresh()` — 每 30 秒从缓存重新计算全部指标
- `stopInfoSummaryAutoRefresh()` — 切走模块时停止
- 进入 `info_summary` 模块时启动，切走时停止
**未验证**，换模型后需要验证。

## 验证步骤（换模型后必须执行）

1. 打开 `https://ltm-web-staging.onrender.com/`，登录（admin / admin）
2. 进入「信息预警管理 → 实时信息汇总」
3. 观察状态栏初始显示「页面已加载并完成计算」
4. 等待 30 秒，确认状态栏变为「自动刷新完成」
5. 检查各指标的标准差列（尤其煤矿比和盘面钢厂利润），确认有值而不是全部 `--`
6. 如还有 `--`，点击「刷新历史缓存」触发回填

## 已知限制（不需要碰）

- **FE 铁矿石历史数据**：新浪不提供新加坡交易所数据。掉期月差仅展示今日值（实时价差），历史统计为 `--`。
- **内外盘差/内外盘差2 历史回填**：尚未实现，需 FE 历史 + 汇率历史两个数据源就绪。
- **Phase 3 定时自动回填**（每日收盘后自动执行）：尚未实现，目前仅手动点击「刷新历史缓存」触发。

## 关键文件

| 文件 | 作用 |
|------|------|
| `frontend/app.js` | 前端全部逻辑（自动刷新、缓存状态、指标计算） |
| `frontend/index.html` | 页面结构（刷新历史缓存按钮等） |
| `frontend/static/styles.css` | 样式（盈亏颜色 `.numeric-up` / `.numeric-down`） |
| `backend/app/info_summary_backfill.py` | 缓存回填服务 |
| `backend/app/cache_service.py` | 数据库读写层 |
| `backend/app/main.py` | API 路由 + 信息汇总计算逻辑 |
| `docs/superpowers/specs/2026-06-23-info-summary-cache-backfill-design.md` | 需求文档 |
| `版本更新记录.md` | 完整版本历史 |
