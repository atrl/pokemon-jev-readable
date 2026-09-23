# Pokémon Red Star · DeepSeek + Jev

**真实游戏状态 → DeepSeek 短期规划 → Jev 局部按键 → 执行后验收。**

DeepSeek 决定当前子目标；CampaignPlanner 管理世界记忆、目标解析与计划生命周期；Jev 选择每次实际输入。网页只读展示真实视频和决策事件。本项目不包含 ROM、密钥、存档或预生成回放，不宣称已经通关。

## 安装与运行

需要 Node.js 22.16+、Python 3.10+（建议 3.12）和 FFmpeg。使用你有权使用的 **Red Star 2020-08-18** 文件，放到仓库根目录 `red-star-2020-08-18.gb`（该文件已被忽略，不会入库）。SHA-1 必须为 `e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9`；不是原版 Red，也不是 GBA FireRed。

```bash
python3 -m venv .venv
.venv/bin/pip install -r pokemon/requirements.txt
cp -n .env.example .env
# 在本机 .env 填写 TYPESAFE_API_KEY 和 DEEPSEEK_API_KEY
npm start -- --planner-mode deepseek --steps 200 --planner-call-budget 4 --max-seconds 300
```

打开 <http://127.0.0.1:18766>。Node 启动器不需要 `npm install`；浏览器 HLS 依赖及许可证已经随源码保留。

| 命令 | 用途 |
| --- | --- |
| `npm start -- --resume --planner-mode deepseek` | 从本机最近有效 checkpoint 及对应记忆继续 |
| `npm run live` | 只打开只读网页，不控制游戏 |
| `npm run pokemon -- --planner-mode deepseek --no-video` | 仅运行游戏循环，不启动网页或编码视频 |
| `npm start -- --help` | 查看预算、路径和模式参数 |
| `npm start -- --rom /path/to/file.gb --planner-mode deepseek` | 显式指定本地 ROM |

不传 `--steps` 默认不设动作上限；`--planner-call-budget` 仍默认限制 50 次规划请求。日常调试建议显式设置步数、规划次数和墙钟预算。停止旧进程、保存 checkpoint 后再更新代码，不能让两个控制器同时操作一个会话。

## 配置

根目录 `.env.example` 是唯一配置模板。`npm` 入口读取根 `.env` 和可选的 `pokemon/.env`；后者会覆盖模板文件中的同名配置，已导出的进程环境变量优先。直接运行 Python 不自动加载 `.env`。

| 配置 | 作用 |
| --- | --- |
| `TYPESAFE_API_KEY` / `TYPESAFE_MODEL` | Jev 密钥与模型标识 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | 规划模型密钥与标识；实际响应模型写入事件 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_THINKING` | 规划端点与思考开关；示例默认值不是永久有效的服务承诺 |
| `POKEMON_PYTHON` / `POKEMON_FFMPEG` | 自定义 Python 与 FFmpeg 路径 |
| `LIVE_HOST` / `LIVE_PORT` | 默认 `127.0.0.1:18766`；不要无鉴权暴露到公网 |

`deepseek` 模式缺密钥或规划请求失败会明确暂停；`auto` 无规划密钥时记录 `local_fallback`；`local` 显式使用规则主线对照。三种模式都需要 Jev 密钥，不生成替代模型答案。

## 从旧版本迁移：先保护本地 ROM

本次只清理当前 Git 文件树，不重写历史。`git pull` 会删除以前受跟踪、未修改的 ROM 文件，所以更新前先保存到忽略目录：

```bash
mkdir -p roms
cp -n red-star-2020-08-18.gb roms/red-star-2020-08-18.gb
# 只有原文件存在时才需要上一步；已在其他路径保存则无需复制。
git pull --ff-only
cp -n roms/red-star-2020-08-18.gb red-star-2020-08-18.gb
npm start -- --resume --planner-mode deepseek
```

运行入口保持兼容：默认读根目录 `red-star-2020-08-18.gb`；也可以传 `--rom roms/red-star-2020-08-18.gb`，无需复制回根目录。不自动下载 ROM，也不从历史中秘密提取游戏文件。`.env`、`pokemon/runs/` 和用户存档不在本次清理范围内；仍建议自行备份。

## 结果在哪里

每次运行生成独立的 `pokemon/runs/<时间>-<进程号>/`，由 `.gitignore` 排除。

| 本地产物 | 用途 |
| --- | --- |
| `events.jsonl` | 观察、规划请求、计划、Jev 决策、输入、结果和计划结束事件 |
| `report.json` | 实际调用/动作计数、耗时和停止原因 |
| `last.state` / `last.state.json` | 模拟器状态及 ROM/内容哈希 |
| `last.progress.json` / `last.campaign.json` | 与同一 checkpoint 绑定的进展和计划记忆 |
| `video/` | 原生 HLS 视频；通常只保留最后一小段，不是完整录像 |

显式恢复用 `--state path/to/last.state`，同时保留三个 JSON 旁文件。网页显示实际事件，不根据当前代码重建历史答案。需要离线截图回放时，在运行中添加 `--screenshots`，然后：

```bash
.venv/bin/python pokemon/render_results.py \
  --input pokemon/runs/<会话> --output outputs/<会话>
```

用浏览器打开输出的 `index.html`。生成器和模板属于源码，生成结果不入 Git。不传 `--screenshots` 时不会凭空补出历史画面。

## 文档与验证

- [当前架构与源码阅读顺序](docs/ARCHITECTURE.md)
- [架构决策及版本演进](docs/DECISIONS.md)
- [问题、解决方案与尚未验证的边界](docs/TROUBLESHOOTING.md)

```bash
npm run check && npm test
.venv/bin/python -m unittest discover -s pokemon/tests -v
```

保留的是核心回归**测试源码**，不是测试输出或游戏日志；临时数据由测试在临时目录生成。GitHub Actions 现在只读验证代码、合同和文档，不启动游戏、不使用模型密钥、不自动提交回放。真实游戏调试使用上述本地入口。通过测试不等于通过月见山或通关。

必要的 `redstar-*.json` 是运行所需的 ROM 适配与地图/招式数据，不是 evidence；生成器在 `pokemon/tools/`，更新方法见架构文档。第三方播放器与许可证同样是运行依赖，不应当作临时文件删除。
