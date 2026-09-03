# WH6 盘中成交与持仓采集器 V2 验收运行手册

状态：代码与本地 SQLite/API/页面测试已完成；Windows 11、真实 WH6 自然事件和 LTM WEB STAGING 读回尚未完成。本手册不把本机回放或 HTTP 模拟写成实机验收。

## 1. 范围与安全边界

- 目标环境只允许 `LTM WEB STAGING` 与对应的 Staging Render 地址；禁止 Production/main/正式 Supabase。
- 采集器只能读取已成交的 `match.dat` 和经过显式版本、声明数量、完整结束标记校验的持仓缓存；必要时只读配套 `order.dat` 补齐成交字段。
- 不执行下单、撤单、改单、平仓、行权、转账，不控制 WH6 界面或进程，不注入、不读内存、不抓包、不模拟交易协议。
- 第一阶段只验收期权当日成交量和当前期权持仓；期货可以验证已经入库，但不得出现在第一阶段页面、统计或 Agent 结果中。

## 2. 本地证据（已完成）

执行目录：本项目独立 worktree，SQLite 临时数据库/临时缓存，非 Windows、非 Staging 数据。

```bash
python3 -m pytest -q \
  tests/test_wh6_collector_core.py \
  tests/test_wh6_position_parser.py \
  tests/test_wh6_collector_store.py \
  tests/test_wh6_collector_scheduler.py \
  tests/test_wh6_collector_cli.py \
  tests/test_wh6_collector_end_to_end.py \
  tests/test_wh6_collector_v2_end_to_end.py \
  tests/test_trading_collector_service.py \
  tests/test_trading_collector_positions_service.py \
  tests/test_trading_collector_api.py \
  tests/test_trading_collector_positions_api.py
node --test tests/trading_collector_frontend.test.mjs
```

记录每次执行的日期、通过数、失败数、完整命令和环境。测试通过只证明代码路径和受控样本，不证明 Windows WH6 版本、自然成交时延或 Staging 页面读回。

## 3. Windows 11 实机阶段

在隔离的 Windows 11 虚拟机安装当前功能分支构建出的 `WH6成交采集器-Setup.exe`，安装前记录 SHA-256。安装前不得复制其他设备的 SQLite、配置或设备令牌。

按以下顺序操作并保存脱敏截图/时间戳：

1. 以普通 Windows 用户安装；确认程序数据落在 `%LOCALAPPDATA%\WH6成交采集器`，不写入 WH6 `Record` 目录。
2. 登录目标宏源期货账户，使用自动发现或手动选择目标 `Record` 目录；发现多个 Record 根时停止并明确选择。
3. 在 Staging Web 管理页生成一次性连接码，在采集器输入；确认页面只显示脱敏账户标签，令牌不进入截图、日志或配置明文。
4. 触发一次历史回补；确认历史队列持续处理，随后保持采集器运行。
5. 断开网络、重新启动采集器，再恢复网络；确认本地待发送记录和快照不丢失、不重复，未知 HTTP 结果继续待确认。
6. 切换 WH6 登录账户或制造可验证账户标识变化；确认上传暂停并显示账户待确认/账户已变化，恢复前不发送新数据。
7. 在 WH6 中产生一条自然成交（不通过脚本或自动化操作），记录 WH6 完整写入时间、采集器观察时间、Staging 接收时间和页面读回时间；正常条件下端到端目标不超过 10 秒。
8. 对照 WH6 完整持仓页面/缓存，确认多合约、空持仓和今昨仓字段；持仓按完整快照读取，不把两个设备数量相加。
9. 使用第二台受控只读设备或已批准的第二个采集器观察同一账户，确认相同快照只产生一个标准快照、保留两条设备观察；制造内容差异后，短暂差异和持续超过 30 秒的冲突均显示异常。
10. 打开第一阶段页面，确认当日期权成交量只按实际期权成交记录汇总，当前持仓只显示期权；期货不出现在页面、统计或 Agent 读结果中。确认过期显示“持仓数据可能已过期”，冲突显示“多设备持仓不一致”。

实机验收必须保留：Windows 版本、WH6 版本、源路径脱敏值、账户标签脱敏值、自然事件时间戳、Staging 接收/读回证据和截图。没有这些材料时，只能报告“本地代码就绪”。

## 4. Staging 变更与回滚

在将本分支部署到 Staging 前，先确认数据库连接确实是 `LTM WEB STAGING`，备份/迁移计划由 Staging 管理员执行。迁移只创建：

- `trading_intraday_position_observations`
- `trading_intraday_position_snapshots`
- `trading_intraday_position_rows`

既有 `trading_trade_facts`、结算表、订单融资表和 Production 数据不变。回滚优先停用 Staging 采集器、保留观察与快照用于审计，再回退应用分支；不得为回滚删除采集证据或修改结算事实。

部署后至少回读：设备绑定、设备上传返回、期权成交量、当前期权持仓、过期/冲突状态和页面版本身份。API 200、健康检查、静态文件存在或本地测试不能替代这些业务回读。

## 5. 当前未完成项目

- 尚未在 Windows 11 构建并签收安装包。
- 尚未连接真实 WH6 缓存进行版本/字段阶段 0 验证。
- 尚未用自然成交证明 10 秒目标。
- 尚未完成两台 Windows 设备的快照观察、冲突和断网/重启实测。
- 尚未部署或回读 LTM WEB STAGING；没有 Staging 版本身份和数据证据前，不更新正式版本记录，也不触碰 Production。
