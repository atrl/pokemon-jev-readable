/** Offline fixtures only: these records are not real Jev calls or gameplay evidence. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { EventStore, createLiveServer } from "../../live/server.mjs";
import { redact } from "../../live/redact.mjs";

const SOURCE = "offline-test-fixture-not-real-jev";
const line = (type, values = {}) =>
  JSON.stringify({ type, source: SOURCE, ...values }) + "\n";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "jev-live-test-only-"));
  fs.mkdirSync(path.join(root, "pokemon/runs/test-only"), { recursive: true });
  fs.mkdirSync(path.join(root, "pokemon/runs/second-test-only"), {
    recursive: true,
  });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    root,
    file: path.join(root, "pokemon/runs/test-only/events.jsonl"),
    secondFile: path.join(root, "pokemon/runs/second-test-only/events.jsonl"),
  };
}

function request(port, pathname, method = "GET", headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: "127.0.0.1",
        port,
        path: pathname,
        method,
        headers,
        agent: false,
      },
      (response) => {
        response.setEncoding("utf8");
        let body = "";
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            headers: response.headers,
            body,
          }),
        );
        response.on("error", reject);
      },
    );
    req.on("error", reject);
    req.end();
  });
}

async function start(t, root, options = {}) {
  const live = createLiveServer({
    root,
    pollMs: 60_000,
    heartbeatMs: 60_000,
    secrets: [],
    ...options,
  });
  live.server.listen(0, "127.0.0.1");
  await once(live.server, "listening");
  t.after(() => live.close());
  return { ...live, port: live.server.address().port };
}

async function stream(t, port, lastId) {
  const frames = [];
  const waiting = new Set();
  let pending = "";
  const req = http.get({
    host: "127.0.0.1",
    port,
    path: "/api/events",
    agent: false,
    headers: lastId ? { "Last-Event-ID": lastId } : {},
  });
  const [response] = await once(req, "response");
  assert.equal(response.statusCode, 200);
  assert.match(response.headers["content-type"], /text\/event-stream/);
  response.setEncoding("utf8");
  response.on("data", (chunk) => {
    pending += chunk;
    let boundary;
    while ((boundary = pending.indexOf("\n\n")) !== -1) {
      const block = pending.slice(0, boundary);
      pending = pending.slice(boundary + 2);
      const frame = {};
      for (const row of block.split("\n")) {
        const colon = row.indexOf(":");
        if (colon !== -1)
          frame[row.slice(0, colon)] = row.slice(colon + 1).trimStart();
      }
      if (frame.data) frame.data = JSON.parse(frame.data);
      frames.push(frame);
      for (const wake of waiting) wake();
    }
  });
  const close = () => {
    response.destroy();
    req.destroy();
  };
  t.after(close);
  return {
    frames,
    close,
    waitFor(predicate) {
      const existing = frames.find(predicate);
      if (existing) return Promise.resolve(existing);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          waiting.delete(check);
          reject(new Error("Timed out waiting for test SSE frame"));
        }, 2000);
        const check = () => {
          const found = frames.find(predicate);
          if (found) {
            clearTimeout(timer);
            waiting.delete(check);
            resolve(found);
          }
        };
        waiting.add(check);
        check();
      });
    },
  };
}

test("JSONL tail waits for newline, replays history once, and does not duplicate on repeated polls", (t) => {
  // Replaying identical events also produces identical timing at one sampled instant.
  t.mock.timers.enable({
    apis: ["Date"],
    now: Date.parse("2026-09-22T03:00:00.000Z"),
  });
  const { root, file } = fixture(t);
  const seen = [];
  const store = new EventStore(root, { onEvent: (value) => seen.push(value) });
  const first = line("started", {
    time: "2026-09-22T01:00:00.000Z",
    goal: SOURCE,
  });
  fs.writeFileSync(file, first.slice(0, -1));
  store.poll();
  assert.equal(seen.length, 0);
  fs.appendFileSync(file, "\n" + line("jev_request", { step: 1 }));
  store.poll();
  store.poll();
  assert.deepEqual(
    seen.map((row) => row.event.type),
    ["started", "jev_request"],
  );
  const [run] = store.list();
  assert.equal(run.calls, 1);
  assert.equal(run.status, "calling");
  assert.deepEqual(
    store.detail(run.id).events.map((event) => event.id),
    [`${run.id}:1`, `${run.id}:2`],
  );
  const rebuilt = new EventStore(root);
  rebuilt.poll();
  assert.deepEqual(rebuilt.detail(run.id), store.detail(run.id));
});

test("source discovery is limited to real Pokémon run directories and ignores legacy logs", (t) => {
  const { root, file } = fixture(t);
  fs.writeFileSync(
    file,
    line("started", { goal: "Pokemon history remains readable" }),
  );
  fs.mkdirSync(path.join(root, "logs"));
  fs.writeFileSync(
    path.join(root, "logs/legacy.jsonl"),
    line("started", { goal: "out-of-scope log must not be exposed" }),
  );
  const outside = path.join(root, "outside-run");
  fs.mkdirSync(outside);
  fs.writeFileSync(
    path.join(outside, "events.jsonl"),
    line("started", { goal: "symlink must not be followed" }),
  );
  fs.symlinkSync(outside, path.join(root, "pokemon/runs/symlinked-run"));
  const store = new EventStore(root);
  store.poll();
  assert.equal(store.list().length, 1);
  const [run] = store.list();
  assert.equal(run.game, "pokemon");
  assert.equal(run.goal, "Pokemon history remains readable");
  assert.equal(store.detail(run.id).events[0].type, "started");
});

test("timing totals survive event retention and count each HTTP attempt once", (t) => {
  t.mock.timers.enable({
    apis: ["Date"],
    now: Date.parse("2026-09-22T01:01:00.000Z"),
  });
  const { root, secondFile } = fixture(t);
  const rows = [
    line("started", {
      time: "2026-09-22T01:00:00.000Z",
      elapsed_ms: 0,
      maxSteps: 5000,
    }),
  ];
  for (let step = 1; step <= 5; step++) {
    const time = `2026-09-22T01:00:0${step}.000Z`;
    const common = { step, time, elapsed_ms: step * 1000 };
    rows.push(line("jev_request", { ...common, attempt: 1 }));
    if (step === 1) {
      rows.push(
        line("jev_response", {
          ...common,
          attempt: 1,
          httpStatus: 503,
          latency_ms: 100,
        }),
      );
      rows.push(
        line("jev_error", {
          ...common,
          attempt: 1,
          httpStatus: 503,
          latency_ms: 100,
        }),
      );
      rows.push(line("jev_request", { ...common, attempt: 2 }));
      rows.push(
        line("jev_response", {
          ...common,
          attempt: 2,
          httpStatus: 200,
          latency_ms: 200,
        }),
      );
    } else
      rows.push(
        line("jev_response", {
          ...common,
          attempt: 1,
          httpStatus: 200,
          latency_ms: 300,
        }),
      );
    rows.push(
      line("result", { ...common, success: true, game_frames: step * 60 }),
    );
  }
  rows.push(
    line("finished", {
      time: "2026-09-22T01:00:10.000Z",
      elapsed_ms: 10000,
      status: "budget_reached",
    }),
  );
  fs.writeFileSync(secondFile, rows.join(""));
  const store = new EventStore(root, { maxEvents: 3 });
  store.poll();
  const first = store.list()[0];
  assert.equal(first.retainedEvents, 3);
  assert.equal(first.truncated, true);
  assert.equal(first.results, 5);
  assert.equal(first.timing.http_attempts, 6);
  assert.equal(first.timing.model_ms, 1500);
  assert.equal(first.timing.avg_latency_ms, 250);
  assert.equal(first.timing.elapsed_ms, 10000);
  assert.equal(first.timing.steps_per_minute, 30);
  assert.equal(first.timing.game_seconds, 5);
  assert.equal(first.timing.estimated_remaining_ms, null);
  t.mock.timers.tick(60000);
  const later = store.detail(first.id).run;
  assert.notEqual(later.timing.sampled_at, first.timing.sampled_at);
  assert.deepEqual(
    { ...later.timing, sampled_at: first.timing.sampled_at },
    first.timing,
  );
});

test("active elapsed time advances from the last monotonic event using its wall time reference", (t) => {
  t.mock.timers.enable({
    apis: ["Date"],
    now: Date.parse("2026-09-22T01:00:10.000Z"),
  });
  const { root, secondFile } = fixture(t);
  fs.writeFileSync(
    secondFile,
    line("started", {
      time: "2026-09-22T01:00:00.000Z",
      elapsed_ms: 0,
      maxSteps: 10,
      pid: process.pid,
    }) +
      Array.from({ length: 5 }, (_, index) =>
        line("result", {
          step: index + 1,
          time: "2026-09-22T01:00:06.000Z",
          elapsed_ms: 6000,
          success: true,
        }),
      ).join(""),
  );
  const store = new EventStore(root);
  store.poll();
  const first = store.list()[0];
  assert.equal(first.timing.sampled_at, "2026-09-22T01:00:10.000Z");
  assert.equal(first.timing.elapsed_ms, 10000);
  assert.equal(first.timing.remaining_steps, 5);
  assert.equal(first.timing.steps_per_minute, 30);
  assert.equal(first.timing.estimated_remaining_ms, 10000);
  t.mock.timers.tick(2000);
  const later = store.detail(first.id).run;
  assert.equal(later.timing.elapsed_ms, 12000);
  assert.equal(later.timing.steps_per_minute, 25);
  assert.equal(later.timing.estimated_remaining_ms, 12000);
});

test("JEV backoff remains active with a ticking retry delay and resumes calling without executing an action", (t) => {
  t.mock.timers.enable({
    apis: ["Date"],
    now: Date.parse("2026-09-22T01:00:10.000Z"),
  });
  const { root, secondFile } = fixture(t);
  fs.writeFileSync(
    secondFile,
    line("started", {
      time: "2026-09-22T01:00:00.000Z",
      elapsed_ms: 0,
      maxSteps: 5000,
      pid: process.pid,
      video_enabled: true,
    }) +
      line("jev_request", {
        time: "2026-09-22T01:00:01.000Z",
        elapsed_ms: 1000,
        step: 191,
        attempt: 3,
      }) +
      line("jev_error", {
        time: "2026-09-22T01:00:02.000Z",
        elapsed_ms: 2000,
        step: 191,
        attempt: 3,
        latency_ms: 1000,
      }) +
      line("jev_wait", {
        time: "2026-09-22T01:00:02.000Z",
        elapsed_ms: 2000,
        step: 191,
        retry_after_seconds: 30,
        reason: "temporarily_unavailable",
        consecutive_windows: 1,
      }),
  );
  const store = new EventStore(root, { maxEvents: 2 });
  store.poll();
  const first = store.list()[0];
  assert.equal(first.status, "waiting_for_jev");
  assert.equal(first.calls, 1);
  assert.equal(first.results, 0);
  assert.equal(first.video.enabled, true);
  assert.equal(first.jev_wait.retry_at, "2026-09-22T01:00:32.000Z");
  assert.equal(first.jev_wait.retry_remaining_seconds, 22);
  assert.equal(first.jev_wait.consecutive_windows, 1);
  assert.equal(first.timing.elapsed_ms, 10000);
  assert.equal(first.timing.model_ms, 1000);
  assert.equal(
    store.detail(first.id).events.some((event) => event.type === "finished"),
    false,
  );
  // Checkpoints and video heartbeats do not finish, resume, or discard backoff.
  fs.appendFileSync(
    secondFile,
    line("checkpoint", {
      time: "2026-09-22T01:00:10.000Z",
      elapsed_ms: 10000,
    }) +
      line("video_started", {
        time: "2026-09-22T01:00:10.000Z",
        elapsed_ms: 10000,
      }),
  );
  store.poll();
  t.mock.timers.tick(25000);
  const waited = store.detail(first.id).run;
  assert.equal(waited.status, "waiting_for_jev");
  assert.equal(waited.jev_wait.retry_remaining_seconds, 0);
  assert.equal(waited.timing.elapsed_ms, 35000);
  assert.equal(waited.results, 0);
  assert.equal(waited.timing.model_ms, 1000);
  // Retry is another HTTP attempt at the same gameplay step, not a result.
  fs.appendFileSync(
    secondFile,
    line("jev_request", {
      time: "2026-09-22T01:00:35.000Z",
      elapsed_ms: 35000,
      step: 191,
      attempt: 4,
    }),
  );
  store.poll();
  const resumed = store.detail(first.id).run;
  assert.equal(resumed.status, "calling");
  assert.equal(resumed.calls, 2);
  assert.equal(resumed.results, 0);
  assert.equal(resumed.steps, 191);
  assert.equal(resumed.jev_wait, undefined);
  fs.appendFileSync(
    secondFile,
    line("jev_response", {
      time: "2026-09-22T01:00:36.000Z",
      elapsed_ms: 36000,
      step: 191,
      attempt: 4,
      httpStatus: 200,
      latency_ms: 1000,
    }),
  );
  store.poll();
  assert.equal(store.detail(first.id).run.status, "received");
  assert.equal(store.detail(first.id).run.timing.model_ms, 2000);
});

test("invalid retry values cannot produce an invalid wait deadline or a terminal status", (t) => {
  const { root, secondFile } = fixture(t);
  fs.writeFileSync(
    secondFile,
    line("started", { pid: process.pid }) +
      line("jev_wait", {
        step: 1,
        retry_after_seconds: "30",
        consecutive_windows: -1,
        reason: "temporarily_unavailable",
      }),
  );
  const store = new EventStore(root);
  store.poll();
  const [run] = store.list();
  assert.equal(run.status, "waiting_for_jev");
  assert.equal(run.jev_wait.retry_at, null);
  assert.equal(run.jev_wait.retry_remaining_seconds, null);
  assert.equal(run.jev_wait.consecutive_windows, null);
  assert.equal(run.calls, 0);
  assert.equal(run.results, 0);
});

test("recovery stays nonterminal, records bounded attempts, and resumes normal observation and request states", (t) => {
  t.mock.timers.enable({
    apis: ["Date"],
    now: Date.parse("2026-09-22T01:00:10.000Z"),
  });
  const { root, secondFile } = fixture(t);
  fs.writeFileSync(
    secondFile,
    line("started", {
      time: "2026-09-22T01:00:00.000Z",
      elapsed_ms: 0,
      pid: process.pid,
      video_enabled: true,
      maxSteps: 5000,
    }) +
      line("recovery", {
        time: "2026-09-22T01:00:05.000Z",
        elapsed_ms: 5000,
        step: 80,
        attempt: 1,
        max_attempts: 3,
        reason: "no_observable_change",
        no_effect_steps: 80,
        loop_kind: "stationary",
        failed_button: "a",
      }),
  );
  const store = new EventStore(root, { maxEvents: 1 });
  store.poll();
  const initial = store.list()[0];
  assert.equal(initial.status, "recovering");
  assert.equal(initial.recovery_count, 1);
  assert.equal(initial.recovery.attempt, 1);
  assert.equal(initial.recovery.max_attempts, 3);
  assert.equal(initial.recovery.loop_kind, "stationary");
  assert.equal(initial.calls, 0);
  assert.equal(initial.results, 0);
  assert.equal(initial.video.enabled, true);
  assert.equal(initial.timing.elapsed_ms, 10000);
  t.mock.timers.tick(1000);
  assert.equal(store.detail(initial.id).run.timing.elapsed_ms, 11000);
  fs.appendFileSync(
    secondFile,
    line("checkpoint", { time: "2026-09-22T01:00:11.000Z", elapsed_ms: 11000 }),
  );
  store.poll();
  assert.equal(store.detail(initial.id).run.status, "recovering");
  assert.equal(store.detail(initial.id).run.recovery.attempt, 1);
  fs.appendFileSync(secondFile, line("observation", { step: 81 }));
  store.poll();
  assert.equal(store.detail(initial.id).run.status, "observing");
  fs.appendFileSync(secondFile, line("jev_request", { step: 81, attempt: 1 }));
  store.poll();
  assert.equal(store.detail(initial.id).run.status, "calling");
  assert.equal(store.detail(initial.id).run.calls, 1);
  assert.equal(store.detail(initial.id).run.results, 0);
  for (let attempt = 2; attempt <= 3; attempt++) {
    fs.appendFileSync(
      secondFile,
      line("recovery", {
        attempt,
        max_attempts: 3,
        no_effect_steps: 80,
        failed_button: "a",
      }),
    );
    store.poll();
    assert.equal(store.detail(initial.id).run.status, "recovering");
  }
  const lastAttempt = store.detail(initial.id).run;
  assert.equal(lastAttempt.recovery_count, 3);
  assert.equal(lastAttempt.recovery.attempt, 3);
  assert.equal(lastAttempt.status, "recovering"); // Reaching the attempt count is not an invented finish.
  fs.appendFileSync(
    secondFile,
    line("finished", {
      status: "stalled",
      reason: "recovery_attempts_exhausted",
    }),
  );
  store.poll();
  const paused = store.detail(initial.id).run;
  assert.equal(paused.status, "stalled");
  assert.equal(paused.recovery_count, 3);
  assert.equal(paused.recovery.max_attempts, 3);
});

test("JSONL decoder preserves a UTF-8 character split between polls and skips corrupt/invalid rows", (t) => {
  const { root, file } = fixture(t);
  const text = "调用中：正在读取真实游戏状态";
  const bytes = Buffer.from(line("observation", { text }));
  const cut = bytes.indexOf(Buffer.from("调用")) + 1;
  fs.writeFileSync(file, bytes.subarray(0, cut));
  const store = new EventStore(root);
  store.poll();
  assert.equal(store.list()[0].retainedEvents, 0);
  fs.appendFileSync(
    file,
    Buffer.concat([
      bytes.subarray(cut),
      Buffer.from(
        '{broken JSON}\nnull\n[]\n{"type":42}\n' +
          line("result", { success: true }),
      ),
    ]),
  );
  store.poll();
  const detail = store.detail(store.list()[0].id);
  assert.deepEqual(
    detail.events.map((event) => event.type),
    ["observation", "result"],
  );
  assert.equal(detail.events[0].text, text);
  assert.ok(!JSON.stringify(detail).includes("\ufffd"));
});

test("oversized JSONL records are discarded and the following valid event survives", (t) => {
  const { root, file } = fixture(t);
  fs.writeFileSync(
    file,
    line("observation", { text: "x".repeat(2 * 1024 * 1024) }) +
      line("result", { success: true }),
  );
  const store = new EventStore(root);
  for (let i = 0; i < 4; i++) store.poll();
  assert.deepEqual(
    store.detail(store.list()[0].id).events.map((event) => event.type),
    ["result"],
  );
});

test("run lifecycle and counters remain accurate after bounded event retention", (t) => {
  const { root, file, secondFile } = fixture(t);
  const store = new EventStore(root, { maxEvents: 3 });
  fs.writeFileSync(
    file,
    line("started", {
      time: "2026-09-22T01:00:00.000Z",
      goal: SOURCE,
      maxSteps: 3,
      pid: process.pid,
    }),
  );
  fs.writeFileSync(
    secondFile,
    line("started", { time: "2026-09-22T02:00:00.000Z", goal: SOURCE }),
  );
  store.poll();
  assert.deepEqual(
    store.list().map((run) => run.game),
    ["pokemon", "pokemon"],
  );
  const id = store
    .list()
    .find((run) => run.startedAt === "2026-09-22T01:00:00.000Z").id;
  const lifecycle = [
    ["observation", "observing"],
    ["jev_request", "calling"],
    ["jev_response", "received"],
    ["decision", "decided"],
    ["executing", "executing"],
    ["result", "running"],
    ["jev_request", "calling"],
    ["jev_error", "request_error"],
  ];
  for (const [type, status] of lifecycle) {
    fs.appendFileSync(file, line(type, { step: 2 }));
    store.poll();
    assert.equal(store.detail(id).run.status, status);
  }
  fs.appendFileSync(
    file,
    line("result", { step: 3, success: false }) +
      line("finished", {
        status: "budget_reached",
        reason: "test-only budget",
      }),
  );
  store.poll();
  const { run, events } = store.detail(id);
  assert.equal(run.status, "budget_reached");
  assert.equal(run.reason, "test-only budget");
  assert.equal(run.steps, 3);
  assert.equal(run.calls, 2);
  assert.equal(run.results, 2);
  assert.equal(run.retainedEvents, 3);
  assert.equal(run.truncated, true);
  assert.equal(events.length, 3);
  assert.equal("pid" in run, false);
});

test("an exited process is marked interrupted unless an explicit terminal event exists", async (t) => {
  const { root, file } = fixture(t);
  const child = spawn(process.execPath, ["-e", "process.exit(0)"], {
    stdio: "ignore",
  });
  await once(child, "exit");
  fs.writeFileSync(
    file,
    line("started", { pid: child.pid, goal: SOURCE }) + line("jev_request"),
  );
  const store = new EventStore(root);
  store.poll();
  assert.equal(store.list()[0].status, "interrupted");
  assert.match(store.list()[0].reason, /退出/);
  fs.appendFileSync(
    file,
    line("jev_wait", {
      retry_after_seconds: 300,
      reason: "temporarily_unavailable",
      consecutive_windows: 2,
    }),
  );
  store.poll();
  assert.equal(store.list()[0].status, "interrupted");
  fs.appendFileSync(
    file,
    line("recovery", {
      attempt: 1,
      max_attempts: 3,
      reason: "no_observable_change",
    }),
  );
  store.poll();
  assert.equal(store.list()[0].status, "interrupted");
  fs.appendFileSync(file, line("finished", { status: "completed" }));
  store.poll();
  assert.equal(store.list()[0].status, "completed");
});

test("truncated/rotated/deleted sources refresh history, and symlink files are not exposed", (t) => {
  const { root, file } = fixture(t);
  fs.writeFileSync(
    file,
    line("started", { goal: SOURCE.repeat(10) }) + line("jev_request"),
  );
  const store = new EventStore(root);
  store.poll();
  fs.writeFileSync(file, line("result"));
  store.poll();
  assert.deepEqual(
    store.detail(store.list()[0].id).events.map((event) => event.type),
    ["result"],
  );
  const old = `${file}.old`;
  fs.renameSync(file, old);
  fs.writeFileSync(file, line("started", { goal: "new test-only run" }));
  store.poll();
  assert.equal(store.list().length, 1);
  assert.equal(store.list()[0].goal, "new test-only run");
  fs.rmSync(file);
  fs.symlinkSync(old, file);
  store.poll();
  assert.deepEqual(store.list(), []);
});

test("malformed timestamp fields cannot poison run summaries or listing", (t) => {
  const { root, file, secondFile } = fixture(t);
  fs.writeFileSync(
    file,
    line("started", { time: { bad: true }, goal: SOURCE }),
  );
  fs.writeFileSync(
    secondFile,
    line("started", { time: "2026-09-22T02:00:00.000Z", goal: SOURCE }),
  );
  const store = new EventStore(root);
  store.poll();
  assert.doesNotThrow(() => store.list());
  for (const run of store.list()) {
    assert.equal(typeof run.startedAt, "string");
    assert.equal(typeof run.updatedAt, "string");
  }
});

test("redaction removes configured secrets, bearer credentials, and nested credential keys", () => {
  const secret = "test-only-secret-value";
  const input = {
    message: `prefix ${secret} suffix`,
    authorization: "anything",
    nested: [
      {
        api_key: "key",
        accessToken: "token",
        password: "password",
        secret: "secret",
        Cookie: "cookie",
        "set-cookie": "cookie",
        text: "Authorization: Bearer test-only-token",
      },
    ],
    confidence: 0.7,
    safe: "real observation",
    nil: null,
  };
  const result = redact(input, [undefined, "", secret]);
  assert.equal(result.message, "prefix [REDACTED] suffix");
  assert.equal(result.authorization, "[REDACTED]");
  for (const key of [
    "api_key",
    "accessToken",
    "password",
    "secret",
    "Cookie",
    "set-cookie",
  ])
    assert.equal(result.nested[0][key], "[REDACTED]");
  assert.equal(result.nested[0].text, "Authorization: Bearer [REDACTED]");
  assert.equal(result.confidence, 0.7);
  assert.equal(result.safe, "real observation");
  assert.equal(result.nil, null);
  assert.equal(input.message, `prefix ${secret} suffix`);
});

test("HTTP serves only explicit assets and redacted run APIs, and rejects writes and file traversal", async (t) => {
  const { root, file } = fixture(t);
  const secret = "test-only-never-public-secret";
  fs.writeFileSync(path.join(root, ".env"), `TYPESAFE_API_KEY=${secret}`);
  fs.writeFileSync(
    file,
    line("started", { goal: SOURCE }) +
      line("jev_request", { request: { apiKey: secret }, note: secret }),
  );
  const live = await start(t, root, {
    secrets: [secret],
    config: { pokemon: { keyConfigured: true } },
  });
  for (const asset of [
    "/",
    "/app.js",
    "/view-helpers.js",
    "/video-player.js",
    "/campaign-panel.js",
    "/controller-view.js",
    "/request-inspector.js",
    "/style.css",
    "/health",
  ]) {
    const result = await request(live.port, asset);
    assert.equal(result.status, 200, asset);
    assert.equal(result.headers["cache-control"], "no-store");
    assert.equal(result.headers["x-content-type-options"], "nosniff");
    assert.ok(!result.body.includes(secret));
  }
  const index = await request(live.port, "/api/runs");
  const parsed = JSON.parse(index.body);
  assert.equal(parsed.runs.length, 1);
  assert.equal(parsed.config.pokemon.keyConfigured, true);
  const detail = await request(live.port, `/api/runs/${parsed.runs[0].id}`);
  assert.equal(detail.status, 200);
  assert.ok(!detail.body.includes(secret));
  assert.match(detail.body, /REDACTED/);
  for (const denied of [
    "/.env",
    "/pokemon/runs/test-only/events.jsonl",
    "/view-helpers.js.map",
    "/video-player.js.map",
    "/campaign-panel.js.map",
    "/../.env",
    "/%2e%2e/.env",
    "/%2f..%2f.env",
    "/api/runs/../../.env",
    "/api/runs/" + "0".repeat(20),
  ]) {
    const result = await request(live.port, denied);
    assert.equal(result.status, 404, denied);
    assert.ok(!result.body.includes(secret));
  }
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    const result = await request(live.port, "/api/runs", method);
    assert.equal(result.status, 405);
    assert.match(result.body, /Read-only/);
  }
  assert.equal((await request(live.port, "http://[invalid-host")).status, 400);
  assert.equal((await request(live.port, "/health")).status, 200);
});

test("video HTTP serves whitelisted HLS files and honors valid byte ranges", async (t) => {
  const { root, secondFile } = fixture(t);
  fs.writeFileSync(secondFile, line("started", { video_enabled: true }));
  const directory = path.join(path.dirname(secondFile), "video");
  fs.mkdirSync(directory);
  fs.writeFileSync(
    path.join(directory, "index.m3u8"),
    "#EXTM3U\nsegment-000001.m4s\n",
  );
  fs.writeFileSync(path.join(directory, "init.mp4"), "fixture-init");
  fs.writeFileSync(path.join(directory, "segment-000001.m4s"), "0123456789");
  fs.writeFileSync(path.join(directory, "private.txt"), "must-not-be-served");
  const live = await start(t, root);
  const run = live.store.list()[0];
  assert.equal(run.video.enabled, true);
  assert.equal(run.video.available, true);
  const prefix = `/api/runs/${run.id}/video/`;
  const playlist = await request(live.port, run.video.url);
  assert.equal(playlist.status, 200);
  assert.equal(
    playlist.headers["content-type"],
    "application/vnd.apple.mpegurl",
  );
  assert.equal(playlist.body, "#EXTM3U\nsegment-000001.m4s\n");
  assert.equal(
    (await request(live.port, prefix + "init.mp4")).headers["content-type"],
    "video/mp4",
  );
  const complete = await request(live.port, prefix + "segment-000001.m4s");
  assert.equal(complete.status, 200);
  assert.equal(complete.headers["content-type"], "video/iso.segment");
  assert.equal(complete.headers["accept-ranges"], "bytes");
  assert.equal(complete.body, "0123456789");
  for (const [range, expected, contentRange] of [
    ["bytes=2-5", "2345", "bytes 2-5/10"],
    ["bytes=7-", "789", "bytes 7-9/10"],
    ["bytes=-3", "789", "bytes 7-9/10"],
    ["bytes=7-99", "789", "bytes 7-9/10"],
  ]) {
    const partial = await request(
      live.port,
      prefix + "segment-000001.m4s",
      "GET",
      { Range: range },
    );
    assert.equal(partial.status, 206, range);
    assert.equal(partial.headers["content-range"], contentRange);
    assert.equal(Number(partial.headers["content-length"]), expected.length);
    assert.equal(partial.body, expected);
  }
  for (const range of [
    "bytes=",
    "bytes=0-1,4-5",
    "bytes=10-",
    "bytes=8-2",
    "bytes=-0",
  ]) {
    assert.equal(
      (
        await request(live.port, prefix + "segment-000001.m4s", "GET", {
          Range: range,
        })
      ).status,
      416,
      range,
    );
  }
  for (const name of [
    "private.txt",
    "segment-1.m4s",
    "segment-000002.m4s",
    "../events.jsonl",
    "%2e%2e%2fevents.jsonl",
  ]) {
    const denied = await request(live.port, prefix + name);
    assert.equal(denied.status, 404, name);
    assert.ok(!denied.body.includes("must-not-be-served"));
  }
});

test("video HTTP refuses symlink files, symlink directories, and untracked run IDs", async (t) => {
  const { root, file, secondFile } = fixture(t);
  fs.writeFileSync(secondFile, line("started", { video_enabled: true }));
  const privateFile = path.join(root, "private-fixture");
  fs.writeFileSync(privateFile, "private-fixture-must-not-leak");
  const directory = path.join(path.dirname(secondFile), "video");
  fs.mkdirSync(directory);
  fs.symlinkSync(privateFile, path.join(directory, "index.m3u8"));
  fs.symlinkSync(privateFile, path.join(directory, "segment-000001.m4s"));
  const live = await start(t, root);
  const pokemonRun = live.store.list().find((run) => run.game === "pokemon");
  const prefix = `/api/runs/${pokemonRun.id}/video/`;
  assert.equal(pokemonRun.video.available, false);
  for (const name of ["index.m3u8", "segment-000001.m4s"]) {
    const denied = await request(live.port, prefix + name);
    assert.equal(denied.status, 404);
    assert.ok(!denied.body.includes("private-fixture-must-not-leak"));
  }
  fs.rmSync(directory, { recursive: true });
  const outside = path.join(root, "private-video");
  fs.mkdirSync(outside);
  fs.writeFileSync(
    path.join(outside, "index.m3u8"),
    "private-fixture-must-not-leak",
  );
  fs.symlinkSync(outside, directory);
  assert.equal((await request(live.port, prefix + "index.m3u8")).status, 404);
  assert.equal(live.store.detail(pokemonRun.id).run.video.available, false);
  assert.equal(
    (await request(live.port, `/api/runs/${"0".repeat(20)}/video/index.m3u8`))
      .status,
    404,
  );
});

test("SSE delivers a Jev request immediately, before response/result records exist", async (t) => {
  const { root, file } = fixture(t);
  fs.writeFileSync(file, line("started", { goal: SOURCE }));
  const secret = "test-only-sse-secret";
  const live = await start(t, root, { secrets: [secret] });
  const client = await stream(t, live.port);
  await client.waitFor((frame) => frame.event === "ready");
  fs.appendFileSync(
    file,
    line("jev_request", {
      step: 1,
      request: { state: { text: SOURCE }, api_key: secret },
    }),
  );
  live.store.poll();
  const pending = await client.waitFor(
    (frame) => frame.data?.event?.type === "jev_request",
  );
  assert.equal(pending.event, "update");
  assert.equal(pending.data.run.status, "calling");
  assert.equal(pending.data.run.calls, 1);
  assert.equal(pending.data.run.results, 0);
  assert.ok(!JSON.stringify(pending).includes(secret));
  assert.equal(
    client.frames.some((frame) => frame.data?.event?.type === "result"),
    false,
  );
  fs.appendFileSync(
    file,
    line("jev_response", { step: 1 }) +
      line("result", { step: 1, success: true }),
  );
  live.store.poll();
  const result = await client.waitFor(
    (frame) => frame.data?.event?.type === "result",
  );
  assert.equal(result.data.run.results, 1);
  assert.deepEqual(
    client.frames
      .filter((frame) => frame.event === "update")
      .map((frame) => frame.data.event.type),
    ["jev_request", "jev_response", "result"],
  );
  client.close();
});

test("SSE reconnect replays only missed events and resets an unknown or expired cursor", async (t) => {
  const { root, file } = fixture(t);
  fs.writeFileSync(file, line("started", { goal: SOURCE }));
  const live = await start(t, root);
  const first = await stream(t, live.port);
  await first.waitFor((frame) => frame.event === "ready");
  fs.appendFileSync(file, line("jev_request"));
  live.store.poll();
  const last = await first.waitFor((frame) => frame.event === "update");
  first.close();
  fs.appendFileSync(
    file,
    line("jev_response") + line("result", { success: true }),
  );
  live.store.poll();
  const resumed = await stream(t, live.port, last.id);
  await resumed.waitFor((frame) => frame.event === "ready");
  assert.deepEqual(
    resumed.frames
      .filter((frame) => frame.event === "update")
      .map((frame) => frame.data.event.type),
    ["jev_response", "result"],
  );
  assert.equal(
    resumed.frames.some((frame) => frame.id === last.id),
    false,
  );
  resumed.close();
  const unknown = await stream(t, live.port, "different-server-epoch:1");
  await unknown.waitFor((frame) => frame.event === "ready");
  assert.deepEqual(
    unknown.frames.map((frame) => frame.event),
    ["reset", "ready"],
  );
  unknown.close();
  fs.appendFileSync(
    file,
    Array.from({ length: 1001 }, () => line("observation")).join(""),
  );
  live.store.poll();
  const expired = await stream(t, live.port, last.id);
  await expired.waitFor((frame) => frame.event === "ready");
  assert.deepEqual(
    expired.frames.map((frame) => frame.event),
    ["reset", "ready"],
  );
  expired.close();
});

test("SSE can deliver a valid large Jev payload without dropping it at the socket high-water mark", async (t) => {
  const { root, file } = fixture(t);
  fs.writeFileSync(file, line("started", { goal: SOURCE }));
  const live = await start(t, root);
  const client = await stream(t, live.port);
  await client.waitFor((frame) => frame.event === "ready");
  const payload = "test-only-observation ".repeat(8000);
  fs.appendFileSync(
    file,
    line("jev_request", { request: { state: { text: payload } } }),
  );
  live.store.poll();
  const frame = await client.waitFor(
    (value) => value.data?.event?.type === "jev_request",
  );
  assert.equal(frame.data.event.request.state.text, payload);
  fs.appendFileSync(file, line("result", { success: true }));
  live.store.poll();
  await client.waitFor((value) => value.data?.event?.type === "result");
  client.close();
});
