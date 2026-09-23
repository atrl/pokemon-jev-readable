# System Two 规划 + System One 局部执行

## 当前分工

- DeepSeek 在启动、当前计划完成、失败或到期时产生一个短期计划，不再只在旧主线规则卡住时介入。
- CampaignPlanner 在双模型模式中管理世界记忆、验证、计划生命周期和导航工具；原主线目录是注明来源的参考，不自动决定下一任务。
- Jev 读取当前唯一有效计划和真实状态，选择一个基础按键。当前版仍保留逐键控制，没有暗中改成自动导航或按键宏。
- 紧急治疗暂时中断计划；暂停期间不消耗计划动作 TTL。恢复后继续评估原计划。
- `--planner-mode local` 保留原规则方案，作为对照；`auto` 没有 DeepSeek 密钥时明确记录 `local_fallback`。要求双模型时用 `deepseek`，缺密钥/请求失败不伪造动作。

## 启动

在根 `.env` 设置 TYPESAFE_API_KEY 和 DEEPSEEK_API_KEY。当前默认 DEEPSEEK_MODEL 为 `deepseek-flash`；显式设置的模型名不会被程序偷偷替换。当前官方支持 JSON output：
https://api-docs.deepseek.com/guides/json_mode/

```bash
npm start -- --resume --planner-mode deepseek --steps 200 --planner-call-budget 4 --max-seconds 300
```

修改环境变量或代码需要结束旧进程并从保存的 checkpoint 重新启动。不要同时启动两个游戏控制器。

## 计划合同

生产模型只返回以下 JSON（target_ref 必须来自该次 situation.targets）：

```json
{
  "subgoal": "use_observed_exit",
  "intent": "通过当前观察到的出口，进入下一房间并重新评估",
  "reasoning": "当前无待处理对白，出口可作为局部目标",
  "target_ref": "warp:38:0",
  "success": {"type": "target_reached"},
  "resource_policy": {"wild_battle": "run", "catch_species": null, "heal_hp_ratio": 0.5, "max_party_size": 2},
  "expires_steps": 160,
  "max_no_effect_steps": 24
}
```

上面的 target_ref 只是合同例子，不是预写路线。实际可用对象、出口、已知地图和局部地面候选由当前观察构造。禁止模型自行编造地图 ID/坐标，禁止代码或按钮序列。

`success.type` 可取 target_reached、fact_true、dialog_closed、party_grew、balls_increased、new_tile、battle_finished。fact_true 需要另一个 fact 字段，引用本次报告提供的事实名。未知数据不能满足条件。到达对象仅表示接近，不表示交互完成；结束战斗不表示获胜；所有局部条件都不表示通关。

## 闭环与恢复

计划生命周期为 active → completed / failed / invalidated / expired，治疗期间为 suspended。每种结束结果保存在 plan_history，附带期望条件、目标、实际位置和证据。失败可在 TTL 之前中断，旧 checkpoint 中无新版验收合同的计划会失效后重新规划。

恢复保留已探索地形和失败记录，不整图抹掉；动态观察继续更新。DeepSeek 收到当前对白、场景、对象、出口、路径错误、队伍/PP、完整可用事实、近期动作及此前计划结果。未知/未验证字段保持未知。

## 看结果，而不是只看绿色 CI

report.json 的 planning_calls 是请求次数，plans 是接受的计划数，planning_failures 是失败数。事件序列：planner_configuration → planning_requested → planner_request → plan → jev_request/decision/result → plan_outcome。

planner_unavailable / blocked_missing_planner_key / planner_budget_reached 不代表游戏成功。错误分类会写日志但不包含 Authorization、API Key 或未经清理的 HTTP 响应。实际模型标识记录在 planner_model。

Actions 已传入 DEEPSEEK_API_KEY，模型/端点可用对应 Repository Variables 设置。密钥在本机 .env 不等于已存在 GitHub Actions Secrets。

## 测试与证据边界

```bash
python -m unittest discover -s pokemon/tests -v
npm run check && npm test
python pokemon/test_live.py
python pokemon/test_dual_live.py
```

最后一条需要真实 ROM 和两把密钥，最多 80 个按键、4 次 DeepSeek 请求、240 秒游戏循环墙钟预算。它从明确标记的出生房间回归存档开始，不冒称月见山验证或完整通关；原始请求、计划、按键和运行报告用于交叉核查。网络调用本身也有超时。

月见山效果需从用户实际 checkpoint 加上匹配的 last.progress.json / last.campaign.json 继续。不要把出生房间连通测试推广成整场游戏能力。
