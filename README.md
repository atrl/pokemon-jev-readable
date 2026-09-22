# Pokémon Red Star + JEV

一个可以按调用顺序阅读的游戏 agent：**只读游戏状态 → 本地任务与操作建议 → JEV 选择按键 → 执行后验证**。网页展示 Pokémon 原生视频和实际模型请求。

**从 [源码阅读路线](docs/READING.md) 开始。** 安装和运行参数见 [运行说明](pokemon/README.md)。

## 启动

需要 Node.js 22.16+、Python 3.12（支持 3.10+）和 FFmpeg。Node 端只使用内置模块；HLS 浏览器依赖已随仓库提供。

```bash
python3 -m venv .venv
.venv/bin/pip install -r pokemon/requirements.txt
cp -n .env.example .env
# 在 .env 中设置 TYPESAFE_API_KEY
npm start -- --steps 5000
```

打开 http://127.0.0.1:18766 。已有存档时使用 `npm start -- --resume --steps 5000`。当前正在运行的会话不需要因阅读或更新代码而重新启动。

## 代码结构

| 位置 | 责任 |
| --- | --- |
| `pokemon/run.py` | 唯一游戏主循环、生命周期和错误处理 |
| `pokemon/memory.py`、`emulator.py` | 读取真实 RAM、执行真实输入 |
| `pokemon/campaign.py`、`progress.py` | 持续任务、地图与最近动作记忆 |
| `pokemon/prompt.py`、`prompts/button.txt` | 模型状态、`current_focus` 和按键候选 |
| `pokemon/jev.py` | JEV HTTP、重试、答案校验 |
| `pokemon/artifacts.py`、`activity.py` | 存档/日志与停滞恢复 |
| `live/` | 启动入口和只读直播网站 |
| `pokemon/tools/` | 离线生成地图、出口与招式知识，不参与按键控制 |

任务规则和寻路/战斗建议由程序提供，最终执行哪个按键由 JEV 的校验后答案决定。没有其他模型参与高层规划，也不写 RAM。

## 验证与历史

```bash
npm run check
npm test
.venv/bin/python -m unittest discover -s pokemon/tests -v
```

[验证证据索引](docs/VERIFICATION.md) · [已发布 Actions 回放](pokemon/results/README.md) · [历史归档](pokemon/archive/README.md)。数据文件、回放和旧报告保留作证据；第一次阅读可跳过这些目录。

总体目标是完成主线并进入名人堂；步数、接口成功、局部任务完成都不等于已经通关。
