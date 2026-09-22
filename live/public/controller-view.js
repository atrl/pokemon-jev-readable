// Read-only action echo: only a fresh, live executing event can animate a press.
import { actionName, runStatusLabel, terminalStatus } from "./view-helpers.js";

const KEYS = [
  "up",
  "down",
  "left",
  "right",
  "a",
  "b",
  "select",
  "start",
  "wait",
];
const ACTION_EVENTS = new Set(["decision", "executing", "result"]);
const sequence = (event) => {
  const match = String(event?.id ?? "").match(/:(\d+)$/);
  const value = match ? Number(match[1]) : null;
  return Number.isSafeInteger(value) ? value : null;
};
function eventButton(event) {
  let value = event?.button;
  if (event?.type === "decision")
    value ??= event.selected ?? event.answer?.choice;
  if (event?.type === "executing") value ??= event.action;
  if (event?.type === "result") value ??= event.result?.button;
  if (value && typeof value === "object") value = value.button ?? value.id;
  return KEYS.includes(value) ? value : null;
}

export function controllerSnapshot(events, run = {}) {
  const actions = events.filter((event) => ACTION_EVENTS.has(event?.type));
  const event = actions.at(-1) ?? null;
  const executed = [...actions]
    .reverse()
    .find((item) => item.type !== "decision" && eventButton(item));
  const button = eventButton(event);
  const last = executed
    ? `最近动作 ${actionName(eventButton(executed))} · STEP ${executed.step ?? "—"}`
    : "尚无执行记录";
  let phase = "idle",
    label = "等待动作记录";
  if (terminalStatus(run.status)) {
    phase = "ended";
    label = runStatusLabel(run);
  } else if (run.status === "waiting_for_jev") {
    phase = "network-wait";
    label = "JEV 服务暂不可用 · 等待重试";
  } else if (run.status === "recovering") {
    phase = "recovering";
    label = "重新观察 · 调整建议";
  } else if (
    [
      "calling",
      "request_error",
      "received",
      "observing",
      "starting",
      "waiting",
    ].includes(run.status)
  ) {
    phase = "thinking";
    label =
      run.status === "request_error"
        ? "JEV 请求失败 · 等待后续记录"
        : "JEV 正在选择下一步";
  } else if (event?.type === "decision") {
    phase = "selected";
    label = button
      ? `已选择 ${actionName(button)} · 尚未执行`
      : "已收到选择 · 按键记录未知";
  } else if (event?.type === "executing") {
    phase = "executing";
    label =
      button === "wait"
        ? "释放按键 · 等待游戏推进"
        : button
          ? `执行 ${actionName(button)}`
          : "执行按键记录未知";
  } else if (event?.type === "result") {
    phase = event.success === false ? "failed" : "result";
    label =
      event.success === false
        ? "动作执行失败"
        : event.success === true
          ? "动作已执行 · 结果已返回"
          : "动作结果已返回";
  }
  return {
    phase,
    label,
    button,
    last,
    event,
    step: run.steps ?? event?.step ?? null,
    lastButton: eventButton(executed),
    lastEvent: executed,
  };
}

export function createControllerView(
  root,
  { now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout } = {},
) {
  const keys = new Map(
    KEYS.map((key) => [
      key,
      root.querySelector(`[data-controller-key="${key}"]`),
    ]),
  );
  const field = (name) => root.querySelector(`[data-controller="${name}"]`);
  const highWater = new Map();
  let runId = null,
    connected = false,
    timer = null,
    generation = 0,
    pressedEvent = null;
  let snapshot = controllerSnapshot([], {});

  function clearPress() {
    generation++;
    if (timer !== null) clearTimer(timer);
    timer = null;
    pressedEvent = null;
    for (const element of keys.values()) element.classList.remove("is-pressed");
  }
  function show() {
    root.dataset.phase =
      !connected && snapshot.phase !== "ended"
        ? "disconnected"
        : snapshot.phase;
    field("phase").textContent =
      !connected && snapshot.phase !== "ended"
        ? "事件流重连中 · 保留最近记录"
        : snapshot.label;
    field("step").textContent = `STEP ${snapshot.step ?? "—"}`;
    field("last").textContent = snapshot.last;
    const actualWait =
      snapshot.button === "wait" &&
      (snapshot.phase === "executing" ||
        (snapshot.phase === "result" && snapshot.event?.success === true));
    keys.get("wait").textContent =
      !connected && snapshot.phase !== "ended"
        ? "LINK"
        : actualWait
          ? "WAIT"
          : ({
              thinking: "AI",
              selected: "AI",
              "network-wait": "NET",
              recovering: "SYNC",
              ended: "END",
              failed: "ERR",
            }[snapshot.phase] ?? "KEY");
    for (const [key, element] of keys) {
      element.classList.toggle(
        "is-selected",
        connected && snapshot.phase === "selected" && snapshot.button === key,
      );
      element.classList.toggle("is-recent", snapshot.lastButton === key);
    }
  }
  return {
    setConnection(value) {
      connected = Boolean(value);
      if (!connected) clearPress();
      show();
    },
    update(events, run, { liveEvent = null } = {}) {
      const selectedId = run?.id ?? null;
      const switched = selectedId !== runId;
      if (switched) {
        clearPress();
        runId = selectedId;
      }
      snapshot = controllerSnapshot(events, run ?? {});
      const previousSequence = highWater.get(runId) ?? -1;
      const newestSequence = events.reduce(
        (maximum, event) => Math.max(maximum, sequence(event) ?? -1),
        previousSequence,
      );
      if (runId !== null) {
        highWater.set(runId, newestSequence);
        while (highWater.size > 40)
          highWater.delete(highWater.keys().next().value);
      }
      const age = now() - Date.parse(liveEvent?.time);
      const freshExecuting =
        !switched &&
        connected &&
        liveEvent?.type === "executing" &&
        run?.status === "executing" &&
        snapshot.phase === "executing" &&
        snapshot.event?.id === liveEvent.id &&
        sequence(liveEvent) !== null &&
        sequence(liveEvent) > previousSequence &&
        Number.isFinite(age) &&
        age >= -1000 &&
        age <= 3000 &&
        eventButton(liveEvent);
      if (freshExecuting) {
        clearPress();
        pressedEvent = liveEvent.id;
        keys.get(eventButton(liveEvent)).classList.add("is-pressed");
        const token = generation;
        timer = setTimer(() => {
          if (token === generation) clearPress();
        }, 450);
      } else if (
        snapshot.phase !== "executing" ||
        snapshot.event?.id !== pressedEvent
      ) {
        clearPress();
      }
      show();
    },
  };
}
