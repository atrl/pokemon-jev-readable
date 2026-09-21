# 阅读指引：只追踪“采一块原木 → 得到木板”

## 先看 agent.mjs 的 runAgent()

不要先读连接代码。主循环依次处理五件事：

```js
const observation = await world.observe(history);    // 真实世界变成数据
const actions = buildActions(observation);            // 数据变成动作菜单
const decision = await decide(observation, actions);  // Jev 选择菜单的一项
await world.execute(selected, signal);                // 执行本地动作
// 下一轮重新 observe；不是让模型自己宣称执行成功。
```

上面为导航用的压缩概览，不是需要另行复制运行的替代代码；实际完整实现就在文件中。

## 然后看 minecraft.mjs 的 observe()

此时先假定角色出生在树边，背包空：

- `inventoryCounts(bot)` 遍历真实物品栏。
- `findBlocks(LOGS)` 找到已加载区块里的原木方块和坐标。
- `currentRecipe()` 检查当前材料能否合成。
- 返回 JSON，不发送屏幕图片。

看返回值即可，不用立即读安全落点、超时、认证代码。

## 看 buildActions() 的 logs 分支

每块观察到的原木变成一个对象：

```js
{
  id: 'mine_3_64_1',
  type: 'mine_log',
  name: 'oak_log',
  position: { x: 3, y: 64, z: 1 },
  description: 'Mine ONE oak_log at ...'
}
```

`id` 用于选择，`type` 用于本地分派，`position` 是观测坐标，`description` 给模型读。
没有给模型一个 `mine(x,y,z)` 工具让它自由填坐标，而是给几个已经验证来源的具体候选。

## 看 jev.mjs 的 buildDecisionRequest()

核心映射：

```js
criteria: Object.fromEntries(actions.map(action => [action.id, action.description]))
```

选项就是候选动作本身。模型返回例如 `choice: "mine_3_64_1"`。
`validateDecision()` 检查 ID、概率范围、概率总和。控制器再次从本地 actions 找到对象，拒绝模型额外附带的执行参数。

## 回到 minecraft.mjs 的 execute() 和 mineLog()

`execute()` 根据 `type` 分派：

```js
case 'mine_log': return this.mineLog(action, actionSignal);
```

`mineLog()`：寻路到附近 → 重读目标方块 → 检查距离/遮挡 → `bot.dig(block)` → 确认方块消失。
这里才真的操作游戏。位置和挖掘方式都不是 Jev 生成的代码。

## 下一轮看 pickup 或 craft

挖完原木后可能出现两种真实结果：

- 自动拾取进背包：下一轮 recipesFor() 发现能合成木板，菜单出现 `craft_oak_planks`。
- 原木掉在地面：下一轮观察掉落实体，菜单出现 `pickup_<id>`。走过去后要验证库存增加。

`craft()` 重新检查配方后调用：

```js
await this.bot.craft(available.recipe, 1, available.table);
```

第二个参数 1 是“一次配方操作”，不是“一件输出物”。原木变木板的一次操作会输出多块木板，最终以库存增量验证。

## 最后再读保护代码

理解一轮数据流之后，再看：

- `taskComplete()`：不让 Jev 宣布成功，使用游戏事实验收。
- `blockedUntil`：普通失败动作冷却几轮。
- `withDeadline()`：超时取消控制并关闭连接，而不是让旧动作在后面继续执行。
- `main()` / `connectMinecraft()`：配置、真实连接、人工调试入口。

## 最小修改练习

1. 在 `TASK.description` 改提示文字，观察请求日志有什么变化；注意只改文字不会改验收。
2. 在 `buildActions()` 暂时去掉探索动作，观察模型能选的范围如何变化。
3. 在手动模式选择采木、拾取、合成各一次，观察真实库存增量。
4. 最后接入 Jev，由它选择相同的菜单。这时游戏代码一行都不必替换。

没有 React、组件目录、模板渲染或伪游戏状态参与上述过程。
