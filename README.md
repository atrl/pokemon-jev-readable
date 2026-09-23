# Pokémon Red Star · DeepSeek + Jev

**真实游戏状态 → DeepSeek 短期规划 → Jev 局部按键 → 执行后验收。**

DeepSeek 决定短期目标；CampaignPlanner 管理记忆、目标解析和计划生命周期；Jev 选择实际按键。网页只读展示视频与决策事件。当前不宣称已通过月见山或通关。

## 文件只按职责放置

```text
pokemon/                   运行逻辑；这里没有测试文件或提示词副本
  run.py                   唯一游戏循环
  planning.py              System Two 请求、返回与校验
  prompt.py / jev.py       System One 的状态组装与调用
  paths.py                 提示词、数据和默认 ROM 的路径解析
  data/                    内存配置、地图、招式和区域数据
  tools/                   必需数据的离线重建工具
prompts/
  system1/button.txt       Jev 固定指令
  system2/planner.txt      DeepSeek 固定系统提示词
live/                      启动器和只读 HLS/SSE 网页
roms/                      恢复的两份原始用户上传文件
tests/                     统一测试入口
  python/                  纯 Python 回归
  web/                     Node、启动器与网页回归
  integration/             实机、视频、特定存档和显式双模型测试
docs/                      架构、变更决策和问题记录
```

[当前架构](docs/ARCHITECTURE.md) · [架构变更记录](docs/DECISIONS.md) · [问题与解决方案](docs/TROUBLESHOOTING.md)

## 安装与运行

需要 Node.js 22.16+、Python 3.10+（建议 3.12）和 FFmpeg。Node 端不需要安装 npm 依赖。

```bash
python3 -m venv .venv
.venv/bin/pip install -r pokemon/requirements.txt
cp -n .env.example .env
# 在 .env 填写 TYPESAFE_API_KEY 和 DEEPSEEK_API_KEY
npm start -- --planner-mode deepseek --steps 200 --planner-call-budget 4 --max-seconds 300
```

打开 <http://127.0.0.1:18766>。从当前会话保存后继续：

```bash
npm start -- --resume --planner-mode deepseek
```

本次恢复的默认游戏文件是 `roms/red-star-2020-08-18.gb`。为兼容旧运行目录，根目录若仍有 `red-star-2020-08-18.gb`，则优先使用它；也可用 `--rom /path/to/game.gb` 明确指定。只支持 SHA-1 为 `e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9` 的 Red Star，不根据扩展名猜内存地址。

FireRed 原上传文件也已恢复到 `roms/`，但只是保留用户资产；**当前 PyBoy 控制器不支持运行 GBA FireRed**。不再要求你从历史手工提取误删文件。文件来源和校验见 [roms/README.md](roms/README.md)。

| 命令 | 用途 |
| --- | --- |
| `npm start -- --resume --planner-mode deepseek` | 恢复最近有效存档及其记忆，启动网页 |
| `npm run live` | 只启动只读网页 |
| `npm run pokemon -- --planner-mode deepseek --no-video` | 仅游戏循环 |
| `npm start -- --help` | 查看参数 |

不传 `--steps` 默认无动作上限；规划请求默认上限为 50 次。调试时应显式限制动作、规划次数和墙钟时间。更新代码前结束旧控制进程，让它保存存档；同一会话不要同时启动两个控制器。

## 配置与提示词

根 `.env.example` 是唯一配置模板。npm 入口加载根 `.env` 与可选 `pokemon/.env`；进程环境优先。直接运行 Python 不自动加载 `.env`。

| 配置 | 用途 |
| --- | --- |
| `TYPESAFE_API_KEY` / `TYPESAFE_MODEL` | Jev 密钥与模型标识 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | 规划密钥与模型标识 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_THINKING` | 端点和思考开关；以账户实际支持为准 |
| `POKEMON_PYTHON` / `POKEMON_FFMPEG` | Python 和 FFmpeg 可执行路径 |
| `LIVE_HOST` / `LIVE_PORT` | 默认仅本机 `127.0.0.1:18766`，不应无鉴权公开 |

`deepseek` 模式缺服务就暂停；`auto` 无规划密钥时明确记录规则回退；`local` 是旧规则对照。三者均需要真实 Jev。

改 System Two 的固定指令只编辑 [prompts/system2/planner.txt](prompts/system2/planner.txt)，不再到 `planning.py` 找大段字符串。System One 的固定指令在 [prompts/system1/button.txt](prompts/system1/button.txt)。动态状态/候选仍由 Python 组装；两者职责见 [提示词说明](prompts/README.md)。本次只移动文本，未改变原模型提示内容；修改后需重启控制进程。

## 测试

先激活虚拟环境，然后使用统一入口：

```bash
source .venv/bin/activate
npm run check && npm test       # tests/web；不启动游戏/模型
npm run test:python             # tests/python；不启动游戏/模型
npm run test:rom                # 真实 ROM、开场、RAM、按键、存档回归；脚本，不是 AI
npm run test:integration        # 原生视频与可选私有 checkpoint；无模型
```

原误删的实机测试已恢复到 `tests/integration/`；默认单测不会扫描这个目录。两把密钥配置好、先运行 `test:rom` 建立独立卧室夹具后，才显式测试双模型：

```bash
npm run test:dual -- --allow-model-calls --steps 20
```

这会消耗 API 额度；上限为 80 动作、4 次规划、240 秒，不自动运行，也不是月见山存档测试。完整命令、指定存档要求和跳过边界见 [tests/README.md](tests/README.md)。

CI 保持 `contents: read`，进行核心测试和独立脚本 ROM 回归，不带模型密钥，不回写 Git。截图/JSON 只作为短期 Actions Artifact，不变成源码提交。绿色 CI 不代表 AI 游戏成功。

## 本地结果和版本控制

每次运行仍写入 `pokemon/runs/<会话>/`，续玩路径不变：`events.jsonl`、`report.json`、`last.state`、manifest、`last.progress.json`、`last.campaign.json` 和 `video/`。`.env`、用户存档及运行目录不会被本次重构移动或删除。

需要离线回放时显式使用 `--screenshots`，再执行：

```bash
.venv/bin/python pokemon/render_results.py --input pokemon/runs/<会话> --output outputs/<会话>
```

浏览器打开输出的 `index.html`。没有截图时不会伪造历史画面。生成的 evidence/archive/results、日志和视频仍排除在 Git 外；源码、可复用测试、必需数据、提示词和原用户资产不是临时生成物。第三方播放器许可证保留。
