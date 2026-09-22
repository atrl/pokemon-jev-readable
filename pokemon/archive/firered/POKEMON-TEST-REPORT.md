# Pokémon FireRed 实机测试报告

## 结论

用户提交的 **FireRed USA v1.0 / GBA** 已在真实 mGBA 0.10.5 上运行，而不是继续使用不兼容的 PyBoy / Game Boy Red 路径。

实际进行了：开机 → 命名 → 主角卧室 → 真新镇 → 大木博士研究所 → 领取妙蛙种子 → 首场劲敌战获胜 → 游戏内保存。

**这段流程是助手查看真实截图后逐步选择的手动测试，并非 Jev 自主游玩。** 202 次基础按键已形成回归夹具，随后从冷启动重放 28,834 帧，验证读取器和模拟器。未修改游戏内存、发放物品、跳过剧情或写入胜利结果。

## 自动测试

本地自动测试 25 项：**24 通过，1 跳过**。运行时间 28.179 秒（一次本地运行，不是性能基准）。

- 11 项真实 ROM / mGBA 集成测试：GBA 启动、身份校验、真实移动、按键释放、跨场景指针、队伍解密、当前出场对象、HP/PP 变化、战斗退出、非修改式读取、内存读取边界、检查点/原生 SRAM 恢复及原始 ROM 未变更，分组覆盖上述内容。
- 13 项解析器/API 客户端单元测试，包括 24 种加密块排列的子测试、校验和错误拒绝、非法动作、请求序列化和失败处理。
- 1 项真实 Jev API 测试跳过：测试环境没有 TYPESAFE_API_KEY。不能据此报告真实 Jev API 或 Jev 驱动的游戏闭环已经通过。

另实测命令行 init / press / show：帧数 600 → 728 → 728；show 不推进游戏。缺少密钥时 agent 返回失败且 session.json 不变，无模拟回答兜底。

## 可复核的实际读数

| 场景 | 观察 |
|---|---|
| 卧室 | 玩家 A；位置 (6,6)；3000 金钱；PalletTown_PlayersHouse_2F |
| 向右按 16 帧、释放后等待 20 帧 | 实际位置变为 (7,6)，held keys 回到 0 |
| 真新镇 | 地图组/编号 (3,0)，SaveBlock 指针已变化，姓名/金钱仍正确 |
| 战斗开始 | 妙蛙种子 Lv5，HP 19/19，TACKLE PP 35；对手小火龙 Lv5，HP 18/18 |
| 战斗中 | 妙蛙种子 HP 15/19，TACKLE PP 33；对手 HP 15/18 |
| 战斗结束 | 妙蛙种子 Lv6，金钱 3080；回到 CB2_Overworld，不再输出残留战斗对象 |
| 原生保存 | 131072 字节 SRAM 从未保存状态发生变化；检查点能恢复相同 SRAM |

检查点恢复逐字节比较了 EWRAM、IWRAM、SRAM、屏幕像素及帧计数；重复恢复后执行相同输入得到相同观察和像素。

## 修正点

1. 改用 mGBA GBA core 和独立 FireRed 地址配置，不复用 Gen 1 地址。
2. 每次重读 gSaveBlock1Ptr / gSaveBlock2Ptr，避免跨地图后读取旧地址。
3. 第三代队伍的加密区域先在 Python 副本里 XOR 解密，按 personality % 24 还原顺序并检查 checksum。
4. 读取 gBattleMons 当前出场对象，而不是假定敌方队伍第一只是当前敌人。
5. 通过 gMain.inBattle 过滤结束后残留战斗数据。
6. 除 libretro state 外，检查点显式保留 SRAM；mGBA libretro 的 unserialize 不替调用方完整恢复 SRAM。
7. 文字输出标记为可能残留或部分打印的 RAM 缓冲区，不当成准确屏幕转录。

## 身份和构建

- ROM：16,777,216 字节；BPRE；revision 0；GBA header checksum 通过。
- ROM SHA-1：`41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc`。
- ROM SHA-256：`3d0c79f1627022e18765766f6cb5ea067f6b5bf7dca115552189ad65a5c3a8ac`。
- 原始 Git blob：`a0d042c360c18b860da3ef71ebac0d826e136cdc`；原文件未修改。
- mGBA 0.10.5 固定源码：`26b7884bc25a5933960f3cdcd98bac1ae14d42e2`。
- GitHub Actions 构建与 ROM 校验：[run 35604598450](https://github.com/atrl/pokemon-jev-readable/actions/runs/35604598450)。这次 CI run 验证的是核心构建和 ROM 身份；25 项游戏/接口测试是在下载该核心和用户 ROM 后于工作容器中运行的，不混称为 CI 的测试成绩。
- 地址及结构参考：pret/pokefirered；40Cakes/pokebot-gen3 的 FireRed 符号表。读取器是只读适配，不运行它们的自动策略。

## 交付与未覆盖范围

本次会话附带 `pokemon-firered-tested.zip`，包含完整 Python 源码、固定地址配置、构建脚本、手动输入回归夹具、自动测试、实际截图、JSON 观察和日志；**不含 ROM、编译核心、私有存档或密钥**。

仓库本分支用于隔离构建/测试记录。原 main、Minecraft 源码和用户上传的 ROM 均保持不变。

尚未验证：真实 Jev API、Jev 驱动长程游戏、全剧情、非空背包和使用道具、捕捉/交换/双打、Mac/Windows 动态库构建。缺少完整 NPC/碰撞/菜单光标解析，不能称为所有界面均可被 Jev 正确读取。

读取模式是 `privileged_read_only_ram`，包含屏幕未必公开的敌方 HP/招式等信息；不能与纯截图智能体直接等同。
