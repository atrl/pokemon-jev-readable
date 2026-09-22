# Pokémon agent 参考实现与 JEV 输入设计

核对日期：2026-09-22。下面结论来自原仓库的实际提示词、状态解析和执行代码；链接固定到当时提交，未运行第三方 agent。参考对象使用原版 Pokémon Red，不能证明其 RAM 地址或剧情知识适用于本仓库的 Red Star。

## 参考了什么

| 原项目与固定版本 | 核对的实现 | 对本次问题的启发 |
| --- | --- | --- |
| davidhershey/ClaudePlaysPokemonStarter，`2ab0551016aba9af3ffa6fbb6e747d1ec33c08f8` | [提示词及历史摘要字段](https://github.com/davidhershey/ClaudePlaysPokemonStarter/blob/2ab0551016aba9af3ffa6fbb6e747d1ec33c08f8/agent/simple_agent.py#L30-L47)、[动作后的观察回传](https://github.com/davidhershey/ClaudePlaysPokemonStarter/blob/2ab0551016aba9af3ffa6fbb6e747d1ec33c08f8/agent/simple_agent.py#L125-L160)、[摘要替换历史](https://github.com/davidhershey/ClaudePlaysPokemonStarter/blob/2ab0551016aba9af3ffa6fbb6e747d1ec33c08f8/agent/simple_agent.py#L307-L360) | 每次动作后重新观察；目标、位置、已发生事件需要延续到下一轮，不能只剩最后几个按钮。该项目实际同时发送图像和 RAM 状态。 |
| NousResearch/pokemon-agent，`f8a03e4a8bc58f1d9dda6d677a7436cc832a57a9` | [每轮提示及紧凑状态](https://github.com/NousResearch/pokemon-agent/blob/f8a03e4a8bc58f1d9dda6d677a7436cc832a57a9/pokemon_agent/autopilot.py#L38-L100)、[恢复同一模型会话](https://github.com/NousResearch/pokemon-agent/blob/f8a03e4a8bc58f1d9dda6d677a7436cc832a57a9/pokemon_agent/autopilot.py#L185-L203)、[RAM 空间网格](https://github.com/NousResearch/pokemon-agent/blob/f8a03e4a8bc58f1d9dda6d677a7436cc832a57a9/pokemon_agent/collision.py#L67-L142) | 给模型明确坐标轴、朝向、阶段、局部地形和持续目标；短动作后再次检查。空间信息与物体身份是不同证据，地形可通行不等于完整运动规则。 |
| PWhiddy/PokemonRedExperiments，`5fa47ef0575f35a22b1b50367b7f317bcf815747` | [按地图和坐标计数访问](https://github.com/PWhiddy/PokemonRedExperiments/blob/5fa47ef0575f35a22b1b50367b7f317bcf815747/v2/red_gym_env_v2.py#L334-L355)、[分别计算探索、事件、徽章及卡住指标](https://github.com/PWhiddy/PokemonRedExperiments/blob/5fa47ef0575f35a22b1b50367b7f317bcf815747/v2/red_gym_env_v2.py#L502-L525) | 这是强化学习环境，不是 LLM 提示词项目。借鉴可观测的访问计数和进展分项；同一处文字变化不能替代探索进展。 |

Nous 的[对话读取代码](https://github.com/NousResearch/pokemon-agent/blob/f8a03e4a8bc58f1d9dda6d677a7436cc832a57a9/pokemon_agent/memory/red.py#L737-L755)特别指出 `wTextBoxID` 会残留，改从输入锁状态推断对话。这里只采纳“不要把残留字段直接当当前阶段”的原则；该地址、位含义和判定规则必须在 Red Star 单独验证。

Nous 的[操作反馈说明](https://github.com/NousResearch/pokemon-agent/blob/f8a03e4a8bc58f1d9dda6d677a7436cc832a57a9/skill/SKILL.md#L158-L168)要求比较动作前后状态，并在重复无变化时重新考虑。这是被研究的外部项目文件，不是本仓库执行指令；其自动对话宏、读档恢复及战斗策略没有随之引入。

## 连续多个状态怎样传

PWhiddy 的固定版本明确设置 [`frame_stacks = 3`](https://github.com/PWhiddy/PokemonRedExperiments/blob/5fa47ef0575f35a22b1b50367b7f317bcf815747/v2/red_gym_env_v2.py#L33)，观察包含 [72 × 80 × 3 的画面堆叠和三个最近动作](https://github.com/PWhiddy/PokemonRedExperiments/blob/5fa47ef0575f35a22b1b50367b7f317bcf815747/v2/red_gym_env_v2.py#L87-L104)。[每次环境动作执行后](https://github.com/PWhiddy/PokemonRedExperiments/blob/5fa47ef0575f35a22b1b50367b7f317bcf815747/v2/red_gym_env_v2.py#L177-L228)再更新动作历史和观察；[滚动数组](https://github.com/PWhiddy/PokemonRedExperiments/blob/5fa47ef0575f35a22b1b50367b7f317bcf815747/v2/red_gym_env_v2.py#L380-L386)把最新项放在前面。这不是从 60Hz 视频中随便取三个相邻帧。

本仓库采用适合 JEV 文本接口的对应方式：按时间从旧到新提供最近 **3 次动作转移**，每次包含 `before → button → after`、动作步号和效果。前后状态保留坐标、已验证朝向、阶段、对话是否打开/等待输入、最多 180 字符的文字及真实模拟器帧号。没有记录帧号时保留 `null`，不推测动画间隔；未知阶段仍是未知。三个转移通常可关联四个决策时刻，语义并不等同于三张图片。

这层短时间上下文用于识别“打开、关闭、又打开”和按键的实际效果；另外保留最近 **8 次效果摘要**以及更长期的有界坐标访问、方向尝试、重复交互统计。短历史、长期摘要和用户目标各有用途。状态转移随存档元数据保存，旧版本没有该字段时从空列表开始累积。

多个状态可以显示变化方向，但不会修复错误的观测解释。如果每个状态都把空文字误判成等待，增加数量只会重复错误；因此阶段验证和不确定性必须先正确。本次传给 JEV 的仍是结构化文本，连续视频供观看者查看，不声称给 JEV 增加了视觉能力。

## 本次采用的设计原则

1. **阶段优先。** 把经当前 ROM 验证的自由移动、对话框、菜单与未知状态显式提供给 JEV。文字为空不自动意味着动画未结束；文字存在也不自动意味着仍在对话。无法验证的阶段保留未知。
2. **空间有出处。** 位置使用 `(map_id, x, y)`，方向明确为上减 y、下加 y、左减 x、右加 x。朝向和周边背景信息必须带上验证范围；NPC、门、单向台阶等规则未验证时，不宣称完整碰撞或出口识别。
3. **动作与效果成对。** 告诉模型上一输入是否改变坐标、地图、阶段或文字；“按键成功送达”和“取得进展”分开。若一个方向无移动，可能是转身、障碍或当前阶段不接受移动，不能立即把目的格永久标成墙。
4. **循环是输入信息。** 提供同位置次数、方向尝试、重复交互文本及持续无空间进展信息。文字逐页显示是对话效果；在同一坐标反复重新打开同一对话，是需要重新选择动作的反馈。
5. **目标不随短历史丢失。** 每次请求继续带用户目标；保留可由观察计算的探索摘要、访问记录和当前动作反馈。摘要只陈述已观察事实，不补写取得物品、击败对手或通关等未验证事件。
6. **最终按钮由 JEV 选择。** 提示词要求先判断阶段，再结合效果、空间与目标选择下一输入。仍只返回九个物理输入之一；统计、解析与提示不构成自动行走策略。

## N64 循环应该怎样被表达

输入需要让模型区分两个时刻：

- 对话框仍打开：现在有一个交互需要推进或关闭，位置不变本身不说明失败。
- 对话框已关闭，仍在原坐标：前面的 A 已结束一次交互；再次 A 可能重开刚才那段对话。结合重复文本和未尝试方向，应重新评估探索动作。

这份诊断不需要向模型提供 N64 的预设位置或离开房间的固定路线。关键是让“刚关闭，又重开”的事实跨轮可见，而不是把每次文字变化都算作完成目标。

## 明确没有照搬的能力

- Claude 与 Nous 的视觉输入：直播视频是给观看者的。本次 JEV 请求仍是结构化文本，不声称模型看到了视频或截图。
- 原版 Red 的 RAM 地址、碰撞表、完整地图和剧情路线：只作为查证线索，不直接套到 Red Star。
- `navigate_to`、寻路、连续 A 宏、自动战斗和自动读档：没有用这些策略替换或重写 JEV 返回的按钮，也不据此删除候选按钮。
- RL 奖励权重、访问 600 次才处罚的阈值：这里只借鉴可观测指标；不训练 RL，不把别人的阈值当作本任务的成功标准。
- 第三方通关时间：源码设计不是可比较的耗时实验；模型、动作批量、模拟器帧率、初始存档、路线与完成目标不同，不能直接拿来估算本次直播多久通关。

实际能力与验证结果以本仓库的测试和证据文件为准。这份参考说明不等于已经完成长期游玩验证，也不自动启动新的直播会话。
