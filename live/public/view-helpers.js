// Small shared view helpers. Text from recorded events is always assigned as text.
export const $ = (id) => document.getElementById(id);
export const statusNames = {
  running: "正在运行",
  started: "正在运行",
  active: "正在运行",
  completed: "已完成",
  success: "已完成",
  succeeded: "已完成",
  finished: "已结束",
  stopped: "已停止",
  stalled: "检测到重复循环 · 已存档暂停",
  failed: "运行失败",
  error: "运行失败",
  interrupted: "已中断",
  aborted: "已中断",
  max_steps: "达到步数上限",
  limit: "达到步数上限",
  blocked: "等待条件",
  waiting: "等待事件",
  starting: "正在启动",
  observing: "读取游戏状态",
  calling: "等待 Jev 响应",
  waiting_for_jev: "等待 JEV 服务恢复",
  recovering: "正在重读状态与调整建议",
  received: "已收到响应",
  request_error: "Jev 调用失败",
  decided: "已确认决策",
  executing: "执行按键中",
  action_error: "动作执行失败",
  budget_reached: "达到步数上限",
  blocked_missing_key: "缺少 Jev Key",
  blocked_connection: "游戏连接不可用",
};
export const eventNames = {
  started: "运行开始",
  observation: "读取游戏状态",
  jev_request: "调用 Jev",
  jev_response: "收到 Jev 响应",
  jev_error: "Jev 调用失败",
  decision: "确认决策",
  jev_wait: "等待 JEV 服务恢复",
  jev_resumed: "JEV 服务已恢复",
  recovery: "重新观察并调整建议",
  executing: "执行动作",
  result: "动作结果",
  finished: "运行结束",
  error: "运行异常",
  video_started: "游戏视频已连接",
  video_restarted: "游戏视频已重连",
  checkpoint: "已自动存档",
  objective: "主线目标更新",
  planner_configuration: "规划模式与模型",
  planner_request: "DeepSeek 规划请求",
  plan_outcome: "计划完成／失败／到期",
  planning_requested: "请求高层规划",
  plan: "接入模型规划",
  planning_skipped: "未配置规划模型",
  planning_error: "高层规划失败",
};
const buttonNames = {
  up: "↑ 上",
  down: "↓ 下",
  left: "← 左",
  right: "→ 右",
  a: "A",
  b: "B",
  start: "START",
  select: "SELECT",
  wait: "等待",
};
export const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
};
export const stringify = (value) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);
// Closed timeline rows do not allocate their often-large JSON text. Reopening
// one mounted detail reuses the exact same body; remembered open rows fill now.
export function createJsonDetails(
  label,
  value,
  { open = false, onToggle = () => {}, serialize = stringify } = {},
) {
  const details = node("details", "json-details");
  details.open = open;
  details.append(node("summary", "", label));
  let filled = false;
  function fillOnce() {
    if (!details.open || filled) return;
    details.append(node("pre", "", serialize(value) ?? "null"));
    filled = true;
  }
  fillOnce();
  details.addEventListener("toggle", () => {
    if (!details.isConnected) return;
    fillOnce();
    onToggle(details.open);
  });
  return details;
}
export const brief = (value, fallback = "未提供") =>
  value === undefined || value === null
    ? fallback
    : typeof value === "object"
      ? stringify(value)
      : String(value);
export const dateValue = (value) => {
  if (value === undefined || value === null) return null;
  const date = new Date(
    typeof value === "number" && value < 1e12 ? value * 1000 : value,
  );
  return Number.isFinite(date.getTime()) ? date : null;
};
export const dateFormat = (value, full = false) =>
  dateValue(value)?.toLocaleString(
    "zh-CN",
    full
      ? {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }
      : {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        },
  ) ?? "时间未知";
export const latest = (list, predicate) => [...list].reverse().find(predicate);
export const findAnswer = (event) =>
  event?.answer ?? event?.response?.answers?.button;
export const actionName = (action) => {
  const value =
    typeof action === "object" && action !== null
      ? (action.id ?? action.button ?? action.type)
      : action;
  return buttonNames[value] ?? brief(value, "未提供动作");
};
export const statusClass = (status) =>
  /^(running|started|active|starting|observing|calling|received|decided|executing)$/.test(
    status,
  )
    ? "active"
    : /^(completed|success|succeeded)$/.test(status)
      ? "success"
      : /^(error|failed|request_error|action_error)$/.test(status)
        ? "error"
        : /^(blocked.*|waiting_for_jev|recovering|stalled|interrupted|aborted)$/.test(
              status,
            )
          ? "warning"
          : "";
export const terminalStatus = (status) =>
  /^(completed|success|succeeded|finished|stopped|stalled|failed|error|interrupted|aborted|max_steps|limit|budget_reached|blocked.*)$/.test(
    status,
  );
export const recoveryExhausted = (run) => {
  const attempt = run.recovery?.attempt ?? run.recovery_attempts;
  const maximum = run.recovery?.max_attempts ?? run.max_recovery_attempts;
  return (
    run.status === "stalled" &&
    Number.isInteger(attempt) &&
    Number.isInteger(maximum) &&
    maximum > 0 &&
    attempt >= maximum
  );
};
export const runStatusLabel = (run) =>
  recoveryExhausted(run)
    ? "恢复尝试用尽 · 已存档暂停"
    : (statusNames[run.status] ?? brief(run.status, "状态未知"));

export function duration(value) {
  if (!Number.isFinite(value)) return "—";
  const seconds = Math.max(0, Math.floor(value / 1000));
  return [
    Math.floor(seconds / 3600),
    Math.floor(seconds / 60) % 60,
    seconds % 60,
  ]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}
export function latencyLabel(value) {
  return Number.isFinite(value)
    ? value >= 1000
      ? `${(value / 1000).toFixed(2)} 秒`
      : `${Math.round(value)} ms`
    : "—";
}
