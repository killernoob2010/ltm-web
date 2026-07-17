# Iron Ore Basis Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一份覆盖 2024-01-01 至提取日、可追溯计算明细和参数来源的铁矿石港口基差基础数据库 Excel。

**Architecture:** 从已登录 EBC 页面只读接口导出港口品牌湿吨价格快照，从项目现有新浪历史行情实现获取 `I0` 主力连续每日收盘价，再用年度矿山参数和 I2312/F-DCE I004-2021 规则生成日度长表。最终由 `@oai/artifact-tool` 生成四页签工作簿，公式列保留完整计算链路，展示页只保留精简字段。

**Tech Stack:** Chrome/EBC 只读 API、Node.js、`@oai/artifact-tool`、项目现有 `SinaHistoryProvider`、Excel `.xlsx`。

## Global Constraints

- 只生成线下 Excel，不修改 Web、数据库、Staging 或 Production。
- 现货起始日期为 `2024-01-01`，只计算 EBC 现货与 `I0` 主力连续收盘价同时存在的交易日。
- 多规格 EBC 价格只取高品位规格；质量参数全部取相应年度 Excel，EBC 名称 Fe 不参与升贴水。
- 乌克兰精粉固定使用 Fe 65.54%、SiO2 7.50%、Al2O3 0.11%、P 0.07%、S 0.03%、H2O 9.00%。
- S 为空按 0；PB粉 P 的 `0.11` 按 0.11% 转成 `0.0011`；卡拉拉精粉使用保证值。
- 昆巴粉价格取 EBC 南非粉 63%Fe、参数取年度 Excel 昆巴粉；杨迪粉价格取 EBC 56.8%Fe、参数取年度 Excel 58.3%。
- 品牌升贴水使用 I2312：卡拉加斯粉、BRBF、PB粉为 `+15`，其余目标品种为 `0`。
- Fe 调整参数 `X=1.5`，规则版本记录为 `I2312 / F-DCE I004-2021`。
- 缺失港口/品种/日期不补零、不插值、不前向填充。
- 工作簿作者脚本只使用加载器提供的 Node.js 与 `@oai/artifact-tool`，在独立临时目录运行。

---

### Task 1: 固化原始输入快照

**Files:**
- Create: `/Users/wangjingze/Documents/轻量化交易管理系统WEB/outputs/019f58fd-4754-7ae2-8ebd-c3321fe0dd7f/raw/ebc_port_prices_2024_onward.json`
- Create: `/Users/wangjingze/Documents/轻量化交易管理系统WEB/outputs/019f58fd-4754-7ae2-8ebd-c3321fe0dd7f/raw/i0_daily_close_2024_onward.json`
- Create: `/Users/wangjingze/Documents/轻量化交易管理系统WEB/outputs/019f58fd-4754-7ae2-8ebd-c3321fe0dd7f/raw/input_manifest.json`

**Interfaces:**
- Consumes: EBC `queryIndexData` 已提取的 8 港口指标与日度宽表；`SinaHistoryProvider.history("I0", since_date="2024-01-01")`。
- Produces: UTF-8 JSON 快照；EBC JSON 结构为 `{港口: {indicators: [...], data: [...]}}`，期货 JSON 为 `{YYYY-MM-DD: close}`。

- [ ] **Step 1: 导出 EBC 快照**

在已登录页面的只读浏览器会话中，将已取得的 `allPortData4` 写入目标 JSON；导出前断言包含 8 个港口、122 条已选指标和 73,624 个非空原始值。

- [ ] **Step 2: 导出主力连续收盘价**

Run:

```bash
.venv/bin/python -c 'import json; from backend.app.info_summary_backfill import SinaHistoryProvider; print(json.dumps(SinaHistoryProvider().history("I0", since_date="2024-01-01"), ensure_ascii=False))'
```

Expected: JSON 首日为 `2024-01-02`，最新日不晚于当前日期，价格均为正数。

- [ ] **Step 3: 写入输入清单并校验哈希**

`input_manifest.json` 保存提取时间、日期范围、港口数、指标数、EBC 值数、期货交易日数、原质量参数工作簿绝对路径和三个输入文件 SHA-256；不保存登录凭证、Cookie 或 Token。

- [ ] **Step 4: 运行输入完整性检查**

Expected:

```text
ports=8
spot_start=2024-01-02
spot_end=2026-07-13
futures_start=2024-01-02
futures_rows>=600
```

### Task 2: 建立年度参数和指标映射

**Files:**
- Create: `/tmp/codex-iron-ore-basis-019f58fd/build_iron_ore_basis_workbook.mjs`
- Create: `/tmp/codex-iron-ore-basis-019f58fd/node_modules` symlink to bundled dependencies

**Interfaces:**
- Consumes: 矿山参数工作簿的 2024、2025、2026 年页签，EBC 指标元数据。
- Produces: `parameterRows`、`indicatorMappings`、`qualityNotes` 三个内存结构。

- [ ] **Step 1: 导入工作簿并按年度读取目标参数**

使用：

```js
const source = await FileBlob.load(parameterWorkbookPath);
const parameterWorkbook = await SpreadsheetFile.importXlsx(source);
```

逐年定位表头中的 `FE/SIO2/Al2O3/P/S/H2O`，按品名和矿山组合识别 15 个目标品种。卡拉拉通过矿山“澳洲卡拉拉”识别，昆巴通过“昆巴粉（南非）”识别，FMG“混合粉”标准化为“FMG混合粉”。

- [ ] **Step 2: 应用参数覆盖规则**

实现：

```js
function normalizeParameter(product, year, raw) {
  if (product === "乌克兰精粉") return { fe: 0.6554, si: 0.075, al: 0.0011, p: 0.0007, s: 0.0003, h2o: 0.09, parameterType: "业务确认典型值" };
  return {
    ...raw,
    p: product === "PB粉" && raw.p === 0.11 ? 0.0011 : raw.p,
    s: raw.s == null ? 0 : raw.s,
    parameterType: product === "卡拉拉精粉" ? "保证值" : "典型值",
  };
}
```

- [ ] **Step 3: 选择 EBC 高品位价格指标**

映射必须输出 `canonicalProduct`、`sourceIndicatorCode`、`sourceIndicatorLabel`、`sourcePort`、`priceSpecFe`、`mappingNote`。同品种多规格选择：BRBF 63%Fe、金布巴粉 60.3%Fe、卡拉拉最高 Fe；昆巴粉使用南非粉 63%Fe；杨迪粉使用 56.8%Fe 价格指标。

- [ ] **Step 4: 校验覆盖和缺失组合**

断言每个已选指标只映射一个标准品种；输出 8 港口 × 15 品种覆盖矩阵，缺失组合进入 `qualityNotes`，不得生成零值记录。

### Task 3: 实现 I2312 质量调整与基差计算

**Files:**
- Modify: `/tmp/codex-iron-ore-basis-019f58fd/build_iron_ore_basis_workbook.mjs`

**Interfaces:**
- Consumes: `parameterRows`、高品位 EBC 日度价格、`I0` 收盘价。
- Produces: `calculationRows`，每行对应一个日期、港口、标准品种。

- [ ] **Step 1: 实现五项质量升贴水函数**

函数签名：

```js
qualityAdjustments({ fe, si, al, p, s, x = 1.5 })
// => { feAdj, siAdj, alAdj, pAdj, sAdj, total, valid, validationNote }
```

规则：

```text
Fe 60.0%-63.5%：相对61%，每0.1%乘X
Fe 56.0%-60.0%：60%以下每0.1%乘(X+1.5)，并累计60%-61%扣价
Fe >63.5%：63.5%以上每0.1%乘(X+1.0)，并累计61%-63.5%升价
Si <4.5%：每低0.1%升0.5；4.5%-6.5%每高0.1%扣1.0；6.5%-8.5%每高0.1%扣1.5并累计
Al <1.0%按1.0%计；1.0%-2.5%每低0.1%升2.0；2.5%-3.5%每高0.1%扣3.0
P 0.10%-0.12%每高0.01%扣10；0.12%-0.15%每高0.01%扣15并累计
S 0.03%-0.10%每高0.01%扣1；0.10%-0.20%每高0.01%扣5并累计
```

质量升价为正、扣价为负；同时检查 Fe≥56%、Si≤8.5%、Al≤3.5%、P≤0.15%、S≤0.20%、Si+Al≤10%。

- [ ] **Step 2: 写入代表性单元测试断言**

```js
assert.equal(qualityAdjustments({fe:0.625,si:0.045,al:0.025,p:0.001,s:0.0003}).feAdj, 22.5);
assert.equal(qualityAdjustments({fe:0.63,si:0.045,al:0.025,p:0.001,s:0.0003}).feAdj, 30);
assert.equal(qualityAdjustments({fe:0.61,si:0.075,al:0.025,p:0.001,s:0.0003}).siAdj, -35);
assert.equal(qualityAdjustments({fe:0.61,si:0.045,al:0.0345,p:0.0012,s:0.0002}).alAdj, -28.5);
```

- [ ] **Step 3: 生成日期交集和计算行**

只处理 `spotDate in futuresCloseByDate`；公式口径：

```text
drySpot = wetSpot / (1 - h2o)
standardizedSpot = drySpot - qualityAdjustment - brandAdjustment
basis = standardizedSpot - futuresClose
```

为每行生成 ISO 日期、年份、ISO 周次、周次标签、原指标信息、年度参数、五项调整、质量合计、品牌调整、标准化现货价、`I0`收盘价、基差和状态。

- [ ] **Step 4: 运行计算质量检查**

检查业务唯一键 `date+port+product+ruleVersion+parameterVersion` 无重复；所有价格为正；无非交易日；无缺失参数行；质量无效行保留在计算明细并标记，但不进入精简期现数据。

### Task 4: 生成和格式化基础数据库 Excel

**Files:**
- Modify: `/tmp/codex-iron-ore-basis-019f58fd/build_iron_ore_basis_workbook.mjs`
- Create: `/Users/wangjingze/Documents/轻量化交易管理系统WEB/outputs/019f58fd-4754-7ae2-8ebd-c3321fe0dd7f/铁矿石港口基差基础数据库_2024至今.xlsx`

**Interfaces:**
- Consumes: `calculationRows`、`parameterRows`、`qualityNotes`、`input_manifest.json`。
- Produces: 四页签最终工作簿。

- [ ] **Step 1: 创建四个页签**

按顺序创建：`期现数据`、`计算明细`、`参数表`、`数据质量说明`。删除默认空白页，不增加图表或导出按钮。

- [ ] **Step 2: 写入精简和明细长表**

`期现数据`写入日期、周次、年份、港口、品种、湿吨现货价、质量升贴水、品牌升贴水、主力连续收盘价、基差、数据状态。`计算明细`写入设计文档第6.2节全部字段；派生字段保留公式或可追溯分项值，不把复杂规则隐藏在单个不可审计常量里。

- [ ] **Step 3: 写入参数和质量说明**

`参数表`包含年度参数、特殊覆盖、I2312品牌升贴水、`X=1.5`和来源URL；`数据质量说明`包含缺失组合、停更、低流动性、S默认0、代理价格和规格不一致说明。

- [ ] **Step 4: 应用一致格式**

标题和表头使用深色填充、白字；数据区按类型设置日期、百分比和 `0.00` 数字格式；冻结表头、启用筛选、隐藏网格线、控制长文本列宽并换行；不对未使用区域套格式。

- [ ] **Step 5: 导出工作簿**

```js
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(finalWorkbookPath);
```

### Task 5: 数值、公式和视觉验收

**Files:**
- Verify: `/Users/wangjingze/Documents/轻量化交易管理系统WEB/outputs/019f58fd-4754-7ae2-8ebd-c3321fe0dd7f/铁矿石港口基差基础数据库_2024至今.xlsx`

**Interfaces:**
- Consumes: 最终 `.xlsx`。
- Produces: 可交付验收结果，不生成第二个工作簿版本。

- [ ] **Step 1: 检查关键范围**

使用 `workbook.inspect` 检查四个页签表头、首尾行、代表性 BRBF/PB粉/杨迪粉/昆巴粉/乌克兰精粉/卡拉拉精粉记录和参数来源。

- [ ] **Step 2: 扫描公式错误**

搜索 `#REF!|#DIV/0!|#VALUE!|#NAME?|#N/A`，Expected: 0 results。

- [ ] **Step 3: 对账记录数和唯一键**

精简期现数据行数应等于有效计算明细行数；原始 EBC、期货交集、缺失组合和无效质量行数量在数据质量说明中能对账；业务唯一键重复数为0。

- [ ] **Step 4: 逐页渲染视觉检查**

渲染四个页签的顶部和代表性数据区域，确认表头、数字、长文本、冻结区域和列宽无裁切、重叠、空白默认页或不可读内容。

- [ ] **Step 5: 最终文件检查**

确认文件存在、可重新导入、四个页签名称正确、修改时间为本次生成时间；最终只交付这一份 `.xlsx`。
