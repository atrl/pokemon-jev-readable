# Pokémon 运行说明

源码入口与职责划分见 [阅读路线](../docs/READING.md)。只接受仓库中的 Red Star ROM，SHA-1 为 `e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9`。

## 安装与启动

Node.js 22.16+、Python 3.10+（建议 3.12）、FFmpeg。Python 依赖记录在 `requirements.txt`；Node 端没有第三方运行依赖。

```bash
python3 -m venv .venv
.venv/bin/pip install -r pokemon/requirements.txt
cp -n .env.example .env
# 在 .env 中填写 TYPESAFE_API_KEY
npm start
```

打开 http://127.0.0.1:18766 ，观看原生 H.264/HLS 视频、JEV 请求/返回、当前状态、任务证据和时间统计。视频下方的手柄回显真实执行的方向键、A/B、START/SELECT 和等待状态。它是只读动作显示，不发送玩家输入；按键事件可能先于视频画面。正常运行不生成截图。

| 命令 | 用途 |
| --- | --- |
| `npm start` | 游戏与直播网页一起启动，不设步数上限 |
| `npm start -- --resume` | 从最近有效存档继续 |
| `npm run live` | 只启动只读网页 |
| `npm run pokemon -- --resume` | 只运行游戏，配合已有网页 |
| `npm run pokemon:live` | `npm start` 的兼容别名 |
| `npm start -- --steps 5000` | 限定本轮最多执行 5000 个动作后保存退出 |
| `npm start -- --help` | 查看参数，不启动游戏、网页或占用锁 |

启动入口读取根 `.env` 与 `pokemon/.env`，后者优先。直接运行 Python 不会读取 `.env`，需自行导出环境变量。已有运行进程时，独占锁会拒绝第二个控制器。

## 配置与预算

| 配置 | 默认/用途 |
| --- | --- |
| `TYPESAFE_API_KEY` | 必需，保存在本机环境或 Actions Secret |
| `TYPESAFE_MODEL` | `jev-latest` |
| `DEEPSEEK_API_KEY` | 可选；配置后卡住时调用 DeepSeek 高层规划（OpenAI 兼容接口） |
| `DEEPSEEK_MODEL` | `deepseek-chat` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `LIVE_HOST` / `LIVE_PORT` | `127.0.0.1` / `18766` |
| `POKEMON_PYTHON` | 默认仓库 `.venv/bin/python` |
| `POKEMON_FFMPEG` | 可指定 FFmpeg 路径 |
| `--steps` | 默认 0（不设步数上限），支持 0..100000；预算结束不等于通关 |
| `--checkpoint-every` | 默认每 50 步存档 |
| `--max-stalled-steps` | 默认 80，识别无效果操作或明确循环 |
| `--max-recovery-attempts` | 默认 3 次有界恢复，然后保存暂停 |
| `--no-video` | 纯结构化状态测试；无需 FFmpeg |

总体目标是击败联盟冠军并登记名人堂。`--goal` 设置传给模型的总体目标文字；任务规则仍是 Red Star 主线规则库，并非任意自然语言任务规划器。

## 高层规划与战斗资源策略

- 本地确定性层负责识别停滞：连续无新坐标、位置循环、短窗口内反复回城。命中后生成紧凑态势报告，并在配置了 `DEEPSEEK_API_KEY` 时调用 DeepSeek 高层规划模型。
- 规划模型只输出**建议**：一个子目标、目标地图/可交互对象、战斗资源策略（野外逃跑/捕获/开打）和理由；写入存档后可跨步保留、到期或完成时清除。**每个物理按键仍由 JEV 选择**，不接入按钮序列。
- 未配置规划模型时自动退化为纯本地策略，不影响运行。
- 野外战斗默认鼓励逃跑以节省时间（训练家战斗不能逃）；背包有已验证的精灵球且规划要求时，模型会被引导用 ITEM 菜单投球。若规划不可用，抓宠/购买精灵球只作为建议出现，不伪造道具。

## 存档与事件

每次 npm 启动创建新的 `pokemon/runs/<时间>-<进程号>/`，原运行不会被覆盖：

| 文件 | 内容 |
| --- | --- |
| `events.jsonl` | 实际 observation、request、response、decision、result 等事件 |
| `report.json` | 当前状态、实际动作数、耗时与停止原因 |
| `last.state` / `last.state.json` | 模拟器存档及 ROM/state 哈希、步数 |
| `last.progress.json` / `last.campaign.json` | 与同一存档身份绑定的观察和任务记忆 |
| `video/` | HLS 视频，只保留最近约 12–15 秒 |

显式恢复可传 `--state path/to/last.state`，同时保留 manifest 和记忆文件。恢复会验证身份；旧存档的已记录动作仍可用于兼容恢复。运行期间可中断并保存，不会覆盖用户卡带 `.sav`。

网页读取 JSONL，不从当前代码重建历史请求。每个 HTTP 尝试都有独立编号，页面保留最近 2000 个事件；完整日志在本机。历史会话通常只有最后一小段视频。游戏时间只在输入或等待时推进，模型请求期间保持当前真实画面。

## 验证边界

- RAM 观察带 `verified` / `quality` / `source`。地图先验不证明剧情已经发生；未知字段不伪装成事实。
- 主线目标、导航和伤害估计由本地程序提供；每个实际按键仍来自通过校验的 JEV 回答。
- 战斗伤害估计明确保留暴击、未观察命中阶段等限制，不等于胜率。
- 已知路线移动、对白变化、HP/PP 变化属于可观察活动；新坐标和任务完成另行统计。真正无变化先恢复，恢复本身不按键。
- 临时连接故障按 5/10/20/40 秒退避，保留存档与画面；非法答案、不可恢复错误不执行替代动作。
- 视频写入按持续无进展判断故障，允许有界重连；不会把结束画面伪装为继续运行。
- 完整通关尚未验证。版本、证据及未覆盖范围见 [验证索引](../docs/VERIFICATION.md) 和 [实现边界](docs/COMPLETION_AGENT_DESIGN.md)。

## 测试与离线工具

```bash
npm run check
npm test
.venv/bin/python -m unittest discover -s pokemon/tests -v
.venv/bin/python pokemon/test_live.py
```

`test_live.py` 使用明确标记的物理输入夹具，验证真实 ROM，并建立 `pokemon/.work/bedroom.state`；它不参与生产控制。其他 `test_*_live.py` 同样是隔离适配回归。

生成知识数据仍可使用原入口，解析器实际位于 `tools/`，不参与运行时策略：

```bash
.venv/bin/python pokemon/world_data.py /path/to/redstarbluestar
.venv/bin/python pokemon/route_regions.py /path/to/redstarbluestar
```

源码固定为 `Rangi42/redstarbluestar@08deafad427f0904f285e515c360003efc19d3dc`。历史重编译与用户 ROM 不完全一致，因此生成数据仍带源码先验标记。

## Actions 与历史回放

普通 push/PR 只做回归，不调用付费模型。手动工作流的 `mode=play` 才读取 Secret 调用 JEV，默认 12 步，并受作业时限限制；长会话使用本机入口。

[已发布结果](results/README.md) · [历史归档](archive/README.md)。这些是独立会话，不能代表当前直播进度。历史 GIF 来自当时的截图；`render_results.py` 仅呈现已有记录。Actions 的交互回放需下载 Artifact ZIP，解压并打开 `index.html`；网页直播不依赖这些文件。
