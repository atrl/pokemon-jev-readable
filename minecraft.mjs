/**
 * Minecraft 适配层：观察 → 提供候选 → 执行一个白名单动作。
 * 所有方块、库存、实体都来自真实 Mineflayer bot，不包含模拟游戏。
 * 不自动破坏寻路障碍；只有 Jev/用户选中的 mine_log 会挖方块。
 */
import { once } from 'node:events';
import { setTimeout as sleep } from 'node:timers/promises';

export const WOODS = ['oak', 'birch', 'spruce', 'jungle', 'acacia', 'dark_oak'];
export const LOGS = WOODS.map(wood => `${wood}_log`);
export const PLANKS = WOODS.map(wood => `${wood}_planks`);
const CRAFT_OUTPUTS = [...PLANKS, 'stick', 'crafting_table', 'wooden_pickaxe'];
const USEFUL_ITEMS = new Set([...LOGS, ...CRAFT_OUTPUTS]);
const EMPTY = new Set(['air', 'cave_air', 'void_air']);
const HAZARDS = new Set(['lava', 'water', 'fire', 'magma_block', 'cactus']);

export const TASK = {
  description: '在附近放好或找到一张工作台，并让背包拥有至少 1 把木镐。',
  limits: '仅采集普通树原木；不攻击、不执行聊天命令、不挖地下、不远距离跑图。',
};

export function inventoryCounts(bot) {
  const counts = {};
  for (const item of bot.inventory.items()) {
    counts[item.name] = (counts[item.name] ?? 0) + item.count;
  }
  return counts;
}
export function countItems(inventory, names) {
  return names.reduce((sum, name) => sum + (inventory[name] ?? 0), 0);
}
export function taskComplete(observation) {
  return (observation.inventory.wooden_pickaxe ?? 0) >= 1 && observation.tables.length > 0;
}
const positionKey = p => `${p.x}_${p.y}_${p.z}`;
const plainPosition = p => ({ x: p.x, y: p.y, z: p.z });

/** 纯函数：输入本轮观察，输出有限、具体、可追踪的动作数据。 */
export function buildActions(observation) {
  const { inventory, logs, drops, craftable, tables, placements, walks } = observation;
  const actions = [];
  const plankCount = countItems(inventory, PLANKS);
  const rawWoodCount = countItems(inventory, LOGS);
  const tableAvailable = tables.length > 0 || (inventory.crafting_table ?? 0) > 0;

  for (const drop of drops.slice(0, 3)) {
    actions.push({ id: `pickup_${drop.entityId}`, type: 'pickup', ...drop,
      description: `Pick up dropped ${drop.name} x${drop.count}, ${drop.distance} blocks away.` });
  }
  // 12 块木板足够本例的工作台、木棍和木镐。此上限只防止无意义囤积。
  if (plankCount + rawWoodCount * 4 < 12) {
    for (const block of logs.slice(0, 4)) {
      actions.push({ id: `mine_${positionKey(block.position)}`, type: 'mine_log', ...block,
        description: `Mine ONE ${block.name} at ${JSON.stringify(block.position)}; distance ${block.distance}. Collect its drop afterward.` });
    }
  }
  for (const recipe of craftable) {
    if (PLANKS.includes(recipe.itemName) && plankCount >= 12) continue;
    if (recipe.itemName === 'stick' && (inventory.stick ?? 0) >= 2) continue;
    if (recipe.itemName === 'crafting_table' && tableAvailable) continue;
    if (recipe.itemName === 'wooden_pickaxe' && (inventory.wooden_pickaxe ?? 0) >= 1) continue;
    actions.push({ id: `craft_${recipe.itemName}`, type: 'craft', itemName: recipe.itemName,
      description: `Craft ${recipe.itemName}: perform ONE recipe, producing ${recipe.outputCount}. Materials are currently available.` });
  }
  if ((inventory.crafting_table ?? 0) > 0 && tables.length === 0) {
    for (const target of placements.slice(0, 2)) {
      actions.push({ id: `place_table_${positionKey(target)}`, type: 'place_table', position: target,
        description: `Place the carried crafting table on the checked empty ground at ${JSON.stringify(target)}.` });
    }
  }
  if (tables.length > 0 && tables[0].distance > 3 && !(inventory.wooden_pickaxe >= 1)) {
    actions.push({ id: 'approach_table', type: 'approach_table', position: tables[0].position,
      description: `Walk closer to the observed crafting table (${tables[0].distance} blocks away).` });
  }
  for (const target of walks) {
    actions.push({ id: `walk_${positionKey(target)}`, type: 'walk', position: target,
      description: `Explore the nearby checked standing point ${JSON.stringify(target)}; do not use if gathering/crafting can already progress.` });
  }
  actions.push({ id: 'wait', type: 'wait', description: 'Wait 0.5 seconds for world or inventory updates.' });
  actions.push({ id: 'stop', type: 'stop', description: 'Stop and request human help. This does NOT declare success.' });
  return actions;
}

/** 一个动作超时后，必须停止控制并断开；不允许未完成的旧动作与下一轮重叠。 */
export async function withDeadline(work, timeoutMs, signal, cancel) {
  const controller = new AbortController();
  const abortFromParent = () => controller.abort(signal.reason);
  if (signal?.aborted) abortFromParent();
  else signal?.addEventListener('abort', abortFromParent, { once: true });
  const timer = setTimeout(() => controller.abort(new Error(`动作超过 ${timeoutMs}ms，停止并断开连接。`)), timeoutMs);
  let rejectAbort;
  const interrupted = new Promise((_, reject) => { rejectAbort = reject; });
  const onAbort = () => {
    try { cancel(); } catch { /* 取消失败也必须拒绝继续运行。 */ }
    rejectAbort(controller.signal.reason);
  };
  controller.signal.addEventListener('abort', onAbort, { once: true });
  try {
    if (controller.signal.aborted) onAbort();
    const running = Promise.resolve().then(() => {
      controller.signal.throwIfAborted();
      return work(controller.signal);
    });
    return await Promise.race([running, interrupted]);
  } finally {
    clearTimeout(timer);
    controller.signal.removeEventListener('abort', onAbort);
    signal?.removeEventListener('abort', abortFromParent);
  }
}

export class MinecraftWorld {
  constructor(bot, { goals, Vec3, scanRadius = 16, roamRadius = 32, actionTimeoutMs = 25_000, minHealth = 8 }) {
    this.bot = bot;
    this.goals = goals;
    this.Vec3 = Vec3;
    this.scanRadius = scanRadius;
    this.roamRadius = roamRadius;
    this.actionTimeoutMs = actionTimeoutMs;
    this.minHealth = minHealth;
    this.origin = bot.entity.position.floored();
    this.visited = new Set();
    this.closed = false;
  }
  vector(p) { return new this.Vec3(p.x, p.y, p.z); }
  blockAt(p) { return this.bot.blockAt(this.vector(p)); }
  distance(p) { return this.bot.entity.position.distanceTo(this.vector(p)); }
  assertReady() {
    if (this.closed || !this.bot.entity) throw new Error('Minecraft 连接已关闭。');
    if (this.bot.health < this.minHealth) throw new Error(`生命值 ${this.bot.health} 低于安全阈值，停止。`);
  }

  findBlocks(names, radius = this.scanRadius, count = 12) {
    const ids = names.map(name => this.bot.registry.blocksByName[name]?.id).filter(id => id !== undefined);
    if (ids.length === 0) return [];
    return this.bot.findBlocks({ matching: ids, maxDistance: radius, count })
      .map(position => this.bot.blockAt(position)).filter(Boolean)
      .sort((a, b) => this.distance(a.position) - this.distance(b.position));
  }
  nearestTable() { return this.findBlocks(['crafting_table'], 16, 3)[0] ?? null; }

  // 只检查目标站立点。完整路径仍由寻路器验证，不假设两点之间一定可达。
  safeStandingPoint(x, y, z) {
    const feet = this.blockAt({ x, y, z });
    const head = this.blockAt({ x, y: y + 1, z });
    const ground = this.blockAt({ x, y: y - 1, z });
    return feet && head && ground && EMPTY.has(feet.name) && EMPTY.has(head.name) &&
      ground.boundingBox === 'block' && !HAZARDS.has(ground.name);
  }
  nearbyEmptyGround() {
    const p = this.bot.entity.position.floored();
    return [[1, 0], [-1, 0], [0, 1], [0, -1]]
      .map(([dx, dz]) => ({ x: p.x + dx, y: p.y, z: p.z + dz }))
      .filter(point => this.safeStandingPoint(point.x, point.y, point.z));
  }
  explorationPoints() {
    const p = this.bot.entity.position.floored();
    const result = [];
    for (const [dx, dz] of [[6, 0], [-6, 0], [0, 6], [0, -6]]) {
      for (const dy of [0, 1, -1, 2, -2]) {
        const target = { x: p.x + dx, y: p.y + dy, z: p.z + dz };
        if (Math.hypot(target.x - this.origin.x, target.z - this.origin.z) > this.roamRadius) continue;
        if (this.visited.has(positionKey(target))) continue;
        if (this.safeStandingPoint(target.x, target.y, target.z)) { result.push(target); break; }
      }
    }
    return result;
  }
  currentRecipe(itemName) {
    if (!CRAFT_OUTPUTS.includes(itemName)) throw new Error('不允许合成该物品。');
    const itemId = this.bot.registry.itemsByName[itemName]?.id;
    if (itemId === undefined) return null;
    const table = itemName === 'wooden_pickaxe' ? this.nearestTable() : null;
    if (itemName === 'wooden_pickaxe' && (!table || this.distance(table.position) > 3)) return null;
    const recipe = this.bot.recipesFor(itemId, null, 1, table)[0];
    return recipe ? { recipe, table } : null;
  }

  observe(history = []) {
    this.assertReady();
    const p = this.bot.entity.position;
    const logs = this.findBlocks(LOGS).filter(block => block.position.y >= Math.floor(p.y) &&
      block.position.y <= Math.floor(p.y) + 2).map(block => ({
      name: block.name, position: plainPosition(block.position), distance: +this.distance(block.position).toFixed(1),
    }));
    const drops = Object.values(this.bot.entities).filter(entity => entity.name === 'item').flatMap(entity => {
      const item = entity.getDroppedItem?.();
      if (!item || !USEFUL_ITEMS.has(item.name) || this.distance(entity.position) > this.scanRadius) return [];
      return [{ entityId: entity.id, name: item.name, count: item.count,
        position: plainPosition(entity.position), distance: +this.distance(entity.position).toFixed(1) }];
    }).sort((a, b) => a.distance - b.distance);
    const tables = this.findBlocks(['crafting_table'], 16, 3).map(block => ({
      position: plainPosition(block.position), distance: +this.distance(block.position).toFixed(1),
    }));
    const craftable = CRAFT_OUTPUTS.flatMap(itemName => {
      const available = this.currentRecipe(itemName);
      return available ? [{ itemName, outputCount: available.recipe.result.count }] : [];
    });
    return {
      task: TASK, position: plainPosition(p), health: this.bot.health, food: this.bot.food,
      inventory: inventoryCounts(this.bot), logs, drops, tables, craftable,
      placements: this.nearbyEmptyGround(), walks: this.explorationPoints(), recent: history.slice(-6),
    };
  }

  halt() {
    try { this.bot.pathfinder.setGoal(null); } catch {}
    try { this.bot.clearControlStates(); } catch {}
    try { this.bot.stopDigging(); } catch {}
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.halt();
    try { this.bot.quit('Readable Minecraft agent stopped'); } catch { /* 已断线。 */ }
  }
  async goNear(position, range, signal) {
    signal.throwIfAborted();
    const p = this.vector(position).floored();
    await this.bot.pathfinder.goto(new this.goals.GoalNear(p.x, p.y, p.z, range));
    signal.throwIfAborted();
    this.halt();
  }

  async mineLog(action, signal) {
    await this.goNear(action.position, 2, signal);
    this.assertReady();
    const block = this.blockAt(action.position); // 网络请求期间世界可能变了，重新取方块。
    if (!block || block.name !== action.name || !LOGS.includes(block.name)) throw new Error('目标原木已变化。');
    if (block.position.y < Math.floor(this.bot.entity.position.y)) throw new Error('本例禁止挖脚下方块。');
    if (!this.bot.canDigBlock(block) || !this.bot.canSeeBlock(block)) throw new Error('原木无法触及或被遮挡。');
    const above = this.blockAt(block.position.offset(0, 1, 0));
    if (above && ['sand', 'gravel'].includes(above.name)) throw new Error('上方存在会掉落的方块，拒绝挖掘。');
    signal.throwIfAborted();
    await this.bot.dig(block);
    signal.throwIfAborted();
    await sleep(350, undefined, { signal });
    if (this.blockAt(action.position)?.name === action.name) throw new Error('挖掘后方块未消失。');
    return `已挖下 1 块 ${action.name}；库存是否增加，以后续观察为准。`;
  }
  async pickUp(action, signal) {
    const entity = this.bot.entities[action.entityId];
    const dropped = entity?.getDroppedItem?.();
    if (!dropped || dropped.name !== action.name) throw new Error('掉落物已消失或变化，请重新观察。');
    const before = inventoryCounts(this.bot)[action.name] ?? 0;
    await this.goNear(entity.position, 0, signal);
    for (let i = 0; i < 20; i++) {
      signal.throwIfAborted();
      const after = inventoryCounts(this.bot)[action.name] ?? 0;
      if (after > before) return `实际拾取 ${action.name} x${after - before}。`;
      await sleep(100, undefined, { signal });
    }
    throw new Error('走到掉落物附近，但库存未增加；不把移动当作拾取成功。');
  }
  async craft(itemName, signal) {
    const available = this.currentRecipe(itemName); // 重新检查材料和工作台，而非复用旧配方。
    if (!available) throw new Error(`当前无法合成 ${itemName}，材料或工作台已变化。`);
    const before = inventoryCounts(this.bot)[itemName] ?? 0;
    signal.throwIfAborted();
    await this.bot.craft(available.recipe, 1, available.table);
    signal.throwIfAborted();
    await sleep(200, undefined, { signal });
    const after = inventoryCounts(this.bot)[itemName] ?? 0;
    if (after <= before) throw new Error(`合成 ${itemName} 后没有观察到库存增加。`);
    return `实际合成 ${itemName} x${after - before}。`;
  }
  async placeTable(action, signal) {
    const position = this.vector(action.position);
    if (!this.safeStandingPoint(position.x, position.y, position.z)) throw new Error('放置点已被占用。');
    if (this.distance(position) > 3) throw new Error('放置点已超出附近范围，请重新观察。');
    const item = this.bot.inventory.items().find(item => item.name === 'crafting_table');
    if (!item) throw new Error('背包中没有工作台。');
    const ground = this.bot.blockAt(position.offset(0, -1, 0));
    signal.throwIfAborted();
    await this.bot.equip(item, 'hand');
    signal.throwIfAborted();
    await this.bot.placeBlock(ground, new this.Vec3(0, 1, 0));
    signal.throwIfAborted();
    await sleep(200, undefined, { signal });
    if (this.blockAt(position)?.name !== 'crafting_table') throw new Error('没有观察到工作台成功放置。');
    return `工作台已放在 ${JSON.stringify(action.position)}。`;
  }

  async execute(action, signal) {
    this.assertReady();
    return withDeadline(async actionSignal => {
      switch (action.type) {
        case 'mine_log': return this.mineLog(action, actionSignal);
        case 'pickup': return this.pickUp(action, actionSignal);
        case 'craft': return this.craft(action.itemName, actionSignal);
        case 'place_table': return this.placeTable(action, actionSignal);
        case 'approach_table':
          if (this.blockAt(action.position)?.name !== 'crafting_table') throw new Error('工作台已变化。');
          await this.goNear(action.position, 2, actionSignal);
          return '已接近工作台。';
        case 'walk':
          if (!this.safeStandingPoint(action.position.x, action.position.y, action.position.z)) throw new Error('站立点已变化。');
          await this.goNear(action.position, 0, actionSignal);
          this.visited.add(positionKey(action.position));
          return '已走到附近探索点。';
        case 'wait': await sleep(500, undefined, { signal: actionSignal }); return '已等待世界更新。';
        default: throw new Error(`不支持执行动作类型：${action.type}`);
      }
    }, this.actionTimeoutMs, signal, () => this.close()).finally(() => this.halt());
  }
}

export async function connectMinecraft(config, signal, onFatal) {
  // 放在连接函数内导入，纯逻辑测试不需要安装或连接 Minecraft。
  const { default: mineflayer } = await import('mineflayer');
  const { default: pathfinding } = await import('mineflayer-pathfinder');
  const { Vec3 } = await import('vec3');
  const bot = mineflayer.createBot({ host: config.host, port: config.port,
    username: config.username, auth: config.auth, version: config.version,
    profilesFolder: '.minecraft-auth', respawn: false });
  bot.loadPlugin(pathfinding.pathfinder);
  bot.on('error', onFatal);
  bot.once('kicked', reason => onFatal(new Error(`被服务器踢出：${String(reason)}`)));
  bot.once('death', () => onFatal(new Error('角色死亡；停止，不自动重生。')));
  bot.once('end', reason => onFatal(new Error(`Minecraft 已断开：${String(reason)}`)));
  try {
    await once(bot, 'spawn', { signal: AbortSignal.any([signal, AbortSignal.timeout(45_000)]) });
    await withDeadline(() => bot.waitForChunksToLoad(), 45_000, signal, () => bot.quit());
    const movements = new pathfinding.Movements(bot);
    movements.canDig = false;
    movements.allow1by1towers = false;
    movements.allowParkour = false;
    movements.scafoldingBlocks = [];
    movements.maxDropDown = 2;
    for (const name of HAZARDS) {
      const id = bot.registry.blocksByName[name]?.id;
      if (id !== undefined) movements.blocksToAvoid.add(id);
    }
    bot.pathfinder.setMovements(movements);
    bot.pathfinder.thinkTimeout = 3_000;
    return new MinecraftWorld(bot, { ...config, goals: pathfinding.goals, Vec3 });
  } catch (error) {
    bot.quit();
    throw error;
  }
}
