/** 阅读入口：main() 连接游戏，runAgent() 执行完整决策循环。 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { chooseWithJev } from './jev.mjs';
import { TASK, buildActions, taskComplete, connectMinecraft } from './minecraft.mjs';

/** decide 是“选择器”，不是动作执行器。生产模式是 Jev，手动模式是用户。 */
export async function runAgent(world, decide, { maxSteps = 40, signal, log = () => {} } = {}) {
  const history = [];
  const blockedUntil = new Map();
  let consecutiveFailures = 0;
  let consecutiveWaits = 0;

  for (let step = 1; step <= maxSteps; step++) {
    signal?.throwIfAborted();

    // 1. 读取真实游戏。每轮重新读，绝不沿用上一轮的假想结果。
    const observation = await world.observe(history);
    if (taskComplete(observation)) {
      return { status: 'completed', reason: '实际库存中有木镐，附近实际存在工作台。', observation };
    }

    // 2. 把真实可用资源、配方、地点转换成候选动作。
    const actions = buildActions(observation).filter(action =>
      (blockedUntil.get(action.id) ?? 0) <= step);
    log('observation', { step, observation, actions });

    // 3. Jev 从候选中选 ONE 个，不生成执行代码。
    const decision = await decide(observation, actions);
    signal?.throwIfAborted();
    // 再映射一次本地候选，绝不相信响应额外附带的坐标或函数。
    const selected = actions.find(action => action.id === decision.action?.id);
    if (!selected) throw new Error('选择器返回了本轮不存在的动作。');
    log('decision', { step, source: decision.source, selected,
      answer: decision.answer, model: decision.model, usage: decision.usage });
    if (selected.type === 'stop') {
      return { status: 'stopped', reason: '选择器请求人工帮助；任务没有宣告完成。' };
    }

    // 4. 本地执行器重新检查前置条件，再执行正常游戏交互。
    try {
      const result = await world.execute(selected, signal);
      history.push({ step, action: selected.id, result, success: true });
      log('result', history.at(-1));
      consecutiveFailures = 0;
      consecutiveWaits = selected.type === 'wait' ? consecutiveWaits + 1 : 0;
      if (consecutiveWaits >= 5) return { status: 'stopped', reason: '连续等待 5 次，停止消耗模型调用。' };
    } catch (error) {
      history.push({ step, action: selected.id, result: error.message, success: false });
      log('result', history.at(-1));
      if (signal?.aborted || world.closed) throw error; // 超时/断线绝不继续旧动作。
      blockedUntil.set(selected.id, step + 4); // 普通失败候选暂时冷却，不强行重放。
      consecutiveWaits = 0;
      if (++consecutiveFailures >= 3) return { status: 'stopped', reason: '连续 3 个动作失败，需要人工检查。' };
    }
    // 回到循环头：重新观察，才知道本轮到底改变了什么。
  }
  const observation = await world.observe(history);
  return taskComplete(observation)
    ? { status: 'completed', reason: '最后一步后实际完成。', observation }
    : { status: 'stopped', reason: `达到 ${maxSteps} 步预算，未确认完成。`, observation };
}

function integer(name, fallback, min, max) {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${name} 必须是 ${min}～${max} 的整数。`);
  return value;
}

async function main() {
  const manual = process.argv.includes('--manual');
  const apiKey = process.env.TYPESAFE_API_KEY;
  if (!manual && !apiKey) throw new Error('请配置 TYPESAFE_API_KEY，或用 npm run manual 进行明确的手动调试。');
  const config = {
    host: process.env.MC_HOST ?? '127.0.0.1', port: integer('MC_PORT', 25565, 1, 65535),
    version: process.env.MC_VERSION ?? '1.16.5', username: process.env.MC_USERNAME ?? 'JevStudent',
    auth: process.env.MC_AUTH ?? 'offline', scanRadius: integer('SCAN_RADIUS', 16, 4, 32),
    roamRadius: integer('ROAM_RADIUS', 32, 6, 128),
    actionTimeoutMs: integer('ACTION_TIMEOUT_MS', 25_000, 1_000, 120_000),
    minHealth: integer('MIN_HEALTH', 8, 1, 20),
  };
  if (!['offline', 'microsoft'].includes(config.auth)) throw new Error('MC_AUTH 只能为 offline 或 microsoft。');
  const controller = new AbortController();
  const stop = () => controller.abort(new Error('用户终止运行。'));
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  fs.mkdirSync('logs', { recursive: true });
  const logFile = path.join('logs', `${new Date().toISOString().replaceAll(':', '-')}.jsonl`);
  function log(type, data) {
    fs.appendFileSync(logFile, JSON.stringify({ time: new Date().toISOString(), type, ...data }) + '\n');
    if (type === 'observation') console.log(`\n第 ${data.step} 步，库存：`, data.observation.inventory);
    if (type === 'decision') console.log(`[${data.source}] 选择：${data.selected.description}`);
    if (type === 'result') console.log(data.success ? '执行结果：' : '执行失败：', data.result);
  }
  let world;
  let terminal;
  try {
    console.log(`模式：${manual ? '手动选动作（不是 AI）' : '真实 Jev API'}\n任务：${TASK.description}`);
    console.log(`连接 ${config.host}:${config.port}；日志：${logFile}`);
    world = await connectMinecraft(config, controller.signal, error => {
      if (!controller.signal.aborted) controller.abort(error);
    });
    if (manual) terminal = createInterface({ input: process.stdin, output: process.stdout });

    async function decide(observation, actions) {
      if (!manual) return chooseWithJev(observation, actions, {
        apiKey, model: process.env.JEV_MODEL ?? 'jev-latest',
        endpoint: process.env.JEV_ENDPOINT ?? 'https://api.typesafe.ai/v1/systemone',
        signal: controller.signal, onRequest: request => log('jev_request', { request }),
      });
      actions.forEach((action, index) => console.log(`  ${index + 1}. ${action.description}`));
      while (true) {
        const input = await terminal.question('输入编号（Ctrl+C 停止）：', { signal: controller.signal });
        const index = Number(input) - 1;
        if (Number.isInteger(index) && actions[index]) return { action: actions[index], source: 'human' };
        console.log('请输入列表中的有效编号。');
      }
    }
    const result = await runAgent(world, decide, {
      maxSteps: integer('MAX_STEPS', 40, 1, 200), signal: controller.signal, log,
    });
    log('finished', result);
    console.log(`\n${result.status === 'completed' ? '成功' : '已停止'}：${result.reason}`);
  } finally {
    terminal?.close();
    world?.close();
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { console.error(`\n停止：${error.message}`); process.exitCode = 1; });
}
