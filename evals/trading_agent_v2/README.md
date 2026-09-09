# Trading Agent V2 Eval 证据层次

这里的检查分三层，输出必须分别解释：

1. definition 只检查题库字段、能力名称和工具白名单是否完整。它不运行模型，不评价答案，不产生软评分。
2. offline-behavior 只运行 offline_behavior_manifest.json 中固定的合成 pytest nodeid，验证 Harness 的预算、答案校验、有限纠错和权限边界。它使用脚本模型或无模型外部依赖，不连接 DeepSeek、Brave、企微或业务数据库。
3. fixture-replay 运行 fixtures.json 中冻结的 5 个脱敏合成业务场景，重新计算固定 oracle；它验证可重复的业务边界，但不评价真实模型的语言回答。
4. live 目前没有获准的真实运行器，命令会拒绝执行。真实模型、云端网页和企微验收必须按任务书 C 批单独授权和留证据。

命令：

    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode definition --suite regression
    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode definition --suite holdout
    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode fixture-replay
    ./.runtime/agent-v2/bin/python scripts/run_agent_v2_evals.py --mode offline-behavior

原有 regression/holdout 题库仍然保留，未送入真实模型就不能称为真实行为通过。历史上的“44+12 题、最低软分10”是旧脚本将软评分固定为满分后的结构检查结果，本目录不再使用那种表述。
