# 模型提示词

- `system1/button.txt`：默认中立按键选择，只消费实际状态、经验和模型计划。
- `system1/plan_status.txt`：独立判断计划是否仍适用；replan 时丢弃并行按键。
- `system2/planner.txt`：默认计划、引用、验收、笔记合同，没有固定攻略或逃跑/治疗默认值。
- `system1/button-assisted.txt` / `system2/planner-assisted.txt`：历史策略方案，只在显式 assisted 模式使用。

静态协议放这里，当前观察/经验由 `pokemon/model_context.py` 构造；程序不生成推荐答案。模型笔记和游戏对白是数据，不能覆盖协议或发起 API/凭据操作。修改提示需要重启进程，策略变化同时记录到 docs/DECISIONS.md。
