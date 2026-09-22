# Red Star → 真实内存 → Jev → 基础按键

只针对仓库中的 `red-star-2020-08-18.gb`，不是原版 Red，也不是 GBA FireRed。

```text
PyBoy 运行用户提供的 ROM
    ↓ 只读 RAM
memory.py：位置、文字、菜单游标、队伍、背包
    ↓ 只保留已验证的模型输入
jev.py：一次 Choice，从九个物理输入中选择一个
    ↓ 白名单校验
emulator.py：按下 → 推进帧 → 松开 → 再观察
```

`run.py` 没有自动开场、固定路线、寻路或领取宝可梦的任务宏。不写 RAM。每轮都提供 up/down/left/right/a/b/start/select/wait；动作不保证成功移动。模型调用期间游戏暂停，下一轮才继续推进。

## 本次实测（2026-09-22）

真实 ROM 回归已通过：出生房间中右键 `(3,6) → (4,6)`、左键 `(3,6) → (2,6)`、下键 `(3,6) → (3,7)`；上方有障碍时坐标不变。主菜单实际显示 PACK/SAVE，Down 让游标 `0 → 1`，图像与 RAM 相符。存档恢复后的像素与观察值可重放。

[实机测试运行](https://github.com/atrl/minecraft-jev-readable/actions/runs/35681323183)。检查结果在本目录 `evidence/verified-report.json`。

Jev 试运行报告是 **`blocked_missing_key`，0 次模型调用、0 次模型动作**，因为 Actions 中尚未配置 `TYPESAFE_API_KEY`。不要将这些脚本测试冒充模型已开始玩游戏。

## 本地直接用

在仓库根目录运行（Python 3.10+，建议 3.12）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pokemon/requirements.txt
export TYPESAFE_API_KEY='你的密钥'
python pokemon/run.py --rom red-star-2020-08-18.gb --steps 20 --visible
```

密钥只放环境变量或 GitHub Actions Secret，不要提交或发到聊天中。`.env.example` 只是变量清单，程序不会自动读取 `.env`。

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

## 版本与能力边界

仅接受 SHA-1 `e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9`，避免原版 Red 与改版地址混用。地址来自 Red Star 历史源码生成的符号表，另通过当前 ROM 做动态检查。历史源码重编译 SHA-1 与上传文件不同，不声称逐字节同源。

- 实际测试覆盖姓名、开场文字、出生房间坐标与移动、菜单游标、空队伍和空背包、存档重放。
- 非空队伍/背包的解析目前有合成结构测试，尚未完成这份 ROM 的实机交叉验证；原始报告可以保留解码值，但发给 Jev 的这些详情固定为 `null`，不把它们当事实。
- 金额、徽章、战斗类型是候选地址读取，尚未分别通过变化事件验证；它们及碰撞提示只留在本地原始报告，不发送给 Jev。模型目前使用姓名、位置、文字、明确主菜单的游标及已验证的空队伍/空背包情况。当前敌人数据不推测。
- 文字来自 `wTileMap` 而不是 OCR；背景 tile 可能被解码成字符，滚动对话可能不完整。
- `background_hint` 只根据游戏当前 tileset 碰撞列表给出背景提示，不覆盖 NPC、出口、台阶等全部规则，也不会据此删除按键。
- 开场和转场时 RAM 可含未初始化或残留值；有明确结构错误时不会继续执行模型动作。
- Jev `confidence` 不当作任务成功率；使用原始概率和选项并记录。

源码：Rangi42/redstarbluestar @ `08deafad427f0904f285e515c360003efc19d3dc`。
接口：https://docs.typesafe.ai/api ，https://docs.pyboy.dk/ 。
