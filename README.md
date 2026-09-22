# Pokémon Red Star + Jev

**当前入口只关注 Pokémon。** 运行用户提交的 `red-star-2020-08-18.gb`，从只读内存构造观察，让 Jev 选择基础按键。

## 先看结果

**[最近一次运行：GIF、动作统计和停止原因](pokemon/results/README.md)** · [运行说明](pokemon/README.md) · [Actions](https://github.com/atrl/minecraft-jev-readable/actions/workflows/redstar-memory.yml)

2026-09-22 的首次真实 Jev 试跑：12 次调用、12 次动作（3 次 wait、9 次 A），坐标始终为地图 38 的 `(3,6)`。模型重复触发 N64 对话，没有走出房间。**流程成功不等于游戏取得进展。**

交互回放在每次 Actions 的 Artifacts 中：下载 `pokemon-results-运行号-尝试号`，解压后双击根目录 `index.html`。在 GitHub 上直接看上方结果页的 GIF，不要把 HTML 源码页当成回放页面。

## 运行

Actions → **Red Star memory and Jev** → **Run workflow** → `main`：

- `mode=play` 调用真实 Jev；`mode=verify` 只运行内存回归。
- `steps` 默认 12，是费用/动作预算，不是通关指标。
- `publish=true` 会将本次 GIF、统计和回放写入本私有仓库的 `pokemon/results/`。

密钥使用仓库 Actions Secret `TYPESAFE_API_KEY`。代码提交/PR 检查不调用 Jev，不消耗模型额度。

## 核心代码

`pokemon/run.py`：观察 → 选键 → 执行；`memory.py`：读取内存；`jev.py`：模型请求；`emulator.py`：模拟器控制。

`pokemon/render_results.py`：把真实 JSON/截图转为回放，不调用模型，不修改游戏。完整本地操作见 [pokemon/README.md](pokemon/README.md)。

## 历史

[分支整合与归档说明](pokemon/archive/README.md)。旧 FireRed/ROM 验证工作流已作为参考文件归档，不与主入口混用。

旧 Minecraft 源码保持不变，其原始说明完整保存在 [docs/MINECRAFT.md](docs/MINECRAFT.md)。ROM、密钥和二进制存档不会被结果发布程序重新上传。
