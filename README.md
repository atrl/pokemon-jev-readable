# Pokémon Red Star + JEV

运行仓库中的 `red-star-2020-08-18.gb`：读取游戏状态，提供主线任务、地图与战斗建议，由 JEV 选择每个实际按键。总体目标是击败联盟冠军并进入名人堂；当前实现尚未验证完整通关。

## 实时直播

准备 Python 环境及 `.env` 中的 `TYPESAFE_API_KEY` 后：

```bash
npm run pokemon:live -- --steps 5000
```

打开 http://127.0.0.1:18766 。网页播放 Pokémon 原生实时视频，展示实际 JEV 请求、返回、提示词、状态、任务证据、地图记忆和耗时。视频通过 H.264/HLS 传输，不使用截图轮询。环境安装、存档续玩和运行边界见 [Pokémon 运行说明](pokemon/README.md)。

`current_focus` 由本地程序根据当前任务和游戏界面生成，战斗时会进一步填入操作建议；来源与覆盖顺序见 [字段说明](pokemon/README.md#current_focus-的来源)。

## 已有结果与 Actions

[最近一次已发布的 Actions 归档](pokemon/results/README.md) · [历史结果](pokemon/results/runs/) · [Actions 工作流](https://github.com/atrl/minecraft-jev-readable/actions/workflows/redstar-memory.yml)。归档与本机直播是独立会话，不能将旧的 12 步试跑当成最新直播进度。

早期运行 35681979746 实际调用 JEV 12 次，却重复出生房间的 N64 对话。后续真实测试已推进初始宝可梦、劲敌战、包裹和图鉴，并加入导航、战斗建议与停滞恢复；这些证据仍不等于完整通关。详见 [验证记录](pokemon/README.md#主线规划版的实际运行证据)。

交互回放可从 Actions Artifacts 下载 `pokemon-results-运行号-尝试号`，解压后打开 `index.html`；GitHub 上的 HTML 文件页面默认显示源码。历史 GIF 是归档截图串联，实时观看请使用上面的直播入口。

普通代码提交和 PR 只运行回归，不调用 JEV。手动触发 `Red Star memory and Jev` 工作流时，`mode=play` 调用真实 JEV，`mode=verify` 仅验证内存；`steps` 默认 12，`publish=true` 将结果发布到仓库。密钥使用 Actions Secret `TYPESAFE_API_KEY`。

## 核心代码

- `pokemon/run.py`：观察、选择、执行、存档与有界恢复。
- `pokemon/memory.py`：只读游戏观察及验证边界。
- `pokemon/campaign.py`：持续任务、导航和补给记忆。
- `pokemon/jev.py`：构建实际请求并校验 JEV 按键。
- `live/`：只读直播网页、视频和事件流。
- `pokemon/render_results.py`：从已有结果生成历史回放。

## 历史

[分支整合与归档说明](pokemon/archive/README.md)。旧 FireRed/ROM 验证工作流保留为归档参考；Minecraft 源码仍保留，原始说明位于 [docs/MINECRAFT.md](docs/MINECRAFT.md)。
