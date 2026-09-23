// Page flow: receive recorded runs/events, select a run, update independent views.
import { createRequestInspector } from "./request-inspector.js";
import { createVideoPlayer } from "./video-player.js";
import { renderCampaign } from "./campaign-panel.js";
import { createControllerView } from "./controller-view.js";
import {
  $,
  node,
  createJsonDetails,
  brief,
  dateValue,
  dateFormat,
  latest,
  findAnswer,
  actionName,
  statusNames,
  eventNames,
  statusClass,
  terminalStatus,
  recoveryExhausted,
  runStatusLabel,
  duration,
  latencyLabel,
} from "./view-helpers.js";

const requestInspector = createRequestInspector($("input-inspector"));
const videoPlayer = createVideoPlayer();
const controllerView = createControllerView($("jev-controller"));
const state = {
  runs: new Map(),
  events: new Map(),
  config: {},
  selected: null,
  autoFollow: true,
  generation: 0,
  visibleGroups: 30,
  expanded: new Set(),
  connection: false,
  timingAnchors: new Map(),
  eventRevision: 0,
  renderedEvents: "",
  streamReady: false,
  historyLoading: false,
};
const MAX_EVENTS = 2000;
const MAX_EXPANDED = 250;
const orderedRuns = () =>
  [...state.runs.values()].sort(
    (a, b) =>
      (dateValue(b.startedAt)?.getTime() ?? 0) -
      (dateValue(a.startedAt)?.getTime() ?? 0),
  );
const eventSequence = (event) => {
  const suffix = String(event.id ?? "").match(/:(\d+)$/);
  return suffix ? Number(suffix[1]) : null;
};
const events = () =>
  [...state.events.values()].sort((a, b) => {
    const aSequence = eventSequence(a);
    const bSequence = eventSequence(b);
    return aSequence !== null && bSequence !== null
      ? aSequence - bSequence
      : (dateValue(a.time)?.getTime() ?? 0) -
          (dateValue(b.time)?.getTime() ?? 0);
  });

function notice(message = "") {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
function setConnection(connected) {
  state.connection = connected;
  if (!connected) state.streamReady = false;
  controllerView.setConnection(connected);
  $("connection").className =
    `connection ${connected ? "connected" : "disconnected"}`;
  $("connection").lastElementChild.textContent = connected
    ? "事件流已连接"
    : "连接中断 · 自动重连";
}
async function fetchJson(url) {
  const response = await fetch(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function upsertRun(run) {
  if (!run?.id) return;
  const existing = state.runs.get(run.id);
  const incomingSample =
    dateValue(run.timing?.sampled_at ?? run.updatedAt)?.getTime() ?? Infinity;
  const existingSample =
    dateValue(existing?.timing?.sampled_at ?? existing?.updatedAt)?.getTime() ??
    0;
  const newer =
    typeof run.eventCount === "number" &&
    typeof existing?.eventCount === "number"
      ? run.eventCount > existing.eventCount ||
        (run.eventCount === existing.eventCount &&
          incomingSample >= existingSample)
      : incomingSample >= existingSample;
  if (!existing || newer) {
    state.runs.set(run.id, run);
    const sample = run.timing?.sampled_at ?? run.updatedAt;
    const anchor = state.timingAnchors.get(run.id);
    if (
      !anchor ||
      anchor.sample !== sample ||
      anchor.terminal !== terminalStatus(run.status)
    ) {
      const elapsed = Number.isFinite(run.timing?.elapsed_ms)
        ? run.timing.elapsed_ms
        : Math.max(
            0,
            (dateValue(run.updatedAt)?.getTime() ?? 0) -
              (dateValue(run.startedAt)?.getTime() ?? 0),
          );
      state.timingAnchors.set(run.id, {
        elapsed,
        received: performance.now(),
        sample,
        terminal: terminalStatus(run.status),
      });
    }
  }
}
function trimEvents() {
  if (state.events.size > MAX_EVENTS) {
    state.events = new Map(
      events()
        .slice(-MAX_EVENTS)
        .map((event) => [event.id, event]),
    );
  }
  const kept = new Set(
    [...state.events.keys()].map((id) => `${state.selected}:${id}`),
  );
  for (const key of state.expanded) {
    if (
      key !== "probabilities" &&
      !key.endsWith(":observation") &&
      !key.endsWith(":decision") &&
      !kept.has(key)
    )
      state.expanded.delete(key);
  }
  while (state.expanded.size > MAX_EXPANDED)
    state.expanded.delete(state.expanded.values().next().value);
}
function setExpanded(key, open) {
  if (open) state.expanded.add(key);
  else state.expanded.delete(key);
  while (state.expanded.size > MAX_EXPANDED)
    state.expanded.delete(state.expanded.values().next().value);
}
function renderRunSelect() {
  const runs = orderedRuns();
  const select = $("run-select");
  const signature = runs.map((run) => `${run.id}:${run.status}`).join("|");
  if (select.dataset.signature !== signature) {
    select.replaceChildren();
    if (!runs.length)
      select.append(node("option", "", "暂无 Pokémon 运行记录"));
    for (const run of runs) {
      const option = node(
        "option",
        "",
        `${dateFormat(run.startedAt, true)} · ${runStatusLabel(run)} · ${run.id}`,
      );
      option.value = run.id;
      select.append(option);
    }
    select.dataset.signature = signature;
  }
  select.disabled = runs.length === 0;
  if (state.selected) select.value = state.selected;
  $("follow-button").setAttribute("aria-pressed", String(state.autoFollow));
  $("follow-button").textContent = state.autoFollow
    ? "跟随最新运行"
    : "返回最新运行";
  $("json-link").href = state.selected
    ? `/api/runs/${encodeURIComponent(state.selected)}`
    : "/api/runs";
}
function renderSetup() {
  const config = state.config.pokemon ?? {};
  const card = node("div", "setup-game");
  card.append(node("h3", "", "Pokémon Red Star"));
  for (const [name, value, positive, negative] of [
    ["Jev API Key", config.keyConfigured, "已配置", "尚未配置"],
    ["Pokémon 模拟器", config.emulatorAvailable, "可用", "尚不可用"],
  ]) {
    const line = node("div", "setup-line");
    line.append(
      node("span", "", name),
      node(
        "span",
        `setup-value ${value === true ? "yes" : ""}`,
        value === true ? positive : value === false ? negative : "尚未验证",
      ),
    );
    card.append(line);
  }
  $("setup-status").replaceChildren(card);
}
async function refreshRuns() {
  const data = await fetchJson("/api/runs");
  for (const run of data.runs ?? []) upsertRun(run);
  state.config = data.config ?? {};
  renderSetup();
  const newest = orderedRuns()[0];
  if (
    newest &&
    (!state.selected || (state.autoFollow && newest.id !== state.selected))
  ) {
    await selectRun(newest.id);
  } else {
    renderRunSelect();
    render();
  }
}
async function selectRun(id) {
  const generation = ++state.generation;
  state.historyLoading = true;
  state.selected = id;
  state.events = new Map();
  state.eventRevision++;
  state.expanded.clear();
  state.visibleGroups = 30;
  renderRunSelect();
  render();
  $("timeline").replaceChildren(
    node("div", "timeline-empty", "正在读取真实运行记录…"),
  );
  try {
    const data = await fetchJson(`/api/runs/${encodeURIComponent(id)}`);
    if (generation !== state.generation) return;
    upsertRun(data.run);
    const receivedDuringFetch = state.events;
    state.events = new Map(
      (data.events ?? [])
        .filter((event) => event?.id)
        .map((event) => [event.id, event]),
    );
    for (const [key, event] of receivedDuringFetch)
      state.events.set(key, event);
    trimEvents();
    state.eventRevision++;
    notice();
    renderRunSelect();
    render();
  } catch (error) {
    if (generation !== state.generation) return;
    notice(`无法读取运行记录：${error.message}。可点击刷新重试。`);
    $("timeline").replaceChildren(
      node("div", "timeline-empty", "运行详情暂不可用。"),
    );
  } finally {
    if (generation === state.generation) state.historyLoading = false;
  }
}
function jsonDetails(label, value, key) {
  return createJsonDetails(label, value, {
    open: state.expanded.has(key),
    onToggle: (open) => setExpanded(key, open),
  });
}
function render(liveEvent = null) {
  const run = state.runs.get(state.selected);
  $("empty-state").hidden = Boolean(run);
  $("run-content").hidden = !run;
  videoPlayer.update(run);
  if (!run) {
    controllerView.update([], null);
    return;
  }
  const list = events();
  controllerView.update(list, run, { liveEvent });
  const start = list.find((event) => event.type === "started");
  const finish = latest(list, (event) => event.type === "finished");
  const response = latest(list, (event) => event.type === "jev_response");
  const results = list.filter((event) => event.type === "result");
  const failures = results.filter((event) => event.success === false).length;
  $("game-label").textContent = "POKÉMON RED STAR";
  $("run-id").textContent = run.id;
  $("run-goal").textContent =
    start?.goal ?? run.goal ?? "记录真实游戏状态、模型决策与执行结果";
  $("run-dates").textContent =
    `开始 ${dateFormat(run.startedAt, true)} · 更新 ${dateFormat(run.updatedAt, true)}`;
  $("run-status").className = `status-pill ${statusClass(run.status)}`;
  $("run-status").textContent = runStatusLabel(run);
  $("run-status").title = brief(run.reason ?? finish?.reason, "");
  const warnings = [];
  if (run.reason) warnings.push(brief(run.reason));
  if (run.truncated)
    warnings.push(
      `本页展示最近 ${run.retainedEvents ?? 2000} 条事件，较早事件仍保留在本机运行记录中。`,
    );
  const updatedAt = dateValue(run.updatedAt)?.getTime();
  const retryAt = dateValue(run.jev_wait?.retry_at)?.getTime();
  const expectedBackoff =
    run.status === "waiting_for_jev" &&
    retryAt &&
    Date.now() <= retryAt + 90000;
  if (
    !terminalStatus(run.status) &&
    !expectedBackoff &&
    updatedAt &&
    Date.now() - updatedAt > 90000
  )
    warnings.push(
      "超过 90 秒未收到此运行的新事件。上方状态为最后记录，不能确认代理仍在推进。",
    );
  $("run-warning").textContent = warnings.join(" ");
  $("run-warning").hidden = !warnings.length;
  const steps = new Set(
    list
      .filter((event) => event.step !== null && event.step !== undefined)
      .map((event) => event.step),
  );
  $("metric-steps").textContent = run.steps ?? steps.size;
  $("metric-step-note").textContent =
    (start?.maxSteps ?? run.maxSteps)
      ? `本次上限 ${start?.maxSteps ?? run.maxSteps} 步`
      : "实际记录";
  $("metric-calls").textContent =
    run.timing?.http_attempts ??
    run.calls ??
    list.filter((event) => event.type === "jev_request").length;
  $("metric-results").textContent = run.results ?? results.length;
  $("metric-result-note").textContent = failures
    ? `其中 ${failures} 次执行失败`
    : "执行后返回";
  const latency = response?.latency_ms;
  $("metric-latency").textContent =
    typeof latency === "number"
      ? latency >= 1000
        ? `${(latency / 1000).toFixed(2)}s`
        : `${Math.round(latency)}ms`
      : "—";
  $("metric-latency-note").textContent = response
    ? `HTTP ${response.httpStatus ?? "未知"} · ${dateFormat(response.time)}`
    : "等待 Jev 响应";
  const signature = `${state.selected}:${state.eventRevision}:${state.visibleGroups}`;
  if (state.renderedEvents !== signature) {
    requestInspector.update(list, run);
    renderTimeline(list);
    renderObservation(list);
    renderDecision(list);
    renderCampaign(list, run);
    state.renderedEvents = signature;
  }
  renderTiming();
  renderComparison();
  const last = list.at(-1);
  if (last)
    $("last-update").textContent =
      `最近事件 ${dateFormat(last.time, true)} · ${eventNames[last.type] ?? last.type}`;
}
function elapsedFor(run) {
  const anchor = state.timingAnchors.get(run.id);
  if (!anchor) return run.timing?.elapsed_ms;
  return (
    anchor.elapsed +
    (anchor.terminal ? 0 : Math.max(0, performance.now() - anchor.received))
  );
}
function jevRetrySeconds(run) {
  if (run.status !== "waiting_for_jev") return null;
  const retryAt = dateValue(run.jev_wait?.retry_at)?.getTime();
  const sampledAt = dateValue(run.timing?.sampled_at)?.getTime();
  const anchor = state.timingAnchors.get(run.id);
  if (retryAt !== undefined && sampledAt !== undefined && anchor) {
    return Math.max(
      0,
      Math.ceil(
        (retryAt -
          sampledAt -
          Math.max(0, performance.now() - anchor.received)) /
          1000,
      ),
    );
  }
  return Number.isFinite(run.jev_wait?.retry_remaining_seconds)
    ? run.jev_wait.retry_remaining_seconds
    : null;
}
function renderRunActivity(run) {
  $("run-status").className = `status-pill ${statusClass(run.status)}`;
  if (run.status === "recovering") {
    const recovery = run.recovery ?? {};
    const attempt = Number.isInteger(recovery.attempt)
      ? `${recovery.attempt}${Number.isInteger(recovery.max_attempts) ? ` / ${recovery.max_attempts}` : ""}`
      : "记录未提供";
    $("run-status").textContent = `正在尝试恢复 · ${attempt}`;
    $("game-phase").textContent =
      `正在重读游戏状态并调整导航建议（尝试 ${attempt}）。后续每个按键仍由 JEV 决策。`;
  } else if (run.status === "waiting_for_jev") {
    const remaining = jevRetrySeconds(run);
    const retry =
      remaining === null
        ? "等待自动重试"
        : remaining > 0
          ? `${remaining} 秒后重试`
          : "等待下一次重试请求";
    const windows = run.jev_wait?.consecutive_windows;
    $("run-status").textContent = `等待 JEV 恢复 · ${retry}`;
    $("game-phase").textContent =
      `JEV 服务暂时不可用${Number.isInteger(windows) ? ` · 第 ${windows} 轮等待` : ""} · ${retry}。游戏暂不执行新按键，视频保持直播。`;
  } else {
    $("run-status").textContent = runStatusLabel(run);
    $("game-phase").textContent =
      run.status === "calling"
        ? "正在等待 JEV 响应 · 游戏停在当前画面，视频流继续播放。"
        : recoveryExhausted(run)
          ? "恢复尝试用尽，运行已存档暂停；可查看保留的视频与恢复记录。"
          : terminalStatus(run.status)
            ? "本轮运行已结束 · 可查看已保留的视频片段与决策记录。"
            : `${statusNames[run.status] ?? "游戏运行中"} · 每次按键与执行结果会同步到下方时间线。`;
  }
}
function renderTiming() {
  const run = state.runs.get(state.selected);
  if (!run) return;
  renderRunActivity(run);
  const timing = run.timing ?? {};
  const elapsed = elapsedFor(run);
  const completed = run.results ?? run.steps ?? 0;
  const budget = run.maxSteps ?? 0;
  const speed =
    elapsed > 0 && completed > 0
      ? (completed * 60000) / elapsed
      : timing.steps_per_minute;
  const remaining = Math.max(0, budget - completed);
  const ended = terminalStatus(run.status);
  $("timing-elapsed").textContent = duration(elapsed);
  $("timing-clock-note").textContent = ended
    ? "本轮已结束 · 耗时已固定"
    : state.connection
      ? "本轮计时中 · 包含模型等待"
      : "按最后记录继续计时 · 事件连接中断";
  $("timing-speed").textContent = Number.isFinite(speed)
    ? `${speed.toFixed(2)} 步 / 分钟`
    : "—";
  $("timing-average").textContent = latencyLabel(timing.avg_latency_ms);
  $("timing-model").textContent = duration(timing.model_ms);
  $("timing-game").textContent = Number.isFinite(timing.game_seconds)
    ? duration(timing.game_seconds * 1000)
    : "—";
  $("budget-label").textContent = budget
    ? `${completed.toLocaleString("zh-CN")} / ${budget.toLocaleString("zh-CN")} 步`
    : `${completed.toLocaleString("zh-CN")} 步`;
  $("budget-progress").max = Math.max(1, budget);
  $("budget-progress").value = Math.min(completed, budget);
  $("budget-estimate").textContent = ended
    ? `本轮停止于 ${completed.toLocaleString("zh-CN")} 步；耗时 ${duration(elapsed)}。`
    : completed >= 5 && budget > 0 && speed > 0
      ? `按本轮均速，剩余 ${remaining.toLocaleString("zh-CN")} 步约需 ${duration((remaining * 60000) / speed)}。`
      : "完成至少 5 步后，按本轮实际速度估算剩余耗时。";
}
function renderComparison() {
  const runs = orderedRuns().slice(0, 20);
  const signature =
    runs
      .map(
        (run) =>
          `${run.id}:${run.status}:${run.results}:${run.timing?.elapsed_ms}:${run.timing?.avg_latency_ms}`,
      )
      .join("|") + state.selected;
  const target = $("timing-comparison");
  if (target.dataset.signature === signature) return;
  const fragment = document.createDocumentFragment();
  for (const run of runs) {
    const row = node("tr", run.id === state.selected ? "selected-run" : "");
    const title = node("td");
    const label =
      run.maxSteps === 20
        ? `${run.id === state.selected ? "当前所选 · " : ""}本地试跑基线`
        : run.id === state.selected
          ? "当前所选运行"
          : "本机运行记录";
    title.append(
      node("strong", "", label),
      node("span", "", dateFormat(run.startedAt, true)),
    );
    title.title = run.id;
    const steps = run.results ?? run.steps ?? 0;
    const elapsed = run.timing?.elapsed_ms;
    const speed =
      run.timing?.steps_per_minute ??
      (elapsed > 0 ? (steps * 60000) / elapsed : null);
    row.append(
      title,
      node("td", "", steps),
      node("td", "mono", duration(elapsed)),
      node("td", "mono", Number.isFinite(speed) ? speed.toFixed(2) : "—"),
      node("td", "mono", latencyLabel(run.timing?.avg_latency_ms)),
    );
    fragment.append(row);
  }
  target.replaceChildren(fragment);
  target.dataset.signature = signature;
}
function plannerReturnValue(event) {
  try {
    return { content: JSON.parse(event.content), model: event.model, usage: event.usage };
  } catch {
    return { content: event.content, model: event.model, usage: event.usage };
  }
}
function eventSummary(event) {
  switch (event.type) {
    case "planner_request":
      return `调用 ${brief(event.request?.model ?? event.model, "DeepSeek")}；发送总目标与结构化 situation。`;
    case "planner_response": {
      let plan = null;
      try {
        plan = JSON.parse(event.content);
      } catch {
        plan = null;
      }
      if (plan && typeof plan === "object")
        return `返回子目标 ${brief(plan.subgoal)} · 验收 ${brief(plan.success?.type)}${plan.reasoning ? `\n${brief(plan.reasoning)}` : ""}`;
      return `返回正文 ${brief(event.content)}`;
    }
    case "planner_validation_error":
      return `模型返回未通过合同校验：${brief(event.error)}。已原样记录，未执行任何按键。`;
    case "planning_retry":
      return `第 ${event.attempt ?? "?"} 次返回无法校验（${brief(event.error)}），重新请求高层规划。`;
    case "plan":
      return `计划 ${brief(event.plan?.subgoal)} · 验收 ${brief(event.plan?.success?.type)}${event.plan?.intent ? `\n${brief(event.plan.intent)}` : ""}`;
    case "plan_outcome":
      return `计划 ${brief(event.status)} · ${brief(event.reason)}${event.subgoal ? ` · ${brief(event.subgoal)}` : ""}`;
    case "plan_review": {
      if (event.forced_continue)
        return "System One 想交回 System Two，但上一步刚生成计划且尚未执行动作；本次强制执行按键，避免只规划不行动。";
      const choice = event.answer?.choice;
      return choice === "replan"
        ? `System One 认为需要战略决策：交给 System Two 规划，本次按键已扣留（原答案 ${actionName(event.button_withheld)} 未执行）。`
        : `System One 选择自行处理（continue）；本次直接按键，不调用 System Two。`;
    }
    case "planning_error":
      return `高层规划失败：${brief(event.error)}${event.http_status ? ` · HTTP ${event.http_status}` : ""}`;
    case "progress": {
      const p = Array.isArray(event.position) ? ` · 位置 ${event.position.join("/")}` : "";
      return `已执行 ${event.executed_actions} 个动作：新格 ${event.new_tiles} · 移动 ${event.movement} · 换图 ${event.map_changes} · 规划 ${event.planning_calls}（计划 ${event.plans}）${event.loop_detected ? " · 检测到循环" : ""}${p}`;
    }
    case "objective":
      return (
        event.objective?.intent ??
        event.objective?.id ??
        "已记录主线目标变化，等待完整观测。"
      );
    case "started":
      return event.goal ?? "开始记录本次运行。";
    case "observation": {
      const observation = event.observation ?? {};
      const p = observation.player;
      const parts = [];
      if (p?.map_id !== undefined) parts.push(`地图 ${p.map_id}`);
      if (p && ["x", "y"].some((key) => p[key] !== undefined))
        parts.push(
          ["x", "y"]
            .filter((key) => p[key] !== undefined)
            .map((key) => `${key.toUpperCase()} ${p[key]}`)
            .join(" · "),
        );
      if (Array.isArray(event.actions))
        parts.push(`${event.actions.length} 个候选动作`);
      return parts.join(" / ") || "已记录本步观测，可展开查看结构化数据。";
    }
    case "jev_request": {
      const request = event.request ?? {};
      const questions = request.questions ?? {};
      const key = Object.keys(questions)[0];
      const criteria = questions[key]?.criteria;
      return `${brief(request.model, "模型未知")} → ${key ?? "选择动作"}${criteria ? ` · ${Object.keys(criteria).length} 个选项` : ""}`;
    }
    case "jev_response": {
      const answer = findAnswer(event);
      return answer?.choice !== undefined
        ? `返回选择 ${actionName(answer.choice)}${typeof answer.confidence === "number" ? ` · 置信度 ${(answer.confidence * 100).toFixed(1)}%` : ""}；等待本地校验。`
        : "已记录响应正文，可展开查看。";
    }
    case "jev_error":
      return event.error === "missing_api_key"
        ? "尚未配置 TYPESAFE_API_KEY，未发起 Jev 请求。"
        : brief(event.error, "调用失败，未提供错误详情");
    case "jev_wait":
      return `JEV 服务暂时不可用；${Number.isFinite(event.retry_after_seconds) ? `${event.retry_after_seconds} 秒后自动重试` : "等待自动重试"}${Number.isInteger(event.consecutive_windows) ? ` · 连续等待 ${event.consecutive_windows} 轮` : ""}。本次等待不执行游戏按键。`;
    case "jev_resumed":
      return `JEV 已成功返回${Number.isInteger(event.consecutive_windows) ? ` · 结束 ${event.consecutive_windows} 轮等待` : ""}，继续处理本步决策。`;
    case "recovery": {
      const parts = ["重读状态并调整导航建议，后续按键仍由 JEV 决策"];
      if (Number.isInteger(event.attempt))
        parts.push(
          `恢复尝试 ${event.attempt}${Number.isInteger(event.max_attempts) ? ` / ${event.max_attempts}` : ""}`,
        );
      if (Number.isInteger(event.no_effect_steps))
        parts.push(`${event.no_effect_steps} 步未观察到有效变化`);
      if (event.loop_kind) parts.push(`重复类型 ${brief(event.loop_kind)}`);
      if (event.failed_button)
        parts.push(`此前按键 ${actionName(event.failed_button)}`);
      if (event.reason) parts.push(brief(event.reason));
      return parts.join(" · ");
    }
    case "decision":
      return `${event.source === "manual" ? "手动选择" : "已确认选择"} ${actionName(event.selected ?? event.answer?.choice)}${event.selected?.description ? `\n${event.selected.description}` : ""}`;
    case "executing":
      return `${actionName(event.action)}${event.action?.description ? `\n${event.action.description}` : ""}`;
    case "result": {
      const parts = [
        event.success === false
          ? "执行失败"
          : event.success === true
            ? "按键已执行"
            : "执行返回",
      ];
      if (event.outcome)
        parts.push(
          event.outcome.new_tile
            ? "探索到新坐标"
            : event.outcome.position_changed
              ? "移动到已访问坐标"
              : "未产生新的坐标探索",
        );
      if (event.error) parts.push(brief(event.error));
      const result = event.result;
      if (typeof result === "string") parts.push(result);
      else if (result && typeof result === "object") {
        if (result.button) parts.push(`按键 ${actionName(result.button)}`);
        const before = result.before?.player;
        const after = result.after?.player;
        if (
          before?.x !== undefined &&
          before?.y !== undefined &&
          after?.x !== undefined &&
          after?.y !== undefined
        ) {
          parts.push(
            `坐标 (${before.x}, ${before.y}) → (${after.x}, ${after.y})`,
          );
        }
        if (result.before?.screen_text && result.after?.screen_text)
          parts.push(
            JSON.stringify(result.before.screen_text.rows) ===
              JSON.stringify(result.after.screen_text.rows)
              ? "游戏文本未变化"
              : "游戏文本已变化",
          );
        if (result.message) parts.push(brief(result.message));
      }
      return parts.join(" · ");
    }
    case "finished":
      return `${runStatusLabel({ ...event.report, status: event.status })}${event.reason ? ` · ${brief(event.reason)}` : ""}`;
    case "error":
      return brief(event.error, "未提供错误详情");
    default:
      return "已记录事件。";
  }
}
function renderTimeline(list) {
  const groups = new Map();
  for (const event of list) {
    const key =
      event.step === undefined || event.step === null
        ? `event-${event.id}`
        : `step-${event.step}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  }
  const groupList = [...groups.values()].reverse();
  const fragment = document.createDocumentFragment();
  for (const group of groupList.slice(0, state.visibleGroups)) {
    const first = group[0];
    const last = group.at(-1);
    const decision = latest(group, (event) => event.type === "decision");
    const executing = latest(group, (event) => event.type === "executing");
    const card = node("article", "step-card");
    const head = node("div", "step-head");
    const title = node("div", "step-title");
    title.append(
      node(
        "span",
        "step-number",
        first.step !== undefined && first.step !== null
          ? `STEP ${String(first.step).padStart(3, "0")}`
          : first.type === "started"
            ? "START"
            : first.type === "finished"
              ? "END"
              : "EVENT",
      ),
    );
    title.append(
      node(
        "span",
        "step-action",
        decision
          ? actionName(decision.selected ?? decision.answer?.choice)
          : executing
            ? actionName(executing.action)
            : (eventNames[last.type] ?? last.type),
      ),
    );
    head.append(title, node("time", "step-time", dateFormat(last.time)));
    const body = node("div", "event-list");
    for (const event of group) {
      const isError =
        event.type === "jev_error" ||
        event.type === "error" ||
        event.success === false ||
        (event.type === "finished" && /^(failed|error)$/.test(event.status));
      const item = node(
        "div",
        `event ${event.type} ${isError ? "is-error" : ""}`,
      );
      item.append(node("span", "event-dot"));
      const top = node("div", "event-top");
      top.append(
        node("span", "event-title", eventNames[event.type] ?? event.type),
        node("time", "event-time", dateFormat(event.time)),
      );
      item.append(top, node("p", "event-summary", eventSummary(event)));
      const meta = [];
      if (event.attempt !== undefined) meta.push(`attempt ${event.attempt}`);
      if (event.httpStatus !== undefined) meta.push(`HTTP ${event.httpStatus}`);
      if (typeof event.latency_ms === "number")
        meta.push(`${Math.round(event.latency_ms)} ms`);
      if (event.source) meta.push(event.source);
      if (meta.length) {
        const tags = node("div", "event-meta");
        for (const value of meta) tags.append(node("span", "meta-chip", value));
        item.append(tags);
      }
      const detailValue =
        event.type === "jev_request"
          ? event.request
          : event.type === "jev_response"
            ? event.response
            : event.type === "planner_response"
              ? plannerReturnValue(event)
              : event;
      const label =
        event.type === "jev_request"
          ? "展开请求 JSON"
          : event.type === "jev_response"
            ? "展开响应 JSON"
            : event.type === "planner_response"
              ? "展开 DeepSeek 返回 JSON"
              : event.type === "result"
                ? "展开执行结果 JSON"
                : "展开事件 JSON";
      item.append(
        jsonDetails(label, detailValue, `${state.selected}:${event.id}`),
      );
      body.append(item);
    }
    card.append(head, body);
    fragment.append(card);
  }
  if (!list.length)
    fragment.append(
      node(
        "div",
        "timeline-empty",
        "本次运行还没有事件。新事件会自动出现在这里。",
      ),
    );
  $("timeline").replaceChildren(fragment);
  $("more-button").hidden = groupList.length <= state.visibleGroups;
  $("more-button").textContent =
    `加载更早记录（剩余 ${Math.max(0, groupList.length - state.visibleGroups)} 组）`;
}
function addStateCell(parent, label, value, wide = false) {
  const cell = node("div", `state-cell ${wide ? "wide" : ""}`);
  cell.append(
    node("span", "", label),
    node("strong", "", brief(value, "未观测到")),
  );
  parent.append(cell);
}
function renderObservation(list) {
  const event = latest(
    list,
    (entry) => entry.observation && typeof entry.observation === "object",
  );
  $("state-time").textContent = event ? dateFormat(event.time) : "等待观测";
  const target = $("observation");
  target.replaceChildren();
  if (!event) {
    target.append(node("p", "empty-copy", "等待游戏返回结构化状态。"));
    return;
  }
  const observation = event.observation;
  const grid = node("div", "state-grid");
  const player = observation.player ?? {};
  addStateCell(grid, "玩家", player.name);
  addStateCell(grid, "地图 ID", player.map_id);
  addStateCell(
    grid,
    "RAM 坐标",
    player.x !== undefined && player.y !== undefined
      ? `X ${player.x} / Y ${player.y}`
      : undefined,
    true,
  );
  addStateCell(
    grid,
    "队伍观测",
    Array.isArray(observation.party)
      ? observation.party.length
        ? `已记录 ${observation.party.length} 项，见完整观测`
        : "当前记录为空"
      : "尚无可靠数据",
    true,
  );
  target.append(grid);
  if (observation.local_map?.rows) {
    target.append(node("h3", "subheading", "附近背景网格"));
    target.append(
      node("pre", "text-display", observation.local_map.rows.join("\n")),
    );
    target.append(
      node(
        "p",
        "state-warning",
        "@ 玩家 · . 背景可走 · # 背景阻挡；不包含完整 NPC、出口与台阶规则。",
      ),
    );
  }
  if (observation.progress) {
    const progress = observation.progress;
    const inBattle =
      observation.scene?.mode === "battle" ||
      observation.battle?.active === true;
    target.append(node("h3", "subheading", "观察到的探索与循环"));
    target.append(
      node(
        "p",
        "empty-copy",
        `已记录 ${progress.visited_tiles ?? 0} 个坐标；连续 ${progress.same_position_steps ?? 0} 次输入未移动。`,
      ),
    );
    target.append(
      node(
        "p",
        progress.loop_detected && !inBattle ? "state-warning" : "empty-copy",
        inBattle
          ? "战斗中地图坐标保持不变通常正常；结合 HP、PP 和战斗结果判断推进。原始位置重复信号保留在完整输入中。"
          : progress.loop_detected
            ? "检测到重复交互/原地操作，模型已收到循环反馈；该信号本身不证明卡住。"
            : "暂无原地循环信号；探索坐标不等于剧情完成。",
      ),
    );
    if (!inBattle)
      target.append(
        node(
          "p",
          "empty-copy",
          `当前位置尚未尝试：${(progress.untried_directions ?? []).map(actionName).join("、") || "四个方向均已尝试"}`,
        ),
      );
  }
  const text = observation.screen_text?.rows;
  if (Array.isArray(text)) {
    target.append(node("div", "state-divider"));
    const heading = node("h3", "subheading", "游戏文本");
    heading.append(node("span", "", "RAM · 非 OCR"));
    target.append(heading);
    const content = text
      .map((row) => (typeof row === "string" ? row.trimEnd() : brief(row)))
      .join("\n")
      .trim();
    target.append(
      content
        ? node("div", "text-display", content)
        : node("p", "empty-copy", "本次观测没有可读文本。"),
    );
  }
  target.append(node("div", "state-divider"));
  const inventory = observation.bag;
  const heading = node("h3", "subheading", "背包观测");
  target.append(heading);
  if (inventory === null || inventory === undefined)
    target.append(node("p", "empty-copy", "尚无可靠背包数据。"));
  else {
    const entries = Array.isArray(inventory)
      ? inventory.map((item) =>
          typeof item === "object" && item !== null
            ? [
                item.name ?? item.item ?? item.id ?? "物品",
                item.count ?? item.quantity ?? "?",
              ]
            : [brief(item), ""],
        )
      : Object.entries(inventory);
    if (!entries.length)
      target.append(node("p", "empty-copy", "当前记录为空。"));
    else {
      const items = node("div", "inventory");
      for (const [name, count] of entries) {
        const chip = node("span", "inventory-chip", name);
        chip.append(node("b", "", count));
        items.append(chip);
      }
      target.append(items);
    }
  }
  target.append(
    node(
      "p",
      "state-warning",
      "开场、菜单和场景切换时，RAM 可能保留旧坐标。请结合游戏文本判断。",
    ),
  );
  if (observation.errors && Object.keys(observation.errors).length)
    target.append(
      node(
        "p",
        "state-warning",
        `观测读取异常：${Object.keys(observation.errors).join("、")}`,
      ),
    );
  target.append(
    jsonDetails(
      "展开完整观测 JSON",
      observation,
      `${state.selected}:observation`,
    ),
  );
}
function probabilityRow([key, value], selected) {
  const row = node("div", `probability ${key === selected ? "selected" : ""}`);
  const head = node("div", "probability-head");
  head.append(
    node("span", "", `${key === selected ? "↳ " : ""}${actionName(key)}`),
    node("span", "", `${(value * 100).toFixed(1)}%`),
  );
  const track = node("div", "probability-track");
  const bar = node("div", "probability-bar");
  bar.style.width = `${Math.max(0, Math.min(100, value * 100))}%`;
  track.append(bar);
  row.append(head, track);
  return row;
}
function renderDecision(list) {
  const target = $("decision");
  target.replaceChildren();
  const event = latest(list, (entry) => entry.type === "decision");
  if (!event) {
    target.append(node("p", "empty-copy", "等待 Jev 返回选择并通过本地校验。"));
    return;
  }
  const answer = findAnswer(event) ?? {};
  target.append(
    node("div", "decision-choice", actionName(event.selected ?? answer.choice)),
  );
  if (event.selected?.description)
    target.append(
      node("p", "decision-description", event.selected.description),
    );
  const tags = node("div", "decision-tags");
  tags.append(node("span", "meta-chip", event.source ?? "来源未提供"));
  if (event.step !== undefined)
    tags.append(node("span", "meta-chip", `STEP ${event.step}`));
  if (typeof answer.confidence === "number")
    tags.append(
      node(
        "span",
        "meta-chip",
        `置信度 ${(answer.confidence * 100).toFixed(1)}%`,
      ),
    );
  target.append(tags);
  const probabilities = Object.entries(answer.probabilities ?? {})
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .sort((a, b) => b[1] - a[1]);
  if (probabilities.length) {
    const heading = node("h3", "subheading", "模型返回的选择概率");
    heading.append(node("span", "", `${probabilities.length} OPTIONS`));
    target.append(heading);
    for (const entry of probabilities.slice(0, 6))
      target.append(probabilityRow(entry, answer.choice));
    if (probabilities.length > 6) {
      const details = node("details", "probability-more");
      details.open = state.expanded.has("probabilities");
      details.append(
        node("summary", "", `查看其余 ${probabilities.length - 6} 个选项`),
      );
      for (const entry of probabilities.slice(6))
        details.append(probabilityRow(entry, answer.choice));
      details.addEventListener("toggle", () => {
        if (details.isConnected) setExpanded("probabilities", details.open);
      });
      target.append(details);
    }
  }
  target.append(
    jsonDetails("展开决策 JSON", event, `${state.selected}:decision`),
  );
}

$("run-select").addEventListener("change", (event) => {
  state.autoFollow = false;
  void selectRun(event.target.value);
});
$("follow-button").addEventListener("click", () => {
  state.autoFollow = true;
  const newest = orderedRuns()[0];
  if (newest && newest.id !== state.selected) void selectRun(newest.id);
  else renderRunSelect();
});
$("more-button").addEventListener("click", () => {
  state.visibleGroups = Math.min(MAX_EVENTS, state.visibleGroups + 30);
  render();
});
$("refresh-button").addEventListener("click", async () => {
  $("refresh-button").disabled = true;
  try {
    await refreshRuns();
    if (state.selected) await selectRun(state.selected);
    notice();
  } catch (error) {
    notice(`刷新失败：${error.message}。正在保留已收到的记录。`);
  } finally {
    $("refresh-button").disabled = false;
  }
});
const stream = new EventSource("/api/events");
stream.addEventListener("open", () => {
  state.streamReady = false;
  setConnection(true);
  void (async () => {
    await refreshRuns();
    // Before the first SSE update there is no cursor to replay on reconnect.
    // Reload the selected history too; selectRun merges concurrent new events.
    if (state.selected) await selectRun(state.selected);
  })().catch((error) =>
    notice(`无法读取运行列表：${error.message}。可点击刷新重试。`),
  );
});
stream.addEventListener("error", () => setConnection(false));
stream.addEventListener("ready", () => {
  state.streamReady = true;
  setConnection(true);
});
stream.addEventListener("reset", () => {
  state.streamReady = false;
  void (async () => {
    await refreshRuns();
    if (state.selected) await selectRun(state.selected);
  })().catch((error) =>
    notice(`补全直播记录失败：${error.message}。可点击刷新重试。`),
  );
});
stream.addEventListener("update", (message) => {
  try {
    const data = JSON.parse(message.data);
    if (!data.run?.id || !data.event?.id) return;
    upsertRun(data.run);
    const newest = orderedRuns()[0];
    if (newest && state.autoFollow && newest.id !== state.selected) {
      void selectRun(newest.id);
    }
    if (data.run.id === state.selected) {
      state.events.set(data.event.id, data.event);
      trimEvents();
      state.eventRevision++;
      render(state.streamReady && !state.historyLoading ? data.event : null);
    }
    renderRunSelect();
  } catch (error) {
    notice(`收到无法读取的事件：${error.message}。可刷新重新同步。`);
  }
});
renderSetup();
render();
setInterval(renderTiming, 1000);
void refreshRuns().catch((error) =>
  notice(`无法读取运行列表：${error.message}。可点击刷新重试。`),
);
let metadataRefreshing = false;
setInterval(async () => {
  if (metadataRefreshing) return;
  metadataRefreshing = true;
  try {
    await refreshRuns();
  } catch (error) {
    notice(`运行状态刷新失败：${error.message}。当前展示的是已收到的记录。`);
  } finally {
    metadataRefreshing = false;
  }
}, 10000);
