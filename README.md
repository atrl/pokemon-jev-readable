# Minecraft + Jev：可阅读的完整实现

本仓库包含 Minecraft 采集任务，以及 [Pokémon Red Star 的 RAM → Jev 按键循环与实时网页](pokemon/README.md)。

**任务：从真实游戏采集原木、合成木板/木棍/工作台、放置工作台，再合成木镐。**
终止条件是“实际库存中有木镐，附近实际存在工作台”；已有工作台也可使用。

这是一个完整的小任务 agent，不是原仓库屠龙路线的完整移植。
它保留 `rmalde/minecraft-agent` 的核心机制：真实结构化状态 → 可用动作集合 → Jev 选择 → Mineflayer 执行 → 新状态。
省去原仓库的大模型高层规划、固定种子路线、龙战斗、原生录屏和状态叠加。任务目标明确写在代码中，不让另外一个模型规划。

Pokémon 直播入口：准备 Python 环境及 `.env` 中的 `TYPESAFE_API_KEY` 后，运行 `npm run pokemon:live -- --steps 20`，打开 http://127.0.0.1:18766 。网页实时展示结构化请求、响应、按键与结果；不使用截图。详见 [直播流程](pokemon/README.md#实时网页直播)。

以下说明针对 Minecraft。

## 1. 运行

需要 Node.js **22.16+**、你有权限操作的 Minecraft Java 服务器、TypeSafe API key。

```bash
npm install
cp .env.example .env
```

在 `.env` 中配置：

```dotenv
TYPESAFE_API_KEY=你的真实密钥
MC_HOST=127.0.0.1
MC_PORT=25565
MC_VERSION=1.16.5
MC_USERNAME=JevStudent
MC_AUTH=offline
```

先启动自己的 **1.16.5** 本地测试服，再运行：

```bash
npm start
```

默认调用真实 Jev API。没有密钥、网络错误、接口失败都会明确停止；不会自动切换固定答案。
机器人以 `JevStudent` 这个玩家连接真实游戏服务器。用正常的同版本 Minecraft 客户端加入同一服务器，就能观察它。
服务器准备见 `server/README.md`。第一次使用新建的、树木附近的和平生存测试世界，不要在重要建筑区运行。

没有 key 但需要先查游戏操作时：

```bash
npm run manual
```

这仍连接真实 Minecraft，由你在终端输入候选编号。不调用模型、没有假模型概率，日志明确标记 `human`。它只是断点式调试入口，不是 AI 效果演示。

## 2. 只读三个文件

```text
agent.mjs       runAgent：完整主循环；main：读取配置和启动
jev.mjs         构造选择题、真实 HTTP 调用、校验答案
minecraft.mjs   读取游戏、提出合法候选、执行挖掘/拾取/合成/放置
```

所有函数都有实现，不需要你补 `observe()`、`execute()` 或 `request()`；依赖库仅负责游戏协议和寻路。

完整主链是：

```text
world.observe(history)                 读取真实库存/原木/掉落物/配方/工作台
        ↓
buildActions(observation)              只提供当前观察支持的具体动作
        ↓
chooseWithJev(observation, actions)     一次 HTTP，请 Jev 选一个本地动作 ID
        ↓
world.execute(selected)                按白名单分派到技能函数
        ↓
Mineflayer 的 dig/craft/placeBlock      真的操作服务器
        ↓
下一次 world.observe()                 验证动作是否改变了世界
```

详见 `docs/READING.md`，按采集一块原木的路径跟踪，不必从文件第一行开始顺读所有辅助函数。

## 3. 哪些是程序写死的，哪些由 Jev 决定

程序负责连接、感知、可行动作、技能、限额和验收：

- 原木只能从实际加载的附近方块中选择，不让模型发明坐标。
- 可合成物来自当前 `recipesFor()` 的结果；执行前重新检查。
- 每次只执行一份配方，不把“输出 4 块木板”误写成“执行配方 4 次”。
- 寻路器禁用自动挖方块和搭方块，因此不会在未选择 mine_log 的情况下为抄近路拆墙。
- 工作台位置由代码检查空位和支撑，模型只选择其中一个位置。
- 原木采集有材料上限、探索有范围、执行有超时、循环有步数上限。
- `stop` 表示停止求助，不表示目标完成。

Jev 决定每一轮从当前候选里执行哪一个。即便只有一个明显合理的动作，也不会偷偷用规则替代 API。
它不输入键鼠坐标、不看截图、不产生 JavaScript、不输出任意游戏命令。

本例只使用一道 Choice：`next_action`。每个具体原木、合成配方、放置点各是一个可选答案；“一次支持多道题”并不是调用 Jev 的前提。

## 4. 观察与候选示例

以下仅说明数据格式，数值不是实测游戏记录：

```js
observation = {
  inventory: { oak_log: 1 },
  logs: [{ name: 'oak_log', position: { x: 3, y: 64, z: 1 }, distance: 3.2 }],
  craftable: [{ itemName: 'oak_planks', outputCount: 4 }],
  // 还有生命值、掉落物、工作台、可探索点、近期动作等。
};

// buildActions() 输出本地执行信息；不是模型输出。
action = {
  id: 'craft_oak_planks',
  type: 'craft',
  itemName: 'oak_planks',
  description: 'Craft oak_planks: perform ONE recipe, producing 4 ...',
};
```

发送给 Jev 的 `criteria` 是 `动作 ID → 描述`。收到 ID 后，在本轮本地动作表中重新查找，最终执行 `bot.craft()`。

## 5. 日志和调试

每次运行生成独立的 `logs/<时间>.jsonl`，包含：

- `observation`：本轮真实观察和候选动作；
- `jev_request`：实际发送的题目/状态，不含 Authorization header；
- `decision`：选中动作、模型返回概率和使用量；
- `result`：执行结果，失败也会记录并进入下一轮历史；
- `finished`：程序验收结果。

不要公开原始日志；它们可能包含你游戏服务器的信息。`.env`、认证缓存和日志已放入 `.gitignore`。

在 VS Code 中打开文件，可在以下位置设断点：

1. `agent.mjs` 的 `const actions = buildActions(...)`：看模型到底可以选什么。
2. `jev.mjs` 的 `return { model, state, questions }`：看发送给模型的内容。
3. `agent.mjs` 的 `world.execute(selected, signal)`：看实际执行的是哪个本地动作。
4. `minecraft.mjs` 的 `await this.bot.craft(...)` / `await this.bot.dig(...)`：看游戏技能调用。

## 6. 已做和未做的验证

```bash
npm test
npm run check
```

创建本工程时通过 **29 个离线测试**和三个源码文件的语法检查。测试包含本地 HTTP 请求/响应链路、候选验证、库存验收、取消/超时和注入防护。
测试中的世界/接口是明确命名的测试替身，不是实机证据。

**未验证：**当前工作容器不能解析 npm/GitHub 下载域名，没有安装游戏依赖，也没有可用的 Minecraft 服务端或 TypeSafe key；因此没有完成 `npm install`、真实服务器连接或真实 Jev 控制的端到端测试。不能声称本工程已在游戏里跑通。
直接依赖版本依据上游源码固定；没有生成虚假的 lockfile。你第一次 `npm install` 成功后应提交生成的 `package-lock.json` 以冻结间接依赖。

## 7. 范围和限制

- 它是“采集与合成”完整任务，不会击败末影龙，也不是重写原视频的通关成绩。
- 没有额外大模型规划器；高层目标由 `TASK` 和 `taskComplete()` 固定。改变任务必须同时改候选、技能和验收，不是只改一句自然语言。
- 最好在树木附近的平坦区域开始；只扫描已加载区块。不会可靠地穿越复杂地形寻找任意远处资源。
- 若叶子遮挡高处原木，可能拒绝挖掘；此示例不自动清理叶子或垫脚砍整棵树。
- 普通交互使用 Mineflayer，不是截图驱动；自然地形/延迟/其他玩家可能造成动作失败。
- 目标点检查不能证明整条路径绝对安全；仍可能卡住或掉血。低血量停止，超时立即停止控制并断开，以避免旧动作继续执行。
- 自动动作会真的改变世界，请只使用自己有权限的测试服。
- 模型概率用来展示和检查响应格式，不视作真实正确率保证。

## 来源

参考原项目的“状态—候选—选择—执行”机制，但这里的任务、中文说明和实现代码是重新编写的，不含原仓库专用路线/录像代码。

- 原项目：https://github.com/rmalde/minecraft-agent
- 原项目决策入口：https://github.com/rmalde/minecraft-agent/blob/main/models.mjs
- TypeSafe 官方 HTTP 接口：https://docs.typesafe.ai/api
- Mineflayer API：https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md
- Pathfinder API：https://github.com/PrismarineJS/mineflayer-pathfinder

查阅日期：2026-09-21。
