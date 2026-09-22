# Red Star → 真实内存 → Jev → 基础按键

只针对仓库中的 `red-star-2020-08-18.gb`，不是原版 Red，也不是 GBA FireRed。

```text
PyBoy 运行用户提供的 ROM
    ↓ 只读 RAM
memory.py：经验证的场景、对白、朝向、局部背景、位置及菜单
    ↓ 跨步观察记忆
progress.py：访问坐标、动作效果、重复交互与当前观察焦点
    ↓ 只保留已验证的模型输入
jev.py：一次 Choice，从九个物理输入中选择一个
    ↓ 白名单校验
emulator.py：按下 → 推进帧 → 松开 → 再观察
```

`run.py` 没有自动开场、固定路线、寻路或领取宝可梦的任务宏。不写 RAM。每轮都提供 up/down/left/right/a/b/start/select/wait；动作不保证成功移动。模型调用期间游戏暂停，下一轮才继续推进。

## 本次实测（2026-09-22）

真实 ROM 回归已通过：出生房间中右键 `(3,6) → (4,6)`、左键 `(3,6) → (2,6)`、下键 `(3,6) → (3,7)`；上方有障碍时坐标不变。主菜单实际显示 PACK/SAVE，Down 让游标 `0 → 1`，图像与 RAM 相符。存档恢复后的像素与观察值可重放。

[实机测试运行](https://github.com/atrl/minecraft-jev-readable/actions/runs/35681323183)。检查结果在本目录 `evidence/verified-report.json`。

此前 GitHub Actions 的 Jev 试运行报告是 **`blocked_missing_key`，0 次模型调用、0 次模型动作**，因为当时 Actions 尚未配置 `TYPESAFE_API_KEY`。

本地直播链路现已完成两轮、合计 **40 次真实 JEV 调用与 40 次按键执行**，HTTP 均为 200，实际返回模型为 `jev-1.13.0`；第二轮从第一轮存档继续。两轮均按 20 步预算结束，未宣称通关；正常运行生成 0 张截图。网页已经在真实浏览器验证新会话自动出现、事件追加和手机布局。[本地验证摘要](evidence/live-stream-verification.json) 保留事件哈希和调用计数，不包含密钥或原始私有日志。

## 本地直接用

在仓库根目录运行（Python 3.10+，建议 3.12）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pokemon/requirements.txt
export TYPESAFE_API_KEY='你的密钥'
python pokemon/run.py --rom red-star-2020-08-18.gb --steps 20 --visible
```

密钥只放环境变量或 GitHub Actions Secret，不要提交或发到聊天中。直接运行 Python 时需自行导出环境变量；下方 npm 启动入口会读取根目录 `.env` 和 `pokemon/.env`（后者优先）。

默认从开机画面开始，每一步由 Jev 决定。输出目录默认 `pokemon/runs/session`，必须不存在或为空；再次运行用 `--output pokemon/runs/another-run`。

可设置 `--goal '你的目标'`；没有脚本规定剧情路线。当前只是接口和小规模行为验证，不保证探索或战斗效果。达到步数预算只表示停止，不表示完成任务或通关。

## 先独立验证内存

```bash
python -m unittest discover -s pokemon/tests -v
python pokemon/test_live.py
```

`test_live.py` 的开场输入是明确标记的回归测试夹具，不是 Jev 决策：它选择预设名字并进入出生房间，随后对比按方向键前后的 RAM 坐标、Start 菜单文字、Down 后的菜单游标、存档恢复后内存与像素是否一致。

测试建立 `pokemon/.work/bedroom.state`，可显式作为模型开始位置：

```bash
python pokemon/run.py --state pokemon/.work/bedroom.state \
  --steps 20 --output pokemon/runs/from-bedroom --visible
```

同一模拟器会话不要同时运行多个控制进程。`last.state` 与旁边的 `.json` 保存了 ROM 和状态哈希；恢复时检查二者。游戏时间只在输入和等待时推进。

## GitHub Actions

工作流 **Red Star memory and Jev** 先运行单元测试和真实 ROM 回归，再尝试最多 12 次 Jev 决策。

仓库的 **Settings → Secrets and variables → Actions → New repository secret** 中添加 `TYPESAFE_API_KEY`。合并到默认分支后可在 Actions 选择此工作流并手动 Run workflow。

密钥缺失时 `pokemon-evidence/jev/report.json` 明确返回 `blocked_missing_key`、`jev_calls: 0`；不会换成本地规则或假答案。CI 对这种情况保留报告，因此不能仅凭工作流绿色就认为 Jev 已运行。

Artifacts 仅保留 JSON 与截图，七天后过期；不会上传 ROM 或二进制存档。正式运行需要检查 `report.json` 中的 `status`、`jev_calls` 和 `executed_actions`。

## 文件阅读顺序

1. `run.py` 的主循环：`snapshot → choose → press → snapshot`。
2. `jev.py` 的 `build_request`：完整模型输入、候选、历史。
3. `memory.py`：只读解析与字段错误处理。
4. `emulator.py`：真实按键、帧推进、存档、截图。
5. `test_live.py`：只用于验证接口的脚本，不参与生产策略。
6. `../live/server.mjs` 和 `../live/public/`：只读 JSONL 事件并通过 SSE 推送至网页。

## 版本与能力边界

仅接受 SHA-1 `e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9`，避免原版 Red 与改版地址混用。地址来自 Red Star 历史源码生成的符号表，另通过当前 ROM 做动态检查。历史源码重编译 SHA-1 与上传文件不同，不声称逐字节同源。

- 实际测试覆盖姓名、开场文字、出生房间坐标与移动、菜单游标、空队伍和空背包、存档重放。
- 非空队伍/背包的解析目前有合成结构测试，尚未完成这份 ROM 的实机交叉验证；原始报告可以保留解码值，但发给 Jev 的这些详情固定为 `null`，不把它们当事实。
- 金额、徽章、战斗类型是候选地址读取，尚未分别通过变化事件验证；它们及碰撞提示只留在本地原始报告，不发送给 Jev。模型现在还接收经过这份 ROM 实测的场景阶段、对白开关、朝向和局部背景网格；当前敌人数据仍不推测。
- 文字来自 `wTileMap` 而不是 OCR。场景由字体状态、实际边框、菜单标签和玩家对齐共同判定；空文字不等于正在加载。对白箭头消失可能只是闪烁，所以 `awaiting_input` 保留未知，不强行判为 false。
- `local_map` 从当前 ROM0 碰撞表与屏幕 tilemap 给出 10×9 背景网格，旧适配误把碰撞表当作图形 bank 的窗口指针，导致房间网格缺失，现已修复。它不识别 NPC、出口、台阶等完整规则，也不会据此删除按键。
- 开场和转场时 RAM 可含未初始化或残留值；有明确结构错误时不会继续执行模型动作。
- Jev `confidence` 不当作任务成功率；使用原始概率和选项并记录。

源码：Rangi42/redstarbluestar @ `08deafad427f0904f285e515c360003efc19d3dc`。
接口：https://docs.typesafe.ai/api ，https://docs.pyboy.dk/ 。


## 游戏画面与 JEV 实时直播

后续已升级连续视频，并从真实存档启动 5,000 步会话；公网浏览器验证 H.264 正常解码和调用持续更新。[视频直播验证快照](evidence/video-live-verification.json) 明确记录当时进度，不表示 5,000 步已经完成。

从仓库根目录执行；Node.js 22.16+，Python 3.12、FFmpeg（需支持 libx264）：

```bash
brew install ffmpeg  # macOS；已安装时跳过
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r pokemon/requirements.txt
cp .env.example .env
# 在本机编辑 .env，填写 TYPESAFE_API_KEY；不要提交密钥。
npm run pokemon:live -- --steps 5000
```

打开 **http://127.0.0.1:18766**。默认从 ROM 开机开始，5,000 步结束后网页继续保留结果，Ctrl+C 关闭服务。重复运行会自动创建新的 `pokemon/runs/<时间>-<进程号>/`，不会覆盖旧会话。`--goal`、`--state` 等参数直接传入现有 Python runner。达到预算不表示通关。

网页和游戏也可分别启动，适合让网页长期运行：

```bash
npm run live                          # 终端 1：只读网页，保持运行
npm run pokemon -- --resume --steps 5000  # 终端 2：从最近有效存档继续
# 首次运行还没有存档时，去掉 --resume。
```

可用 `POKEMON_PYTHON` 指定其他 Python 解释器；默认使用 `.venv/bin/python`。`LIVE_HOST`/`LIVE_PORT` 设置网页监听地址，默认仅本机。手机同一局域网查看可绑定 `0.0.0.0`，再打开本机局域网 IP 的 18766 端口。若使用公网隧道，只转发此只读网页端口；这会公开该仓库直播日志中的游戏观察与模型输入输出。

网页上方直接播放 **Pokémon 原生界面**。模拟器以 60 帧/秒推进每次按键，直接读取其 RGB 帧缓存，FFmpeg 编成 30fps H.264/HLS 视频；不落地 PNG/JPEG，不做屏幕截图轮询。JEV 请求期间游戏暂停，视频连接持续播放当前真实帧；动画在下一次按键时继续。视频有约几秒播放缓冲，调用事件可比画面更早到达。网页静音播放，当前未推送声音。

每一步通过 `events.jsonl` 追加并立即刷新：

```text
observation → jev_request → jev_response → decision → executing → result
```

每次 HTTP 重试单独记录 `attempt`。临时连接错误、非法 JSON 或未通过原有严格校验的模型回答最多重试 3 次，间隔 1/2 秒；每次只重新请求当前观察，校验通过前不执行动作，也不会把模型选项改成另一个答案。401 等不可重试错误及连续失败仍明确停止。请求发出后立刻可见，无需等待整轮结束；页面显示延迟、概率、实际按键、按键前后位置/文本，以及完整请求与响应 JSON。概率是模型返回值，不代表游戏成功率。密钥、Authorization 和常见凭据字段会被过滤。页面不提供控制游戏或读取任意本机文件的接口。

**画面来自连续视频流，没有截图传输、OCR、图片轮询或预设玩法回放。**正常运行不保存 PNG；仅在显式传入 `--screenshots` 时保留可选本地截图证据。RAM 中未经过实机验证的队伍/背包等字段仍显示为不可用，不把未知值当成事实。

编码器遇到暂时性故障时会保留游戏进程并重连，最多每 5 分钟自动恢复 3 次；重连次数及错误会记入事件日志。持续故障仍会明确停止并存档，不把断流当成正常画面。

直播默认预算为 **5,000 步**，支持 `--steps 1..100000`。每 50 步自动更新经哈希校验的 `last.state`，退出时也保存；用 `--checkpoint-every` 调整频率，`--resume` 选择最近有效存档。可用 `--no-video` 关闭编码作纯数据性能测试。

页面显示累计实际耗时、JEV HTTP 延迟、每分钟完成步数、预算剩余时间估计和游戏实际推进秒数。旧 20 步会话仅作为本仓库试跑基线：当时未做视频实时帧节奏控制，因此不能把旧速度直接当成新视频模式耗时，也不是剧情通关基准。

断线时显示连接状态，重连后补齐事件；历史会话可切换。网页每 500ms 增量读取日志，保留最近 40 个会话、每个会话最近 2000 个事件；完整事件继续保存在磁盘。视频磁盘仅保留最近约 12–15 秒，历史会话只保留最后这段画面，并非全程录像。丢失结束事件但进程已退出时标为中断。缺密钥时记录 `blocked_missing_key`，模型调用和动作次数为 0，不启动替代策略。

验证直播代码：

```bash
npm test
npm run check
.venv/bin/python -m unittest discover -s pokemon/tests -v
.venv/bin/python pokemon/test_live.py
```

单元测试使用标明的测试替身，覆盖请求尚未返回时的事件、失败不执行、重连、脱敏及不保存截图；`test_live.py` 单独验证真实 ROM 内存与按键。只有实际配置密钥并运行产生的 JEV 返回才能证明模型已开始玩游戏。


## 输入与提示词优化：避免重复交互

参考 [Claude starter、Nous Pokémon agent 与 PWhiddy 的实际源码](docs/REFERENCE_DESIGN.md)，采用阶段辨识、局部空间、坐标访问和动作反馈；没有加入固定剧情路线、自动连续按 A、寻路或自动战斗。JEV 仍独立选择全部九个物理输入之一。

模型输入由以下部分组成：

- `game`：当前经过验证的 scene、dialog、player、local_map；不知道的字段为 null，范围限制跟随数据一起传入。
- `feedback`：同坐标输入次数、尚未尝试方向、邻居访问次数、反复交互、没有发现新坐标的步数。
- `recent_actions`：最近 8 次紧凑的“按钮 → 位置/对白/文字效果”，避免重复发送大量空白屏幕行。
- `temporal_context`：最近 3 次按键对应的前后状态，按时间先后排列；每个状态只保留场景、对白、位置、已验证朝向和实际帧号。完整网格只发送当前一份。它通常覆盖 4 个决策时刻，不是三张连续视频帧。
- `current_focus`：依据当前已知阶段生成的短期观察焦点，例如结束反复对话后探索未尝试的邻格；用户总体目标始终保留。该字段不指定实际方向，程序不代替 JEV 决策。

“按钮执行成功”与“探索到新坐标”是两个指标；文本变化、关闭菜单、来回走旧格不会被记成新的探索，更不会宣称剧情任务完成。网页相应显示当前阶段、朝向、对白状态、背景网格和循环反馈。

每次存档同时保存 `last.progress.json`，使用 ROM/state 哈希和保存步数绑定；读档后恢复访问与交互记忆。旧版存档没有该文件时，只回放同目录中截止保存步数的已记录动作效果，明确标为 `legacy_observed_effects`，不编造场景阶段。

若连续 80 次本轮动作没有新坐标，且在可验证场景中检测到重复循环，runner 会以 `stalled` 状态保存并停止。可用 `--max-stalled-steps` 调整阈值。未知场景和缺失字段不会被当成确切的碰撞或对白事实。

```bash
.venv/bin/python -m unittest discover -s pokemon/tests -v
.venv/bin/python pokemon/test_observation_live.py --help
```

[RAM 观察实测](evidence/observation-verified.json) 使用停止存档的副本，验证 N64 开关/箭头闪烁、空白自由移动、菜单、朝向和四方向背景预测。它是明确标记的脚本回归，不是 JEV 行为成绩。真实 JEV 输入优化的隔离验证记录于 [decision-context-verification.json](evidence/decision-context-verification.json)：基础状态/记忆版两轮各 30 步，共探索 52 个新坐标，第二轮第 3 步从地图 38 切换至 37；加入三组对齐状态后的最终版重新从同一原始存档测试 30 步，29 次改变坐标，并在第 29 步切换地图。三个测试共 90 次真实 JEV 调用，全程没有脚本代选按钮，原 784 步存档未改变。

这验证了整套状态与提示词修复可以摆脱该循环，不是证明“3”是最优窗口的严格消融实验。正式直播循环保持用户要求的停止状态。
