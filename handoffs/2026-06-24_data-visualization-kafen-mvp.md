# 给执行模型的说明

你将接手"轻量化交易管理系统 Web"的新增功能开发：`数据可视化管理` MVP。
按本文执行，不要自行扩大范围。当前目标是先跑通**卡粉**的完整闭环，不做全品种模板。

## 当前状态

- 工作目录：`/Users/wangjingze/Documents/轻量化交易管理系统WEB`
- 默认分支/环境：`staging`
- 默认部署目标：Render `ltm-web-staging` / Supabase `LTM WEB STAGING`
- 严禁：未获用户明确确认时触碰 `main`、Production、正式 Supabase、正式 Render 或生产数据。
- 入口文档：先读 `开发流程_备忘.md`、`README.md`、`版本更新记录.md`。

## 业务目标

新增一级菜单：`数据可视化管理`，与 `台账管理`、`信息预警管理`、`后台管理` 并列。

第一版只做卡粉 MVP：

- 从现有 Excel `副本铁矿data base.xlsx` 读取卡粉发运和库存。
- 自动归属业务周。
- 自动计算卡粉表需。
- 支持库存、发运、表需三个数据页查看和人工修正。
- 支持导入预检、确认入库、人工修正保护、显式覆盖人工修正项。
- 支持库存、发运、表需三个图表页展示卡粉多年份折线。

暂不做：

- 12 个品种完整导入模板。
- 图表导出。
- 数据导出。
- 图表页补 0 标记开关。
- 固定 Excel 模板设计。

## 菜单结构

```text
数据可视化管理
  图表数据管理
  数据展示
```

`图表数据管理` 页面使用页签：

```text
库存数据 | 发运数据 | 表需数据
```

`数据展示` 页面使用页签：

```text
库存图表 | 发运图表 | 表需图表
```

建议新增模块 code：`data_visualization`，接入现有模块权限体系。

## Excel 数据来源

第一版按现有文件格式读取：

```text
文件：/Users/wangjingze/建龙/期货组/本地数据库/副本铁矿data base.xlsx
```

发运数据：

```text
sheet = 发运
日期列 = B列，表头 19港发运更新时间
卡粉列 = AY列，表头 卡粉
```

库存数据：

```text
sheet = 卡粉库存
日期列 = A列
卡粉库存列 = B列，表头 卡粉总库存
```

表需不从 Excel 导入，由系统计算。

## 业务周规则

系统需要生成统一业务周字段：

```text
year
week_no
display_date
shipment_date
inventory_date
```

规则：

- 周从周一开始。
- `1 月 1 日` 所在周为新年第 1 周。
- 该年第一个完整周一开始的周为第 2 周。
- 库存和发运日期相差不超过 4 天且属于同一业务周时，归属同一周。
- 图表和表格展示日期优先使用该业务周的发运日期；没有发运日期时使用库存日期。
- 页面呈现保持简洁：`日期 | 周次 | 卡粉`。

## 表需计算

表需公式：

```text
表需(t) = 2周前发运量 - 库存变化量
库存变化量 = 库存(t) - 库存(t-1)

即：
表需(t) = 发运(t-2) + 库存(t-1) - 库存(t)
```

缺失值第一版按 `0` 展示和参与计算。底层必须记录该 0 是否来自缺失补 0，但第一版页面不提供补 0 标记筛选开关。

## 数据管理页面

三个页签均用简洁表格：

```text
日期 | 周次 | 卡粉
```

库存、发运、表需都支持手动编辑。任何手动编辑都视为人工修正，规则等同：

```text
导入值 / 系统计算值
人工修正值
当前展示值
是否人工修正
最近修改人
最近修改时间
来源：导入 / 自动计算 / 手工修改 / 缺失补0
```

当前展示值规则：

```text
如果存在人工修正值，显示人工修正值。
否则库存/发运显示导入值，表需显示系统计算值。
```

主表只显示数值，不展示复杂审计字段。人工修正单元格可加轻量标记；点击或悬停单元格时可展示详情。

## 导入流程

导入必须分两步：

```text
上传 Excel -> 导入预检 -> 用户确认写入
```

不得上传后直接入库。

预检需要展示：

```text
文件名
识别数据类型：库存、发运
识别日期范围
新增数据量
覆盖数据量
空值数量
异常项数量
历史变更数量
人工修正保护项数量
```

空值数量统计口径：

```text
识别到的日期行 × 识别到的品种列 - 有值单元格数
```

第一版异常项至少包括：

```text
日期无法识别
数值不是数字
同一文件内同一业务周重复
历史值发生变化
命中人工修正保护项
```

如果上传文件修改了历史数据，预检必须提示：

```text
数据类型
周次
日期
品种
系统当前值
本次导入值
是否人工保护
```

## 覆盖规则

普通数据：

```text
同一数据类型 + 同一业务周 + 同一品种
新导入值覆盖旧导入值
```

人工修正数据：

```text
默认保护，新导入不覆盖。
```

预检页面需要列出人工保护项，并允许：

```text
覆盖选中人工保护项
覆盖本次全部人工保护项
```

覆盖动作必须二次确认并留痕。覆盖后：

```text
当前值 = 本次导入值
人工修正状态 = 取消
来源 = Excel导入覆盖人工修正
```

不要做“以后导入永远覆盖人工修正”的全局开关。

## 图表展示

每个图表页第一版只展示卡粉一张图。

顶部控制只保留：

```text
年份筛选：支持多选
品种筛选：第一版只有卡粉，可预留但只显示卡粉
```

图表规则：

- 一年一根折线。
- 横轴为周，对外显示日期。
- 纵轴为库存量 / 发运量 / 表需量。
- 默认展示当前年份。
- 点击任意年份折线后，高亮该年份。
- 高亮年份折线加粗、颜色饱和度提高。
- 非高亮年份降低透明度。
- 鼠标悬停显示：日期、周次、年份、品种、数值。

## 建议数据模型

页面可以按宽表展示，但底层建议长表存储，兼容后续扩展到 12 个品种。

核心数据表建议字段：

```text
id
week_key
year
week_no
display_date
raw_date
metric_type: inventory / shipment / apparent_demand
product
value
imported_value
calculated_value
manual_value
is_manual_override
is_missing_filled
source_batch_id
created_by
updated_by
updated_at
```

业务周表建议字段：

```text
id
year
week_no
week_start_date
week_end_date
shipment_date
inventory_date
display_date
```

导入批次表建议字段：

```text
id
file_name
metric_types
date_start
date_end
created_by
created_at
status
insert_count
overwrite_count
empty_count
error_count
manual_protected_count
```

变更记录表建议字段：

```text
id
data_point_id
old_value
new_value
operation_type: import / manual_edit / import_override_manual / recalculation
source_batch_id
created_by
created_at
note
```

## 建议 API

按现有 FastAPI 风格新增：

```text
POST /api/data-visualization/import/preview
POST /api/data-visualization/import/commit
GET  /api/data-visualization/table?metric=inventory|shipment|apparent_demand
PUT  /api/data-visualization/value
GET  /api/data-visualization/chart?metric=inventory|shipment|apparent_demand
GET  /api/data-visualization/import-batches
```

所有接口必须走当前登录用户和模块权限。

## 验收标准

第一版验收通过条件：

- 左侧菜单出现 `数据可视化管理`。
- 可上传 `副本铁矿data base.xlsx` 并完成预检。
- 可导入卡粉发运和库存。
- 系统正确归属业务周。
- 系统自动生成卡粉表需。
- 库存、发运、表需表格可查看和手动编辑。
- 手动编辑库存、发运、表需后均被标记为人工修正。
- 再次导入时默认不覆盖人工修正。
- 预检可选择覆盖人工修正项。
- 库存、发运、表需图表可按年份展示。
- 年份筛选和品种筛选可用。
- 点击某一年折线后能高亮该年份。

## 开发注意

- 只在 `staging` / 测试环境完成开发和验证。
- 不要触碰生产数据库或生产服务。
- 数据库设计要兼容 PostgreSQL/Supabase，不要只按 SQLite 特性写。
- 保持现有页面风格，先做可用的数据管理和图表闭环，不做大规模视觉重构。
- 代码改动完成后，更新 `版本更新记录.md` 的 Staging 记录。
