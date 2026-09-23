# Pokémon Red Star + System Two / System One

**只读游戏状态 → DeepSeek 短期规划 → JEV 局部选择 → 执行后验证。** 网页展示 Pokémon 原生视频、实际模型请求与计划状态。

从 [双模型合同和阅读指引](pokemon/docs/DUAL_MODEL.md) 开始，通用模块介绍见 [源码阅读路线](docs/READING.md)。

## 启动

需要 Node.js 22.16+、Python 3.10+（建议 3.12）和 FFmpeg。

```bash
python3 -m venv .venv
.venv/bin/pip install -r pokemon/requirements.txt
cp -n .env.example .env
# 在 .env 中设置 TYPESAFE_API_KEY 和 DEEPSEEK_API_KEY
npm start -- --planner-mode deepseek --steps 200 --planner-call-budget 4
```

打开 http://127.0.0.1:18766 。从已保存的游戏继续时增加 `--resume`。更新代码或 `.env` 后，需要先停止旧控制进程，再从保存的 checkpoint 重启；不会热更新现有会话。

## 谁负责什么

| 模块 | 职责 |
| --- | --- |
| `pokemon/planning.py` | DeepSeek 在启动、计划完成、失效或失败时提出一个短期计划 |
| `pokemon/plan_contract.py` | 将目标引用解析成真实对象/出口；验证成功条件、资源策略与生命周期 |
| `pokemon/campaign.py` | 保存世界记忆、计划、失败证据，管理暂停/恢复并计算导航建议 |
| `pokemon/prompt.py`、`jev.py` | JEV 根据当前唯一有效计划和现场状态选择实际按键 |
| `pokemon/run.py` | 唯一执行循环、请求预算、日志、存档与错误暂停 |
| `pokemon/memory.py`、`emulator.py` | 只读真实 RAM、执行输入，不写游戏内存 |
| `live/` | 启动入口、只读直播、实际请求和手柄状态 |

双模型模式中，固定剧情目录只是注明来源的参考，不再强制决定下一任务。寻路和伤害计算仍由代码提供建议；当前版本保留 JEV 逐键模式，没有隐藏的自动导航或剧情宏。

`deepseek` 模式要求规划密钥；缺密钥或规划请求失败会保存暂停。`local` 明确启用旧规则方案作为对照；`auto` 无 DeepSeek 密钥时会记录本地回退。

## 计划如何结束

模型返回 `target_ref` 和可检查的 `success`，不能自行生成地图 ID、坐标或代码。计划结束分为 completed / failed / invalidated / expired，失败证据送给下一次 DeepSeek 规划。紧急治疗期间原计划为 suspended，当前提示只保留治疗目标。局部计划完成不等于剧情完成或通关。

## 实际验证

[本次修复与真实运行报告](pokemon/evidence/dual-model-repair.json) · [Actions 运行](https://github.com/atrl/pokemon-jev-readable/actions/runs/35826841651)

- Python：258 项检查，250 通过、8 跳过、0 失败。跳过的是缺少私有存档、固定源码或 FFmpeg 的可选检查。
- Node：40 项通过；语法检查通过。
- 真实 Red Star ROM 的移动、菜单、对白、存档重放回归通过。
- 本次双模型试跑明确停在 `blocked_missing_planner_key`：Actions 未收到 DEEPSEEK_API_KEY，DeepSeek/JEV 均未调用；不宣称已经验证双模型游戏效果或通过月见山。

本机 `.env` 不会自动进入 GitHub Actions。云端运行需设置同名 Repository Secret，并在工作流选择 `planner=deepseek`。密钥不要发到聊天或提交进代码。

```bash
npm run check
npm test
.venv/bin/python -m unittest discover -s pokemon/tests -v
```

[运行参数](pokemon/README.md) · [历史验证索引](docs/VERIFICATION.md) · [已发布回放](pokemon/results/README.md)。完整通关仍未验证。
