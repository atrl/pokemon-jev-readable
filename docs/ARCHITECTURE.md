# 当前架构：模型决定玩法，代码提供观察、记忆与执行

设计演进见 [DECISIONS.md](DECISIONS.md)，问题与未验证边界见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。安装入口是 [README](../README.md)。

## 1. 默认控制链

```text
Reader.snapshot() —— 完整内部状态只供解码/独立验收
       ↓ perception.project()：明确的结构化玩家观察白名单
Experience —— 实际视野、已访问地图、历史对白、输入前后结果
       ↓
System Two / DeepSeek —— 子目标、策略、验收条件、带证据引用的笔记
       ↓ normalize_observed_plan() 校验
PlanManager —— 持久计划与事实验收，不选剧情、不自动治疗
       ↓
System One / Jev —— 按键 + 是否需要重新规划（同次请求的独立问题）
       ├─ replan：丢弃并行的按键答案，回到 System Two
       └─ continue：执行被选中的一个按键
                         ↓
              重新观察、记录、完成/失效/到期后再规划
```

没有隐藏按键宏。路径是对**模型已选择目标**的几何计算，不是目标选择器；所有实际按键仍由 Jev 返回。默认输入中不存在 `recommended_move`、`next_button`、固定剧情目录或 `heal_party` 仲裁。

## 2. 目录与阅读顺序

| 文件/目录 | 职责 |
|---|---|
| `pokemon/run.py` | 唯一控制循环，预算、日志、存档；默认 `knowledge_mode=observed` |
| `pokemon/perception.py` | 模型观察边界；不把原始 RAM 字典直接当模型状态 |
| `pokemon/experience.py` | 有界地图/对白/动作经验与模型笔记，事实和假设分开 |
| `pokemon/plan_manager.py` | 无手写游戏策略的计划管理器，兼容事件中的 `campaign` 字段 |
| `pokemon/model_context.py` | 两个模型的中立输入，保持相同的观察权限 |
| `pokemon/planning.py` / `jev.py` | 模型请求、重试/错误、响应检查 |
| `pokemon/plan_contract.py` | observed v3 与显式 assisted v2 合同校验 |
| `pokemon/memory.py` / `emulator.py` | 版本绑定的只读 RAM 适配及模拟器执行 |
| `prompts/system1/`、`prompts/system2/` | 固定提示；`*-assisted.txt` 仅供旧方案对照 |
| `tests/python/`、`tests/web/`、`tests/integration/` | 单测、网页测试、真实模拟器/显式模型测试 |
| `pokemon/data/` | 原始版本匹配/解码数据；完整地图不进入默认模型规划 |
| `live/` | 原生 RGB→HLS、事件→SSE，只读网页 |

历史 `campaign.py`、`team_strategy.py`、`battle_strategy.py`、`campaign_knowledge.py`、`route_regions.py` 未删除，以免再次破坏可复用资源，但只在 **`--knowledge-mode assisted`** 的对照模式下参与策略。默认循环不实例化 CampaignPlanner，也不调用其导航、治疗或招式评分。

## 3. 观察合同：准确不等于有权知道

默认 `structured_player_v1` 是明确允许较丰富的结构化玩家信息，不声称纯像素、无先验或从零学习：

- 自身位置、金钱、队伍、背包、HP/PP/异常状态可以不打开菜单就读取。
- 已拥有招式的通用规则描述可作为标注来源的参考，不计算/排序推荐动作。
- 当前对白、菜单、光标、局部视野背景与真正可见的对象可读取。
- 当前视野内的传送点位置可作为结构化局部交互信息，**不提前公开目的地**。
- 对手只暴露当前种类、等级、状态与粗粒度血条。血条是 RAM 比例量化到 48 格，**不是实际屏幕像素解码**；不暴露精确 HP、属性数值、完整招式。
- 不向模型提供全地图连接、屏幕外首次出现的 NPC、脚本触发坐标、隐藏剧情位和预置任务目标。

RAM 解码器仍使用特定版本结构定义和源码校验。这与把源码攻略送入模型不同，但也不是严格的视觉游戏基准。未知字段保留 null/质量边界。`game_completed` 只由独立终局验收器用于停机，不作为未来任务目录发送给模型。

## 4. 记忆是什么

`Experience` 保存已看见的地形格、当前/过去见过的对象及最后观察时间、真实方向输入后的跨图连接、已读对白、动作前后变化。跨图边是有向经验，不凭空补反向路径；战斗失败/脚本移动不当作可自由重放的道路。

模型输入包含当前地图最多 65×65 的已观察窗口、已访问地图概览、最近 64 条连接、40 条对白及 12 次动作前后摘要。完整持久容器也有上限（256 图、每图 12000 格、512 连接、160 对白、64 动作、32 条模型笔记），不是无限日志。远处裁剪和遗忘在输入中说明，不能把“不在保留窗口”解释成“从未发生”。

DeepSeek 可以输出最多 4 条 `memory_updates`，每条必须引用本次收到的观察/对白/动作/目标证据。记录固定标为 `system2_hypothesis_not_verified_fact`，带 plan_id、步骤和可用的证据摘录；**模型推断不会写进已验证事实或地图**。对白/笔记都是数据，不能改变系统/API指令。

旧存档只迁移带 `observed_background` / `observed_player_position` / `observed_dialog` 等明确来源的历史记录，以及实际记录的跨图边；不导入旧攻略计划、治疗目标、源码路径或全剧情事实。迁移不改动原 sidecar，新的运行输出写入新会话目录。

## 5. 计划合同与模型主导权

生产模型返回 `target_ref` 或 null，引用必须来自本次实际观察/经验目录。不能编造地图 ID、坐标、代码或按钮脚本。模型选择当前目标，代码可在已观察地形上给出到此目标的路径坐标，但不返回“建议下一键”，也不自动走路。路径未知不等于不可达。

```json
{
  "subgoal": "model_chosen_identifier",
  "intent": "模型根据现场决定的短期目标",
  "reasoning": "证据、理由和未解决的不确定性",
  "target_ref": null,
  "success": {"type": "state_changed"},
  "policy": "模型自己的执行策略；可为空",
  "resource_policy": {},
  "memory_updates": [],
  "replan_when": [],
  "expires_steps": 160,
  "max_no_effect_steps": 24
}
```

这是格式示例，不是固定流程。`resource_policy` 缺失时就是空，不补默认逃跑、半血治疗或两只队伍。即使模型显式写了资源偏好，它也只交给 Jev 判断；程序不自动替换动作。如果模型需要某种风险触发重新规划，应明确写 `replan_when`，例如 `party_hp_below` 与阈值。

验收支持接近目标、地图/场景/可观察状态变化、对白关闭、队伍/球/新格增加、战斗结束、已提供事实为真。这些是**测量合同**，不是剧情目录：走近不等于交互成功，战斗结束不等于赢，state_changed 特别弱，不能当成剧情进展。终局用独立证据。

生命周期：`active → completed / failed / invalidated / expired`。默认没有自动治疗导致的 suspended；错误计划可提前结束。Jev 的独立 `plan_status` 也可以请求重规划，这会在任何按键执行之前丢弃该次并行按键答案。空密钥、非法响应、服务故障不触发伪造动作。

## 6. 模式、费用和可观察性

两个正交开关：

- `--knowledge-mode observed`（默认）：上述观察/记忆；`assisted`：显式旧攻略与策略对照。
- `--planner-mode deepseek`：要求规划服务；`auto`：有密钥则双模型，缺少时为 Jev-only observed；`local`：禁用 System Two。要复现实验旧规则，用 `assisted + local`。

`--steps` 现在限制决策轮次，包含 Jev 请求重规划但未执行按键的轮次；`executed_actions` 单独计数。`plan_review_requests`、`knowledge_mode`、`observation_policy` 写入报告。规划调用另有预算；暂停和存档不是通关。

当前 UI 继续展示事件、计划及可展开的真实请求，不是静态的示意状态。测试与运行结果只放被忽略的 outputs/runs 或 Actions Artifact。

## 7. 验证边界

默认路径测试必须证明：改变隐藏字段不改变两模型输入；低血量不自动回城；招式不自动排名；经验只由观察更新；笔记不会变成事实；完成/失败确实触发重规划；Jev 要求重规划时并行按键不会执行。

旧策略测试通过 `tests/python/assisted_helpers.py` 显式选择 assisted，保留对照，不冒充默认模式测试。`test_model_agency.py` 专门检查默认控制链。真实 ROM 由 integration 分别验证 raw→过滤→记忆→序列化；模拟 API 的测试不等于真实模型对局。

本次重构不能证明月见山或整场游戏成功；模型可能仍选错目标、反复修改计划，当前背景图也不识别所有机关/招牌语义。需要对应存档与真实两模型运行才能测量策略效果。
