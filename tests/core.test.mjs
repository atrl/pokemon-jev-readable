/** 离线测试：明确使用测试替身；不是在真实游戏中跑完任务的证据。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { runAgent } from '../agent.mjs';
import { buildDecisionRequest, validateDecision, chooseWithJev } from '../jev.mjs';
import { TASK, buildActions, taskComplete, inventoryCounts, withDeadline, MinecraftWorld } from '../minecraft.mjs';

function state(overrides = {}) {
  return { task: TASK, position: { x: 0, y: 64, z: 0 }, health: 20, food: 20,
    inventory: {}, logs: [], drops: [], tables: [], craftable: [], placements: [], walks: [], recent: [], ...overrides };
}
function reply(actions, selected = actions[0].id) {
  return { model: 'unit-test-not-jev', answers: { next_action: { type: 'choice', choice: selected,
    confidence: 1, probabilities: Object.fromEntries(actions.map(action => [action.id, action.id === selected ? 1 : 0])) } } };
}
const actions = [
  { id: 'mine_1', type: 'mine_log', description: 'Mine an observed oak log', position: { x: 1, y: 64, z: 0 } },
  { id: 'wait', type: 'wait', description: 'Wait' },
];

test('库存将多个同名物品槽相加', () => {
  assert.deepEqual(inventoryCounts({ inventory: { items: () => [{ name: 'oak_log', count: 2 }, { name: 'oak_log', count: 3 }] } }), { oak_log: 5 });
});
test('完成必须同时拥有木镐和附近工作台', () => {
  assert.equal(taskComplete(state({ inventory: { wooden_pickaxe: 1 } })), false);
  assert.equal(taskComplete(state({ tables: [{ position: {} }] })), false);
  assert.equal(taskComplete(state({ inventory: { wooden_pickaxe: 1 }, tables: [{ position: {} }] })), true);
});
test('候选包含观察到的原木和合法配方，不包含任意命令', () => {
  const options = buildActions(state({ logs: [{ name: 'oak_log', position: { x: 1, y: 64, z: 2 }, distance: 3 }],
    craftable: [{ itemName: 'oak_planks', outputCount: 4 }] }));
  assert.ok(options.some(action => action.type === 'mine_log'));
  assert.ok(options.some(action => action.id === 'craft_oak_planks'));
  assert.ok(!options.some(action => action.type === 'command'));
});
test('无原木、无配方时不会凭空提供采集和合成', () => {
  assert.deepEqual(buildActions(state()).map(action => action.id), ['wait', 'stop']);
});
test('备足材料后不再提供采木', () => {
  const options = buildActions(state({ inventory: { oak_log: 3 }, logs: [{ name: 'oak_log', position: { x: 1, y: 64, z: 2 }, distance: 3 }] }));
  assert.ok(!options.some(action => action.type === 'mine_log'));
});
test('已有工作台和木棍不会继续生产同类物资', () => {
  const options = buildActions(state({ inventory: { crafting_table: 1, stick: 4 },
    craftable: [{ itemName: 'crafting_table', outputCount: 1 }, { itemName: 'stick', outputCount: 4 }] }));
  assert.ok(!options.some(action => action.type === 'craft'));
});
test('放置只发生在拥有工作台且存在已观测落点时', () => {
  const options = buildActions(state({ inventory: { crafting_table: 1 }, placements: [{ x: 1, y: 64, z: 0 }] }));
  assert.ok(options.some(action => action.type === 'place_table'));
});
test('实际请求只有一道动作选择题，state 是真实观察的入参', () => {
  const observation = state();
  const request = buildDecisionRequest(observation, actions);
  assert.equal(request.state, observation);
  assert.deepEqual(Object.keys(request.questions), ['next_action']);
  assert.deepEqual(request.questions.next_action.criteria, { mine_1: actions[0].description, wait: 'Wait' });
});
test('重复候选编号被拒绝', () => {
  assert.throws(() => buildDecisionRequest(state(), [actions[0], actions[0]]), /重复/);
});
test('答案只映射回本地动作对象', () => {
  assert.equal(validateDecision(reply(actions), actions).action, actions[0]);
});
test('非法动作编号被拒绝', () => {
  assert.throws(() => validateDecision(reply(actions, 'teleport'), actions), /合法候选/);
});
test('缺失概率、NaN、总和错误和非最大值选择均被拒绝', () => {
  for (const alter of [
    p => { delete p.answers.next_action.probabilities.wait; },
    p => { p.answers.next_action.probabilities.wait = NaN; },
    p => { p.answers.next_action.probabilities.wait = 0.5; },
    p => { p.answers.next_action.probabilities = { mine_1: 0.1, wait: 0.9 }; },
  ]) {
    const value = reply(actions); alter(value);
    assert.throws(() => validateDecision(value, actions));
  }
});
test('缺少 API key 时失败，不回退模拟结果', async () => {
  await assert.rejects(chooseWithJev(state(), actions, {}), /TYPESAFE_API_KEY/);
});
test('HTTP 请求/响应链路：本地测试服务器，不是真实 Jev', async () => {
  let requestBody;
  let header;
  const server = http.createServer(async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk;
    requestBody = JSON.parse(body); header = request.headers.authorization;
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(reply(actions)));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    const chosen = await chooseWithJev(state(), actions, { apiKey: 'test-only-key',
      endpoint: `http://127.0.0.1:${server.address().port}/v1/systemone` });
    assert.equal(chosen.action, actions[0]);
    assert.equal(header, 'Bearer test-only-key');
    assert.equal(requestBody.questions.next_action.type, 'choice');
    assert.ok(!JSON.stringify(requestBody).includes('test-only-key'));
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
test('401 不返回伪造成功', async () => {
  await assert.rejects(chooseWithJev(state(), actions, { apiKey: 'test',
    fetchImpl: async () => new Response('unauthorized', { status: 401 }) }), /HTTP 401/);
});
test('预先取消时不会调用模型', async () => {
  const controller = new AbortController(); controller.abort(new Error('cancelled'));
  let called = false;
  await assert.rejects(chooseWithJev(state(), actions, { apiKey: 'test', signal: controller.signal,
    fetchImpl: async () => { called = true; } }), /cancelled/);
  assert.equal(called, false);
});
test('动作超时调用取消器，拒绝继续', async () => {
  let cancelled = 0;
  await assert.rejects(withDeadline(() => new Promise(() => {}), 10, undefined, () => cancelled++), /停止并断开/);
  assert.equal(cancelled, 1);
});
test('已取消时不执行动作体', async () => {
  const controller = new AbortController(); controller.abort(new Error('stop now'));
  let executed = false;
  await assert.rejects(withDeadline(() => { executed = true; }, 100, controller.signal, () => {}), /stop now/);
  assert.equal(executed, false);
});
test('执行器白名单拒绝任意命令', async () => {
  const world = new MinecraftWorld({ entity: { position: { floored: () => ({}) } }, health: 20,
    pathfinder: { setGoal() {} }, clearControlStates() {}, stopDigging() {} }, { goals: {}, Vec3: class {} });
  await assert.rejects(world.execute({ type: 'command', command: '/give' }), /不支持执行动作/);
});
test('主循环：执行后重新观察；真实条件成立才完成（测试世界替身）', async () => {
  let observations = 0;
  let executions = 0;
  const world = {
    observe(history) {
      observations++;
      if (executions === 1) {
        assert.equal(history.at(-1).success, true);
        return state({ inventory: { wooden_pickaxe: 1 }, tables: [{ position: {} }] });
      }
      return state({ craftable: [{ itemName: 'wooden_pickaxe', outputCount: 1 }] });
    },
    async execute(action) { assert.equal(action.id, 'craft_wooden_pickaxe'); executions++; return 'test craft'; },
  };
  const result = await runAgent(world, async (_state, options) => ({ action: options.find(a => a.type === 'craft'), source: 'test' }));
  assert.equal(result.status, 'completed'); assert.equal(observations, 2); assert.equal(executions, 1);
});
test('主循环不接受响应注入的动作参数', async () => {
  let seen;
  const world = { observe: () => state(), execute: async action => { seen = action; return 'wait'; } };
  await runAgent(world, async () => ({ action: { id: 'wait', type: 'command' } }), { maxSteps: 1 });
  assert.equal(seen.type, 'wait');
});
test('stop 不是完成，也不执行游戏动作', async () => {
  const world = { observe: () => state(), execute: () => assert.fail('不得执行') };
  const result = await runAgent(world, async (_state, options) => ({ action: options.find(a => a.id === 'stop') }));
  assert.equal(result.status, 'stopped');
});
test('Jev 请求失败不触发任何游戏动作', async () => {
  const world = { observe: () => state(), execute: () => assert.fail('不得执行') };
  await assert.rejects(runAgent(world, async () => { throw new Error('network failed'); }), /network failed/);
});
test('最后一个预算动作完成后仍可正确验收', async () => {
  let executed = false;
  const world = { observe: () => executed ? state({ inventory: { wooden_pickaxe: 1 }, tables: [{}] }) : state(),
    execute: async () => { executed = true; return 'test'; } };
  const result = await runAgent(world, async (_state, options) => ({ action: options.find(a => a.id === 'wait') }), { maxSteps: 1 });
  assert.equal(result.status, 'completed');
});

test('合成执行器：真正调用 bot.craft(recipe, 1, table)，并校验库存增量', async () => {
  let count = 0;
  let actualArgs;
  const recipe = { result: { count: 4 } };
  const bot = { entity: { position: { floored: () => ({}) } }, inventory: { items: () => count ? [{ name: 'oak_planks', count }] : [] },
    craft: async (...args) => { actualArgs = args; count += 4; } };
  const world = new MinecraftWorld(bot, { goals: {}, Vec3: class {} });
  world.currentRecipe = () => ({ recipe, table: null });
  assert.match(await world.craft('oak_planks', new AbortController().signal), /x4/);
  assert.deepEqual(actualArgs, [recipe, 1, null]);
});
test('合成 API 返回成功但库存未增加，执行器仍报失败', async () => {
  const bot = { entity: { position: { floored: () => ({}) } }, inventory: { items: () => [] }, craft: async () => {} };
  const world = new MinecraftWorld(bot, { goals: {}, Vec3: class {} });
  world.currentRecipe = () => ({ recipe: {}, table: null });
  await assert.rejects(world.craft('wooden_pickaxe', new AbortController().signal), /没有观察到库存增加/);
});
test('执行前配方失效不调用 bot.craft', async () => {
  const world = new MinecraftWorld({ entity: { position: { floored: () => ({}) } }, craft: () => assert.fail('不应调用') }, { goals: {}, Vec3: class {} });
  world.currentRecipe = () => null;
  await assert.rejects(world.craft('stick', new AbortController().signal), /当前无法合成/);
});
test('掉落物消失不把“走过去”算作拾取成功', async () => {
  const world = new MinecraftWorld({ entity: { position: { floored: () => ({}) } }, entities: {} }, { goals: {}, Vec3: class {} });
  await assert.rejects(world.pickUp({ entityId: 1, name: 'oak_log' }, new AbortController().signal), /已消失或变化/);
});
test('超时关闭世界后主循环不发起下一次模型调用', async () => {
  let decisions = 0;
  const world = { closed: false, observe: () => state(), execute: async () => { world.closed = true; throw new Error('timeout'); } };
  await assert.rejects(runAgent(world, async (_state, options) => { decisions++; return { action: options.find(a => a.id === 'wait') }; }), /timeout/);
  assert.equal(decisions, 1);
});
