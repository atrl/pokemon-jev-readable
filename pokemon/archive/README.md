# Pokémon 历史分支与检查归档

2026-09-22 整合以 `main@728e629` 为运行代码基线，保留用户原有 ROM 与当时主分支源码。

| 原分支 | 原提交 | 整合方式 |
|---|---|---|
| feat/pokemon-redstar-jev | b2ac2ee8ed3a63d97a93103bcd7d7687de6303b9 | 与原 main 的文件树相同，补入合并祖先，不重复导入代码 |
| verify/red-star-rom-20260922 | bc7a974c4ccc941274fa11f78946d0c6d8a9f9c0 | 原 ROM 检查工作流保存在 redstar/，作为历史参考 |
| test/pokemon-firered | 8e3cc64b7e83a2e63b390584ac24a4a8bb97c044 | 独有的两个工作流与报告保存在 firered/；不启用 FireRed 旧路线 |

整合提交使用这三个分支作为额外父提交，保留完整历史。清理程序仅当分支头仍等于上表 SHA，且该 SHA 已是 main 祖先时执行；先建立 `archive/pokemon-20260922/分支名（斜杠换为连字符）` 标签，再通过 SHA lease 删除旧分支。发生并发更新时保留分支，不强行清理。

此目录里的 YAML 位于 `.github/workflows` 之外，不会自动执行。历史报告反映当时状态，可能写着依赖缺失或 Jev 尚未调用；当前运行结果请以 [results/](../results/README.md) 为准。

当前只维护一个主入口：`.github/workflows/redstar-memory.yml`。
