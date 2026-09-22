# 验证与证据索引

这里按目的列出已有证据，避免把开发过程中的每次修复都堆在运行说明里。完整通关尚未验证，当前游戏状态以实际运行的 `report.json` 和直播事件为准。

## 常规检查

`npm run check` 检查 Node 入口与浏览器模块；`npm test` 覆盖启动、只读 HTTP、SSE、HLS、脱敏与请求展示。`python -m unittest discover -s pokemon/tests` 覆盖观察、规划、响应校验、存档、活动检测及恢复；缺少私有存档的可选回归会明确跳过。

源码整理保留了存档与请求合同，使用真实样本比较重构前后的完整输出；见 [readability-refactor-verification.json](../pokemon/evidence/readability-refactor-verification.json)。这类比较不调用 JEV，也不是新增游戏成绩。

## 真实 ROM 适配

| 证据 | 验证内容 |
| --- | --- |
| [verified-report.json](../pokemon/evidence/verified-report.json) | ROM、开场、位置、菜单、存档回放 |
| [observation-verified.json](../pokemon/evidence/observation-verified.json) | 对话/箭头、朝向、空白自由移动、局部背景 |
| [campaign-observation-verified.json](../pokemon/evidence/campaign-observation-verified.json) | 地图出口、NPC、初期剧情事实、队伍与战斗 |
| [party-registration-verified.json](../pokemon/evidence/party-registration-verified.json) | 昵称确认前队伍尚未初始化的合法中间状态 |
| [battle-intro-and-pp-verified.json](../pokemon/evidence/battle-intro-and-pp-verified.json) | 野外战斗开场和最大 PP |
| [trainer-intro-verified.json](../pokemon/evidence/trainer-intro-verified.json) | 训练师开场需要确认，而非无限等待 |
| [battle-tactics-observation-verified.json](../pokemon/evidence/battle-tactics-observation-verified.json) | 当前攻防/类型和 ROM 属性表 |

以上是只读或隔离物理输入的接口回归；不将它们算作模型自己完成任务。

## 真实 JEV 行为

| 证据 | 实际验证范围 |
| --- | --- |
| [live-stream-verification.json](../pokemon/evidence/live-stream-verification.json) | 初期本地请求、执行和网页链路 |
| [decision-context-verification.json](../pokemon/evidence/decision-context-verification.json) | 近期状态/记忆与脱离房间循环 |
| [campaign-jev-progression.json](../pokemon/evidence/campaign-jev-progression.json) | 初始宝可梦、劲敌战、包裹、图鉴 |
| [trainer-intro-jev-verified.json](../pokemon/evidence/trainer-intro-jev-verified.json) | JEV 推进训练师开场并进入战斗 |
| [progress-recovery-jev-verified.json](../pokemon/evidence/progress-recovery-jev-verified.json) | 已知路返程/重新挑战的 300 步测试，保留失败事实 |
| [video-live-verification.json](../pokemon/evidence/video-live-verification.json) | 原生连续视频链路 |

开发期间多次改过 harness，不能把这些片段耗时相加当作固定版本的通关成绩。旧的缺密钥、12 步 N64 循环、战败和停滞记录仍保留，未改写为成功。

## 历史材料

[Actions 回放](../pokemon/results/README.md)、[归档](../pokemon/archive/README.md)、[参考实现对照](../pokemon/docs/REFERENCE_DESIGN.md)、[主线架构与能力边界](../pokemon/docs/COMPLETION_AGENT_DESIGN.md)。
