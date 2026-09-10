# Trading Agent V2 Eval 证据层次

这里的检查分三层，输出必须分别解释：

1. definition 只检查题库字段、能力名称和工具白名单是否完整。它不运行模型，不评价答案，不产生软评分。
2. offline-behavior 只运行 offline_behavior_manifest.json 中固定的合成 pytest nodeid，验证 Harness 的预算、答案校验、有限纠错和权限边界。它使用脚本模型或无模型外部依赖，不连接 DeepSeek、Brave、企微或业务数据库。
3. live 目前没有获准的真实运行器，命令会拒绝执行。真实模型、云端网页和企微验收必须按任务书 C 批单独授权和留证据。

命令：

    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode definition --suite regression
    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode definition --suite holdout
    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode offline-behavior

原有 regression/holdout 题库仍然保留，未送入真实模型就不能称为真实行为通过。历史上的“44+12 题、最低软分10”是旧脚本将软评分固定为满分后的结构检查结果，本目录不再使用那种表述。

本候选版本新增的定义案例覆盖登记数据集查询、确定性周环比/关系分析、证据字段白名单、atlas/compare 图谱、缺失断线、facet 分页和 matrix 列分页。它们使用合成快照或静态协议检查：通过只能说明代码和题库边界可检查，不能证明真实数据源已激活、真实管理员页面可用或正式版已发布。

新增数据工具必须使用固定 DatasetSpec 和只读适配器；题库中的 `query_dataset`、`compare_dataset`、`relate_datasets` 等工具名只代表允许的白名单入口，不允许模型提交 SQL、网页代码、任意 join 或交易动作。内部问题的 `external_egress=0` 仍是硬门槛；明确外部问题也必须分离内部证据和公开来源。
