# 模型提示词：一个角色，一个文件

| 文件 | 使用者 | 加载处 |
| --- | --- | --- |
| [system1/button.txt](system1/button.txt) | Jev 的局部判断与按键选择 | `pokemon/prompt.py` 的 `BUTTON_INSTRUCTIONS` |
| [system2/planner.txt](system2/planner.txt) | DeepSeek 的目标选择、计划合同与约束 | `pokemon/planning.py` 的 `PLANNER_SYSTEM_PROMPT` |

`pokemon/paths.py::load_prompt()` 相对项目目录读取 UTF-8 文件，不依赖执行命令的工作目录。System One 保留原先将多行合并为空格的方式；System Two 原样读取。文件缺失/空白立即报错，不静默退回另一套指令。

固定行为要求写在上述文件里。游戏快照、候选 `target_ref`、当前计划、失败历史与运行时菜单建议仍由 `prompt.py` / `planning.py` 构造，属于**动态输入**，不是第二份固定系统提示。

本次抽取前后发给模型的固定提示文本逐字相同。`tests/python/test_prompt_files.py` 固定了这次重构的 SHA-256；有意修改提示内容时，应更新该合同测试并在 `docs/DECISIONS.md` 追加原因、影响和验证范围，不能把行为修改隐藏成格式整理。修改提示词后重启游戏进程；不需要改 HTTP 调用逻辑。
