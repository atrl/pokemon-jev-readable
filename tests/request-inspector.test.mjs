/** Offline request fixtures; no game or model calls. */
import test from "node:test";
import assert from "node:assert/strict";
import {
  requestEvents,
  selectRequest,
  flattenState,
  stateValueType,
} from "../live/public/request-inspector.js";
import {
  latestCampaign,
  campaignFactLabel,
} from "../live/public/campaign-panel.js";
import {
  findAnswer,
  terminalStatus,
  runStatusLabel,
  createJsonDetails,
} from "../live/public/view-helpers.js";
import { createVideoPlayer } from "../live/public/video-player.js";

test("closed JSON details serialize only on first expansion and preserve remembered open state", (t) => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement(tag) {
      return {
        tag,
        textContent: "",
        children: [],
        listeners: {},
        isConnected: true,
        append(child) {
          this.children.push(child);
        },
        addEventListener(name, handler) {
          this.listeners[name] = handler;
        },
      };
    },
  };
  t.after(() => {
    globalThis.document = originalDocument;
  });
  let calls = 0;
  const transitions = [];
  const value = {
    zero: 0,
    text: "<script>literal JSON text</script>",
    list: [false, null],
  };
  const serialize = (input) => {
    calls++;
    return JSON.stringify(input, null, 2);
  };
  const detail = createJsonDetails("Actual JSON", value, {
    serialize,
    onToggle: (open) => transitions.push(open),
  });
  assert.equal(calls, 0);
  assert.equal(detail.children.length, 1);
  detail.listeners.toggle();
  assert.equal(calls, 0);
  detail.open = true;
  detail.listeners.toggle();
  assert.equal(calls, 1);
  assert.equal(detail.children[1].textContent, JSON.stringify(value, null, 2));
  detail.open = false;
  detail.listeners.toggle();
  detail.open = true;
  detail.listeners.toggle();
  assert.equal(calls, 1);
  assert.equal(detail.children.length, 2);
  assert.deepEqual(transitions, [false, true, false, true]);
  const remembered = createJsonDetails("Previously open", value, {
    open: true,
    serialize,
  });
  assert.equal(remembered.open, true);
  assert.equal(calls, 2);
  remembered.isConnected = false;
  remembered.listeners.toggle();
  assert.equal(calls, 2);
});

const event = (id, step, attempt = 1, request = {}) => ({
  id,
  type: "jev_request",
  step,
  attempt,
  request,
  time: "2026-09-22T01:00:00Z",
});

test("actual request selection keeps retries distinct and never substitutes observation/decision data", () => {
  const one = event("run:9", 8, 1, { state: { x: 3 } });
  const retry = event("run:12", 8, 2, { state: { x: 3 } });
  const observation = {
    id: "run:13",
    type: "observation",
    observation: { x: 99 },
  };
  const rows = [
    null,
    one,
    { type: "decision", selected: "a" },
    retry,
    observation,
  ];
  assert.deepEqual(requestEvents(rows), [one, retry]);
  assert.equal(selectRequest(rows), retry);
  assert.equal(
    selectRequest(rows, { followLatest: false, selectedId: one.id }),
    one,
  );
  assert.equal(selectRequest(rows).request.state.x, 3);
});

test("pinned historical request survives window eviction but does not replace another selected ID", () => {
  const pinned = event("old:1", 1),
    latest = event("new:2001", 300);
  assert.equal(
    selectRequest([latest], {
      followLatest: false,
      selectedId: pinned.id,
      pinnedEvent: pinned,
    }),
    pinned,
  );
  assert.equal(
    selectRequest([latest], {
      followLatest: false,
      selectedId: "different",
      pinnedEvent: pinned,
    }),
    latest,
  );
  assert.equal(
    selectRequest([latest], {
      followLatest: true,
      selectedId: pinned.id,
      pinnedEvent: pinned,
    }),
    latest,
  );
  assert.equal(selectRequest([]), null);
});

test("field inventory preserves null, false, zero, empty text and containers without inventing missing fields", () => {
  const input = {
    unknown: null,
    open: false,
    zero: 0,
    text: "",
    emptyArray: [],
    emptyObject: {},
    history: [{ frame: 0 }, false],
  };
  const rows = flattenState(input),
    byPath = new Map(rows.map((row) => [row.path, row]));
  for (const [key, type, value] of [
    ["unknown", "null", null],
    ["open", "boolean", false],
    ["zero", "number", 0],
    ["text", "string", ""],
  ]) {
    assert.equal(byPath.get(`$.state.${key}`).type, type);
    assert.equal(byPath.get(`$.state.${key}`).value, value);
  }
  assert.equal(byPath.get("$.state.emptyArray").type, "array");
  assert.equal(byPath.get("$.state.emptyObject").type, "object");
  assert.equal(byPath.get("$.state.history[0].frame").value, 0);
  assert.equal(byPath.get("$.state.history[1]").value, false);
  assert.equal(byPath.has("$.state.missing"), false);
  assert.equal(stateValueType(undefined), "undefined");
  assert.deepEqual(input.history, [{ frame: 0 }, false]);
});

test("field paths retain unusual keys and user strings exactly without treating them as markup", () => {
  const value = JSON.parse(
    '{"a.b":{"x[0]":"<img src=x onerror=alert(1)>"},"__proto__":{"testMarker":true}}',
  );
  const rows = flattenState(value);
  assert.ok(
    rows.some(
      (row) =>
        row.path === '$.state["a.b"]["x[0]"]' &&
        row.value === "<img src=x onerror=alert(1)>",
    ),
  );
  assert.ok(
    rows.some(
      (row) =>
        row.path === "$.state.__proto__.testMarker" && row.value === true,
    ),
  );
  assert.equal({}.testMarker, undefined);
});

test("scalar and array state schemas remain inspectable rather than being replaced by empty objects", () => {
  assert.deepEqual(flattenState("actual text"), [
    { path: "$.state", type: "string", value: "actual text", depth: 0 },
  ]);
  assert.deepEqual(flattenState(null), [
    { path: "$.state", type: "null", value: null, depth: 0 },
  ]);
  assert.equal(flattenState([false, 0, ""])[3].value, "");
});

test("shared Pokemon view vocabulary preserves waiting/recovery and uses the button answer", () => {
  const answer = { choice: "up" };
  assert.equal(
    findAnswer({ response: { answers: { button: answer } } }),
    answer,
  );
  assert.equal(
    findAnswer({ response: { answers: { unrelated_question: answer } } }),
    undefined,
  );
  assert.equal(terminalStatus("waiting_for_jev"), false);
  assert.equal(terminalStatus("recovering"), false);
  assert.equal(
    runStatusLabel({
      status: "stalled",
      recovery: { attempt: 3, max_attempts: 3 },
    }),
    "恢复尝试用尽 · 已存档暂停",
  );
  assert.equal(
    runStatusLabel({ status: "stalled" }),
    "检测到重复循环 · 已存档暂停",
  );
});

test("campaign module selects recorded observations without reconstructing them from requests", () => {
  const campaign = { overall_goal: "Actual recorded story goal" };
  assert.equal(
    latestCampaign([{ type: "jev_request", request: { state: { campaign } } }]),
    null,
  );
  const observation = {
    type: "observation",
    id: "run:1",
    observation: { campaign },
  };
  const after = {
    type: "result",
    id: "run:2",
    result: {
      after: {
        campaign: { ...campaign, active_objective: { id: "meet_oak" } },
      },
    },
  };
  assert.equal(
    latestCampaign([observation, after]).path,
    "result.after.campaign",
  );
  assert.equal(latestCampaign([observation, after]).event, after);
  assert.match(
    campaignFactLabel({ value: true, verified: true, quality: "source_prior" }),
    /^尚未验证/,
  );
  assert.match(
    campaignFactLabel({ value: false, verified: true, quality: "verified" }),
    /^已验证/,
  );
});

test("video controller keeps HLS mounted across Pokemon event/status updates and switches only its source", (t) => {
  const elements = new Map();
  const element = () => ({
    textContent: "",
    className: "",
    hidden: false,
    listeners: new Map(),
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    },
    pause() {},
    load() {},
    removeAttribute() {},
    canPlayType() {
      return "maybe";
    },
    play() {
      return Promise.resolve();
    },
  });
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  let mounts = 0,
    destroys = 0;
  class Hls {
    static Events = { MANIFEST_PARSED: "manifest", ERROR: "error" };
    static isSupported() {
      return true;
    }
    constructor() {
      mounts++;
    }
    on() {}
    loadSource() {}
    attachMedia() {}
    destroy() {
      destroys++;
    }
  }
  globalThis.document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
  };
  globalThis.window = { Hls };
  t.after(() => {
    globalThis.document = originalDocument;
    globalThis.window = originalWindow;
  });
  const player = createVideoPlayer();
  const run = {
    id: "run-a",
    status: "calling",
    video: {
      enabled: true,
      available: true,
      url: "/api/runs/a/video/index.m3u8",
    },
  };
  player.update(run);
  player.update({ ...run, status: "waiting_for_jev" });
  player.update({ ...run, status: "recovering" });
  player.update({ ...run, status: "calling" });
  assert.equal(mounts, 1);
  assert.equal(destroys, 0);
  assert.equal(elements.get("video-badge").textContent, "LIVE · 游戏画面");
  player.update({
    ...run,
    id: "run-b",
    video: { ...run.video, url: "/api/runs/b/video/index.m3u8" },
  });
  assert.equal(mounts, 2);
  assert.equal(destroys, 1);
  player.update(null);
  assert.equal(destroys, 2);
});
