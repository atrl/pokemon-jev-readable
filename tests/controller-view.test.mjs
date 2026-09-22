/** Controller fixtures only: no emulator, browser controls, or model calls. */
import test from "node:test";
import assert from "node:assert/strict";
import {
  controllerSnapshot,
  createControllerView,
} from "../live/public/controller-view.js";

const NOW = Date.parse("2026-09-22T12:00:00Z");
const run = (status = "running", id = "run") => ({ id, status });
const event = (number, type, button = "a", extra = {}) => ({
  id: `run:${number}`,
  type,
  button,
  step: number,
  time: new Date(NOW).toISOString(),
  ...extra,
});
function fixture() {
  const fields = new Map(),
    timers = new Map(),
    cancelled = [];
  let timerNumber = 0;
  const root = {
    dataset: {},
    querySelector(selector) {
      if (!fields.has(selector)) {
        const classes = new Set();
        fields.set(selector, {
          textContent: "",
          classList: {
            add(value) {
              classes.add(value);
            },
            remove(value) {
              classes.delete(value);
            },
            toggle(value, on) {
              if (on) classes.add(value);
              else classes.delete(value);
            },
            contains(value) {
              return classes.has(value);
            },
          },
        });
      }
      return fields.get(selector);
    },
  };
  const view = createControllerView(root, {
    now: () => NOW,
    setTimer(callback) {
      timers.set(++timerNumber, callback);
      return timerNumber;
    },
    clearTimer(id) {
      cancelled.push(id);
    },
  });
  view.setConnection(true);
  const key = (name) => root.querySelector(`[data-controller-key="${name}"]`);
  const field = (name) => root.querySelector(`[data-controller="${name}"]`);
  const pressed = () =>
    ["up", "down", "left", "right", "a", "b", "select", "start", "wait"].filter(
      (name) => key(name).classList.contains("is-pressed"),
    );
  return { root, view, key, field, pressed, timers, cancelled };
}

test("decision is selected only; only a fresh live executing event depresses the matching key", () => {
  const f = fixture();
  f.view.update([], run());
  const selected = event(1, "decision", "a");
  f.view.update([selected], run("decided"), { liveEvent: selected });
  assert.equal(f.key("a").classList.contains("is-selected"), true);
  assert.deepEqual(f.pressed(), []);
  assert.match(f.field("phase").textContent, /尚未执行/);
  assert.equal(f.field("last").textContent, "尚无执行记录");
  const executing = event(2, "executing", "a");
  f.view.update([selected, executing], run("executing"), {
    liveEvent: executing,
  });
  assert.deepEqual(f.pressed(), ["a"]);
  assert.match(f.field("phase").textContent, /执行 A/);
  assert.equal(f.timers.size, 1);
  const result = event(3, "result", "a", { success: true });
  f.view.update([selected, executing, result], run(), { liveEvent: result });
  assert.deepEqual(f.pressed(), []);
  assert.match(f.field("phase").textContent, /结果已返回/);
  assert.match(f.field("last").textContent, /A/);
  assert.equal(f.timers.size, 1); // Result is evidence, not another synthetic press.
});

test("history selection, reconnect replay, stale timestamps and nonexecuting run states never replay presses", () => {
  const f = fixture();
  const original = event(4, "executing", "up");
  f.view.update([original], run("executing"), { liveEvent: original });
  assert.deepEqual(f.pressed(), []); // First snapshot, even if a caller marks it live.
  f.view.setConnection(false);
  f.view.setConnection(true);
  f.view.update([original], run("executing"), { liveEvent: original });
  assert.deepEqual(f.pressed(), []);
  for (const candidate of [
    event(3, "executing"),
    event(5, "executing", "a", { time: new Date(NOW - 10000).toISOString() }),
    event(6, "executing", "a", { time: "invalid" }),
    event(7, "executing", "a", { time: new Date(NOW + 5000).toISOString() }),
  ]) {
    f.view.update([candidate], run("executing"), { liveEvent: candidate });
    assert.deepEqual(f.pressed(), []);
  }
  const late = event(8, "executing");
  f.view.update([late], run("calling"), { liveEvent: late });
  assert.deepEqual(f.pressed(), []);
  f.view.update([original], run("executing", "older-run"), {
    liveEvent: original,
  });
  assert.deepEqual(f.pressed(), []);
  assert.equal(f.timers.size, 0);
});

test("waiting, recovery, disconnect and termination release keys; cancelled timers cannot affect a later press", () => {
  const f = fixture();
  f.view.update([], run());
  const a = event(1, "executing", "a");
  f.view.update([a], run("executing"), { liveEvent: a });
  const oldTimer = f.timers.get(1);
  const b = event(2, "executing", "b");
  f.view.update([a, b], run("executing"), { liveEvent: b });
  assert.deepEqual(f.pressed(), ["b"]);
  assert.ok(f.cancelled.includes(1));
  oldTimer();
  assert.deepEqual(f.pressed(), ["b"]);
  f.view.setConnection(false);
  assert.deepEqual(f.pressed(), []);
  assert.match(f.field("phase").textContent, /重连中/);
  f.view.setConnection(true);
  let number = 3;
  for (const status of [
    "calling",
    "waiting_for_jev",
    "recovering",
    "stopped",
  ]) {
    const press = event(number++, "executing", "left");
    f.view.update([press], run("executing"), { liveEvent: press });
    assert.deepEqual(f.pressed(), ["left"]);
    f.view.update([press], run(status));
    assert.deepEqual(f.pressed(), []);
    assert.equal(f.key("left").classList.contains("is-selected"), false);
    assert.equal(
      f.key("wait").textContent,
      {
        calling: "AI",
        waiting_for_jev: "NET",
        recovering: "SYNC",
        stopped: "END",
      }[status],
    );
  }
  assert.equal(f.root.dataset.phase, "ended");
});

test("WAIT is a release/wait echo, malformed keys are inert, and successful result alone does not animate", () => {
  const f = fixture();
  f.view.update([], run());
  const selectedWait = event(0, "decision", "wait");
  f.view.update([selectedWait], run("decided"), { liveEvent: selectedWait });
  assert.equal(f.key("wait").textContent, "AI");
  const wait = event(1, "executing", "wait");
  f.view.update([wait], run("executing"), { liveEvent: wait });
  assert.deepEqual(f.pressed(), ["wait"]);
  assert.match(f.field("phase").textContent, /释放按键/);
  assert.equal(f.key("wait").textContent, "WAIT");
  const malformed = event(2, "executing", "<img src=x onerror=alert(1)>");
  f.view.update([malformed], run("executing"), { liveEvent: malformed });
  assert.deepEqual(f.pressed(), []);
  assert.match(f.field("phase").textContent, /未知/);
  const result = event(3, "result", "right", { success: true });
  f.view.update([result], run(), { liveEvent: result });
  assert.deepEqual(f.pressed(), []);
  assert.equal(f.timers.size, 1);
  assert.equal(
    controllerSnapshot(
      [event(4, "result", "b", { success: false })],
      run("action_error"),
    ).phase,
    "failed",
  );
});
