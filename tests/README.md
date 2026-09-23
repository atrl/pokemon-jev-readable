# 统一测试目录

所有测试源码都在此处。运行代码只在 `pokemon/` 和 `live/`，固定模型指令只在 `prompts/`。不要再向运行包里添加 `test_*.py`。

| 目录 | 内容 | 是否启动真实游戏 / 模型 |
| --- | --- | --- |
| `python/` | 观察质量、计划合同、生命周期、循环、路径、脱敏和提示加载 | 否 / 否 |
| `web/` | Node 启动器、网页/SSE/HLS 接口、导入路径、目录合同 | 否 / 否 |
| `integration/` | 真 ROM、真实视频、私有存档、双模型烟雾测试 | 按所选入口 / 模型必须显式授权 |

Python 单测共享 `_paths.py`，实机入口共享 `_bootstrap.py`，不在每个文件复制复杂路径逻辑。虚拟环境激活后在根目录运行：

```bash
npm test
npm run test:python
npm run test:rom
npm run test:integration
```

也可以直接运行 `.venv/bin/python -m unittest discover -s tests/python -v`，避免系统 Python 选错。

## 实机入口（从历史恢复，不是重写或删除测试）

| 文件 | 调用及要求 |
| --- | --- |
| [test_rom.py](integration/test_rom.py) | `python tests/integration/test_rom.py`；默认恢复的 Red Star，生成卧室测试 checkpoint |
| [test_observation.py](integration/test_observation.py) | `--rom ROM --state STATE --output REPORT`；需要原特定 checkpoint，脚本内校验固定哈希 |
| [test_campaign.py](integration/test_campaign.py) | `--rom ROM --state STATE --output DIR`；需要原特定 checkpoint，可加 `--checkpoint-dir` |
| [test_checkpoints.py](integration/test_checkpoints.py) | 命名/战斗初始化的历史私有存档；缺少就明确 skip |
| [test_video.py](integration/test_video.py) | FFmpeg/ffprobe 与可选真实 ROM；缺依赖明确 skip |
| [test_dual_models.py](integration/test_dual_models.py) | 真实 DeepSeek + Jev；必须显式 `--allow-model-calls` |

ROM 回归的开场按键是**测试夹具**，不是正式 Agent 的策略。它写入 `outputs/tests/rom/` 和 `pokemon/.work/bedroom.state`；正式 `pokemon/runs/` 不被覆盖。

## 双模型测试是 opt-in

先创建卧室测试 checkpoint，再运行：

```bash
npm run test:rom
npm run test:dual -- --allow-model-calls --steps 20
```

npm 包装器加载既有 `.env`，并优先使用 `.venv`/`POKEMON_PYTHON`。测试默认从独立卧室夹具启动，而不是从用户最新存档启动；最多 80 次实际按键、4 次 DeepSeek 请求、240 秒。不要把它当通过月见山/通关的验证。

第二次运行请用 `--output outputs/tests/dual-2` 等新目录。可传 `--rom` 和 `--state`，但更换起点时应单独记录场景；通用主循环的入口仍是 `npm start -- --resume`。

没有 opt-in 时，脚本在模型请求前直接退出；缺密钥或没有观测到双模型衔接也返回非零退出码，不以绿色掩盖失败。CI 不调用它，不注入 API 密钥。

所有测试报告、视频、截图与 checkpoint 都留在忽略目录/CI Artifact，不加入 Git。单测替身是明确的合成数据，不算真实模型成绩。固定历史存档不随代码提交，相关测试缺材料时诚实跳过。

## observed 默认路径

`tests/python/test_model_agency.py` 检查观察权限、经验来源与真正的模型控制权，使用明确模拟数据/API，不是游戏成绩。旧策略断言通过 `assisted_helpers.py` 明确运行对照，不冒充默认策略。

`test:rom` 在脚本启动回归后还运行 `tests/integration/test_observed_memory.py`，对真实快照做过滤、隐藏字段反事实、记忆往返和两模型请求序列化；不调用模型。真正的双模型试跑仍需要显式 `--allow-model-calls` 和两把密钥。
