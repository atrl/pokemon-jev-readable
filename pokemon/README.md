# Red Star → 只读内存 → Jev → 基础按键

[**最近一次真实运行结果**](results/README.md) · [历史结果](results/runs/) · [主工作流](https://github.com/atrl/minecraft-jev-readable/actions/workflows/redstar-memory.yml)

## 这次运行到底发生了什么

用户在 `main` 触发的运行 [35681979746](https://github.com/atrl/minecraft-jev-readable/actions/runs/35681979746) 已真实调用 Jev 12 次。3 次 wait、9 次 A，角色始终在出生房间 `(3,6)`，重复 N64 对话。停止原因是 `budget_reached`，**不是缺少密钥，也不是通关**。

旧版结果只有散落的 JSON 和 PNG，且只有 12 步。这次补齐回放入口、进展统计和参数，未把脚本动作替换为模型动作，也未修改模型策略。增加步数不保证能解决原地循环。

## 如何查看结果

**直接在 GitHub 看：** 打开 [results/README.md](results/README.md)，进入最近一次运行。README 显示 GIF、起止坐标、按键次数和逐步记录。GIF 是每步截图串联，不是实时录像。

**交互回放：** Actions 运行详情 → 页面底部 **Artifacts** → 下载 `pokemon-results-运行号-尝试号` → 解压 → 双击根目录 `index.html`。可以上一步/下一步、拖动时间轴、播放、查看按键概率和完整模型请求。全部资源内嵌，不联网、不需要 Node 或服务器。GitHub 的 HTML 文件页面默认显示源码。

工作流的 Summary 会显示实际 Jev 调用数、动作数、坐标变化、停止原因和下载链接。`publish=true` 时另一个隔离的发布任务只把回放文件写进当前私有仓库，绝不上传 ROM、二进制存档或密钥。Artifacts 保留 30 天；已发布的结果保留在 Git 中。

## 通过 Actions 运行

仓库 Secret `TYPESAFE_API_KEY` 已在上述试跑中有效，不需要重新发送密钥。

Actions → Red Star memory and Jev → Run workflow → 选择 `main`：

| 参数 | 含义 |
|---|---|
| mode | `play` 调用 Jev；`verify` 只测试模拟器和内存 |
| steps | 默认 12，最多接受 1000；不等于任务成功条件 |
| goal | 模型本轮总体目标，不由代码预置剧情路线 |
| publish | 默认勾选，将 GIF 和结果发布到 `pokemon/results/` |

每次从回归测试建立的出生房间开始；跨运行不会自动续玩。普通代码提交或 PR 只做回归，不调用付费模型。显式 play 缺少密钥会报错并保留失败报告，不再用绿色作业掩盖未运行。

## 本地运行和观看

Python 3.10+，建议 3.12。在仓库根目录：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pokemon/requirements.txt
export TYPESAFE_API_KEY='你的密钥'
python pokemon/test_live.py
python pokemon/run.py --state pokemon/.work/bedroom.state \
  --steps 12 --visible --output pokemon/runs/my-run
python pokemon/render_results.py --input pokemon/runs/my-run --output pokemon/runs/my-run/replay
# macOS：
open pokemon/runs/my-run/replay/index.html
```

不指定 `--state` 时，从开机画面开始。`--visible` 显示本机模拟器窗口；Actions 是无窗口运行，只能查看回放，并非直播。

输出目录必须为空。续玩用 `--state pokemon/runs/my-run/last.state`，并换一个新输出目录；保留 `.state` 旁的 `.json` 哈希文件。`.env` 不会自动读取。程序没有自动开场、A* 寻路、固定路线或领取宝可梦的宏动作。九个按键始终可选，按键结果不保证移动。

## 能力边界

只接受本仓库 Red Star ROM 的 SHA-1 `e2d564a5172d38e44b4f4b8f7d9db92f644cf5e9`，不套用原版 Red/FireRed 地址。

已实机验证：开场文字、姓名、出生房间位置与方向键变化、PACK/SAVE 菜单和游标、空队伍和空背包、存档重放。

非空队伍/背包详情、金额、徽章、敌人、战斗与碰撞信息尚未完成实机变化验证，仍不发给 Jev 当可靠事实。文字来自 tile RAM 而非 OCR；背景可能没有可解码文字，滚动文本可能残缺。模型目前缺少已验证的可通行地图/出口信息，不能把这版当成通用通关 Agent。

历史源码 `Rangi42/redstarbluestar@08deafad427f0904f285e515c360003efc19d3dc` 提供地址线索，但重编译二进制与上传 ROM 不完全一致。`evidence/verified-report.json` 是早期回归的历史快照；真实 Jev 的最新状态请看 `results/`，不要再用旧报告的 `blocked_missing_key` 判断当前运行。

## 阅读与测试

先读 `run.py`，再读 `jev.py` 和 `memory.py`。报告生成逻辑在 `render_results.py`，离线模板在 `replay.html`。`test_live.py` 的开场按键属于明确标记的回归夹具，不是 Jev 决策。

```bash
python -m unittest discover -s pokemon/tests -v
python pokemon/test_live.py
```

不根据 `confidence`、API 成功或画面变化推断任务成功。回放报告把“执行了多少”“位置有没有变化”“游戏是否完成”分开显示。
