# 三组控制实验

入口是 `python -m pokemon.experiments`。`brain` 为 DeepSeek 每步选择一个键；`hybrid` 为 DeepSeek 提供短期计划、Kev 选择每个键；`rl` 为训练后的 Recurrent PPO。前两组目前不训练模型权重，hybrid 是 A0 零样本迁移基线，不是已完成的 Pokémon 微调模型。

实验使用现有 Red Star、Reader、perception 和 Experience，独立于 `npm start` 的计划运行链。原运行链仍支持 DeepSeek 的 `ui_steps`；实验中禁止这些多步执行捷径。原运行链的 `jev_calls` 已纠正，不再把 `plan_steps` 动作记作 Jev 调用。

## 信息与执行合同

```text
同一 hash 绑定的存档 → Reader → perception → 本回合事实记忆
                                             ↓
                               同一份公共 observation packet
                              ↙              ↓             ↘
                         Kev + DS         DS-only       Recurrent PPO
                              ↘              ↓             ↙
                                  一个物理键 → 重新观察
                                             ↓
                                      独立验收与奖励
```

- 三组都是 `up/down/left/right/a/b/start/select/wait`，v1 固定按住 8 帧、松开 16 帧；`wait` 同样推进 24 帧。每步重新观察，没有一组能执行长按键串。
- packet 包含允许的当前状态、累计已观察地图、当前地图相关旧对白、真实转场、近期动作效果及共同任务描述。每个 trial 从空事实记忆开始，不导入旧 campaign 的攻略、假设或其它组的经验。
- DeepSeek 的短计划和私有笔记明确标记为模型假设。它们不写回共同事实记忆。Kev 判断计划不适用时，同次按键会被丢弃，有限重规划仍受原预算约束。
- 完整 packet 的 UTF-8 上限为 65,536 字节，由共用环境检查，超限明确停止。不会只截断 RL 的对白。扩大容量需同步 `environment.max_packet_bytes`、`rl.packet_bytes` 并重新训练匹配的策略。
- 内部终局证据与验收目标 map ID 只由 evaluator 使用，不进入策略输入。评分后的成功布尔值/标量奖励属于三组共同允许的结果反馈。

RL 编码器完整保留公共 JSON 字节，并从同一信息计算数值特征，用小型字节 CNN＋LSTM 学习表示。这是信息权限一致的可训练基线，但不拥有大语言模型的预训练语义能力；不宣称它是最强 Pokémon RL 方法。

## 安装

```bash
source .venv/bin/activate
pip install -r pokemon/requirements.txt
# 仅 RL 需要；GPU 主机先选择适合硬件的官方 PyTorch 构建。
pip install -r pokemon/experiments/requirements-rl.txt
python -m pokemon.experiments --help
```

SB3 与 SB3-Contrib 固定 2.9.0；精确依赖版本会写入模型元数据。CPU 可完成短训练验证。主运行、brain、hybrid 的导入不要求安装 Torch。

## 注册独立起点

原存档旁必须有匹配的 `last.state.json`（或同名 `.state.json`），程序校验 ROM 与存档哈希后复制到 suite 自己的 `states/`。不修改原文件，也不复制旧策略记忆。

```bash
python -m pokemon.experiments register \
  --suite outputs/experiment-suite/suite.json \
  --id mountain-test --family mountain-session-heldout --split test \
  --state pokemon/runs/YOUR_RUN/last.state \
  --goal 'Investigate the surroundings and find a way to leave this map.' \
  --success map_changed
```

`map_changed` 只证明换地图/楼层，不等于走出整座月见山。正式穿山任务可使用 `--success map_entered --map-id VERIFIED_ID`，编号需独立核实。`battle_finished` 不等于获胜；`scene_changed` 不等于剧情推进；只有 `game_completed` 使用独立终局证据。其它验收包括 `dialog_closed`、`party_grew`、`badge_gained`。

训练起点用 `--split train`，调参用 `validation`，最终评估用 `test`。同一存档哈希或同一 family 跨 split 会被拒绝；相关会话必须使用相同 family，不能随机拆相邻帧。family 由注册者提供，哈希检查不能自动证明两个不同存档来自独立会话。评估 RL 时还会对训练权重元数据检查 hash/family，防止换一份 suite 绕过隔离。

## B：纯 DeepSeek

run/compare 自动按现有约定读取根 `.env`、`pokemon/.env`，进程环境优先；不会把文件作为 shell 执行。模型请求必须显式传 `--allow-model-calls`。

```bash
python -m pokemon.experiments run \
  --suite outputs/experiment-suite/suite.json --case mountain-test \
  --controller brain --allow-model-calls \
  --max-steps 20 --max-requests 20 --max-seconds 120 \
  --output outputs/brain-1
```

沿用 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`DEEPSEEK_API_KEY`。brain 不需要 TypeSafe 或 Kev。它每次只返回一个键及有界私有笔记。

## A0：Kev＋DeepSeek

部署 [Kev 官方服务](https://github.com/jaredpalmer/kev) 并按官方文档选择实际模型（讨论建议从 `jaredpalmer/kev-4b` 开始）。服务接口采用其 [System One 请求合同](https://github.com/jaredpalmer/kev/blob/main/kev/api.py)，而非 OpenAI 聊天请求。

```dotenv
KEV_BASE_URL=http://127.0.0.1:8009
KEV_MODEL=kev-latest
# 有认证的自建服务才需 KEV_API_KEY。
```

`kev-latest` 是服务别名，不能证明权重是 4B 或固定修订。发布比较结果时应另保留服务权重仓库、revision/hash、量化和硬件记录。本代码不会凭别名宣称已验证权重身份。

```bash
python -m pokemon.experiments run \
  --suite outputs/experiment-suite/suite.json --case mountain-test \
  --controller hybrid --allow-model-calls \
  --output outputs/hybrid-1
```

DS 只提供意图、策略和动作 TTL，Kev 做逐步选择；没有按置信度阈值自动切成 DS 动作的隐藏路径。Kev 的概率不是长期通关价值。

## C：训练、续训、冻结评估

先用上述 register 命令注册独立 train 起点，再训练：

```bash
python -m pokemon.experiments train \
  --suite outputs/experiment-suite/suite.json --timesteps 100000 \
  --num-envs 4 --seed 7 --output outputs/rl-training-1
```

这里的步数是训练预算示例，不是通过月见山的保证。SB3 按完整 rollout 更新，实际采样步数可能向 `n_steps × num_envs` 的倍数取整；报告同时记录请求与实际步数。`num_envs` 使用 DummyVecEnv，多模拟器在本进程轮流推进，不冒称并行 CPU 加速。Python seed 只控制策略采样/训练，不能声称改变了存档里的游戏 RNG。

训练产物包括 `policy.zip`、`policy.json`、`training-start.json`、`learning-curve.jsonl` 及优化损失日志。只有实际更新发生且参数哈希变化才发布 `trained` 权重。记录训练时间、设备、版本、训练数据身份与初末参数哈希；没有给本地算力编造美元价格。

```bash
python -m pokemon.experiments train \
  --suite outputs/experiment-suite/suite.json --timesteps 100000 \
  --num-envs 4 --seed 7 --resume outputs/rl-training-1/policy.zip \
  --output outputs/rl-training-2

python -m pokemon.experiments run \
  --suite outputs/experiment-suite/suite.json --case mountain-test \
  --controller rl --checkpoint outputs/rl-training-2/policy.zip \
  --output outputs/rl-heldout-1
```

续训要求原训练合同和相同数据身份。评估严格核验权重 SHA、协议、ROM、动作/编码合同及训练数据隔离，冻结梯度并每回合重置 LSTM。缺权重、依赖或元数据时停止，不退回随机策略。只加载自己训练或可信来源的 SB3 checkpoint，因为其格式包含 Python 序列化对象。

## 同起点批量比较

```bash
python -m pokemon.experiments compare \
  --suite outputs/experiment-suite/suite.json --split test \
  --controllers brain,hybrid,rl --checkpoint outputs/rl-training-2/policy.zip \
  --repeats 1 --max-trials 30 --batch-max-seconds 1800 \
  --allow-model-calls --output outputs/comparison-1
```

打开 `outputs/comparison-1/index.html`；`comparison.json` 保留完整 trial 列表，失败也计入分母。每个 trial 保存独立验收、真实输入 packet、模型动作来源、执行结果、最后截图和实际 HTTP/usage。输入哈希只在足够初始化证据存在时标一致。

默认配置见 [configs/experiments.json](../configs/experiments.json)，可用 `--config PATH` 覆盖。时间/请求/token/动作预算为**单 trial** 上限；批次另由 `max-trials` 和可选 `batch-max-seconds` 限制。相同证据下的所有 provider 请求共用计数，不能靠换目标名或私有笔记重置。

usage 缺失或失败响应未报告时记录 unknown，以请求字节上界＋输出预算保守占用额度；这不是实测 token。当前 HTTP 超时是同步 transport 超时，返回后再次检查墙钟预算，超时答案不能产生动作。未知 usage、失败尝试、本地推理时间都不能算免费。DS 请求占比统计实际 HTTP（含重试），不是成功计划比例。实际美元/能耗与 RL 训练摊销需要部署方提供价格模型，默认保持 null。

独立奖励：首次完成任务、真正新增徽章、封顶的首次访问奖励、每步动作成本；不奖励改计划名、写反思或反复治疗。重复存档不是独立随机样本；同类死锁复发需要预先定义场景家族后跨 trial 分析，不能由两步烟雾运行推出结论。

CLI `run` 仅验收成功返回 0；预算耗尽/未成功返回 2，详细原因在报告。`compare` 在批次完成且各 trial 正常终止/截断时返回 0，仍需读取成功率；模型/环境错误或批次未完成返回 2。

## 验证入口

```bash
python -m unittest discover -s tests/python -p 'test_experiment*.py' -v
python -m unittest discover -s tests/integration -p test_experiment_rom.py -v
python -m unittest discover -s tests/integration -p test_experiment_rl.py -v
POKEMON_EXPERIMENT_SUITE=outputs/experiment-suite/suite.json \
  python -m unittest discover -s tests/integration -p test_experiment_rl.py -v
```

纯测试不请求模型。ROM 集成使用独立脚本夹具验证环境；PPO 集成验证真实梯度更新、保存/加载、冻结及续训。设置 `POKEMON_EXPERIMENT_SUITE` 可启用真实 train 存档的短 PPO rollout。缺依赖/夹具会明确 skip。CI 单独运行真实 ROM 和 PPO 机制检查，禁止调用付费服务。

## ADR-011 · 共用环境，替换控制器（2026-09-29）

按[最新讨论](https://chatgpt.com/share/6abb2f78-1288-83e9-8297-c47153ddea0e)增加独立实验入口。触发问题是旧链的 `ui_steps` 捷径与统一 `jev_calls` 统计会污染分工比较，且观察/记忆/奖励不统一就无法归因模型差异。

采用三组相同观察与单键执行；严格预算、hash/family 隔离、独立评分及真实 PPO 训练。没有同时引入技能库、搜索、外部 Q 表或游戏攻略。代价是逐键成本和字节编码学习难度；当前只有 A0，没有 A1/A2 的游戏适配训练。验证只能分别证明环境、调用链与学习机制可运行，不能由绿色测试推导通关、最佳模型或成本优势。原 ROM、存档、运行入口和 live 控制进程不迁移。
