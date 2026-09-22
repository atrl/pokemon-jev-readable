/** Read-only JSONL → SSE. The browser never controls games or receives credentials. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { redact } from './redact.mjs';

const PUBLIC = fileURLToPath(new URL('./public/', import.meta.url));
const MAX_LINE = 1024 * 1024;
const TERMINAL = new Set(['completed', 'stopped', 'failed', 'interrupted', 'budget_reached', 'blocked_missing_key', 'blocked_connection']);

export class EventStore {
  constructor(root, { maxEvents = 2000, maxRuns = 40, secrets = [], onEvent = () => {} } = {}) {
    Object.assign(this, { root, maxEvents, maxRuns, secrets, onEvent });
    this.runs = new Map();
    this.files = new Map();
  }
  sources() {
    const sources = [];
    const list = dir => { try { return fs.readdirSync(dir, { withFileTypes: true }); } catch { return []; } };
    const logs = path.join(this.root, 'logs');
    for (const entry of list(logs)) if (entry.isFile() && entry.name.endsWith('.jsonl')) sources.push({ file: path.join(logs, entry.name), game: 'minecraft' });
    const pokemon = path.join(this.root, 'pokemon/runs');
    for (const entry of list(pokemon)) if (entry.isDirectory()) sources.push({ file: path.join(pokemon, entry.name, 'events.jsonl'), game: 'pokemon' });
    return sources.flatMap(source => {
      try {
        const stat = fs.lstatSync(source.file);
        return stat.isFile() ? [{ ...source, stat }] : [];
      } catch { return []; }
    }).sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs).slice(0, this.maxRuns);
  }
  poll() {
    const sources = this.sources();
    const active = new Set(sources.map(source => source.file));
    for (const [file, cursor] of this.files) if (!active.has(file)) { this.files.delete(file); this.runs.delete(cursor.id); }
    for (const { file, game, stat } of sources) {
      let cursor = this.files.get(file);
      if (!cursor || cursor.ino !== stat.ino || stat.size < cursor.offset) {
        if (cursor) this.runs.delete(cursor.id);
        const id = crypto.createHash('sha256').update(path.relative(this.root, file) + ':' + stat.birthtimeMs).digest('hex').slice(0, 20);
        cursor = { id, ino: stat.ino, offset: 0, pending: Buffer.alloc(0), number: 0, dropping: false };
        this.files.set(file, cursor);
        this.runs.set(id, { id, game, startedAt: stat.birthtime.toISOString(), updatedAt: stat.mtime.toISOString(), status: 'waiting', steps: 0, calls: 0, results: 0, events: [], _file: file, _modelMs: 0, _latencyCount: 0, _lastLatencyKey: null });
      }
      if (stat.size <= cursor.offset) continue;
      // One bounded chunk per pass also prevents a large historical log blocking SSE.
      const chunk = Buffer.alloc(Math.min(MAX_LINE, stat.size - cursor.offset));
      let fd;
      try {
        fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
        const read = fs.readSync(fd, chunk, 0, chunk.length, cursor.offset);
        cursor.offset += read;
        let data = Buffer.concat([cursor.pending, chunk.subarray(0, read)]);
        let end;
        while ((end = data.indexOf(10)) !== -1) {
          const line = data.subarray(0, end);
          data = data.subarray(end + 1);
          if (cursor.dropping || line.length > MAX_LINE) { cursor.dropping = false; continue; }
          try { this.append(cursor, JSON.parse(line.toString('utf8'))); } catch { /* Partial/corrupt records never become fabricated events. */ }
        }
        if (data.length > MAX_LINE || cursor.dropping) { cursor.dropping = true; cursor.pending = Buffer.alloc(0); }
        else cursor.pending = Buffer.from(data);
      } catch { /* A deleted/rotating source is rediscovered next pass. */ }
      finally { if (fd !== undefined) fs.closeSync(fd); }
    }
  }
  append(cursor, raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || typeof raw.type !== 'string') return;
    const run = this.runs.get(cursor.id);
    const event = { ...redact(raw, this.secrets), id: `${cursor.id}:${++cursor.number}` };
    if (typeof event.time !== 'string' || !Number.isFinite(Date.parse(event.time))) event.time = run.updatedAt;
    else event.time = new Date(event.time).toISOString();
    if (event.type === 'started' && (!Number.isInteger(event.pid) || event.pid < 1)) delete event.pid;
    run.eventCount = cursor.number;
    run.updatedAt = event.time || run.updatedAt;
    run.steps = Math.max(run.steps, Number.isInteger(event.step) ? event.step : 0);
    run.lastEvent = event.type;
    if (event.type === 'started') { run.startedAt = event.time || run.startedAt; run.goal = event.goal; run.maxSteps = event.maxSteps; run.pid = event.pid; run.videoEnabled = event.video_enabled === true; run.status = 'starting'; }
    if (typeof event.elapsed_ms === 'number' && Number.isFinite(event.elapsed_ms) && event.elapsed_ms >= 0) run._elapsedMs = event.elapsed_ms;
    if (typeof event.game_frames === 'number' && Number.isFinite(event.game_frames)) run.gameFrames = event.game_frames;
    if (event.type === 'video_started') { run.videoEnabled = true; run._videoMetadata = event.video; }
    if (['jev_response','jev_error'].includes(event.type) && Number.isFinite(event.latency_ms) && event.latency_ms >= 0) {
      const latencyKey = `${event.step}:${event.attempt ?? 1}`;
      if (latencyKey !== run._lastLatencyKey) { run._modelMs += event.latency_ms; run._latencyCount++; run._lastLatencyKey = latencyKey; }
    }
    if (event.type === 'observation') run.status = 'observing';
    if (event.type === 'jev_request') { run.calls++; run.status = 'calling'; }
    if (event.type === 'jev_response') run.status = 'received';
    if (event.type === 'jev_error') run.status = 'request_error';
    if (event.type === 'decision') run.status = 'decided';
    if (event.type === 'executing') run.status = 'executing';
    if (event.type === 'result') { run.results++; run.status = event.success === false ? 'action_error' : 'running'; }
    if (event.type === 'finished') { run.status = event.status || 'stopped'; run.reason = event.reason || event.error; }
    run.events.push(event);
    if (run.events.length > this.maxEvents) run.events.splice(0, run.events.length - this.maxEvents);
    this.onEvent({ run: this.summary(run), event });
  }
  summary(run) {
    const { events, pid, _file, _elapsedMs, _modelMs, _latencyCount, _lastLatencyKey, _videoMetadata, ...summary } = run;
    if (pid && !TERMINAL.has(summary.status)) {
      try { process.kill(pid, 0); } catch (error) { if (error.code === 'ESRCH') { summary.status = 'interrupted'; summary.reason = '运行进程已退出，未收到结束事件。'; } }
    }
    const sampledAt = new Date();
    let elapsed = _elapsedMs ?? Math.max(0, Date.parse(run.updatedAt) - Date.parse(run.startedAt));
    if (!TERMINAL.has(summary.status)) elapsed += Math.max(0, sampledAt.getTime() - Date.parse(run.updatedAt));
    const rate = elapsed > 0 ? run.results * 60000 / elapsed : 0;
    const remaining = Math.max(0, (run.maxSteps || 0) - run.results);
    const playlist = this.videoFile(run.id, 'index.m3u8');
    summary.video = { enabled: run.videoEnabled === true, available: !!playlist, url: `/api/runs/${run.id}/video/index.m3u8`,
      fps: _videoMetadata?.fps ?? 30, width: _videoMetadata?.width ?? 160, height: _videoMetadata?.height ?? 144, format: 'hls-h264' };
    summary.timing = { sampled_at: sampledAt.toISOString(), elapsed_ms: Math.round(elapsed), model_ms: _modelMs, http_attempts: run.calls,
      avg_latency_ms: _latencyCount ? _modelMs / _latencyCount : null, steps_per_minute: rate, remaining_steps: remaining,
      estimated_remaining_ms: run.results >= 5 && rate > 0 && !TERMINAL.has(summary.status) ? remaining * 60000 / rate : null,
      game_seconds: typeof run.gameFrames === 'number' ? run.gameFrames / 60 : null };
    return { ...summary, retainedEvents: events.length, truncated: events.length > 0 && !events[0].id.endsWith(':1') };
  }
  videoFile(id, name) {
    const run = this.runs.get(id);
    if (!run || run.game !== 'pokemon' || !/^(?:index\.m3u8|init\.mp4|segment-\d{6,10}\.m4s)$/.test(name)) return null;
    const directory = path.join(path.dirname(run._file), 'video');
    const file = path.join(directory, name);
    try {
      if (!fs.lstatSync(directory).isDirectory() || fs.lstatSync(directory).isSymbolicLink()) return null;
      const stat = fs.lstatSync(file);
      return stat.isFile() && !stat.isSymbolicLink() ? { file, size: stat.size } : null;
    } catch { return null; }
  }
  list() { return [...this.runs.values()].map(run => this.summary(run)).sort((a, b) => b.startedAt.localeCompare(a.startedAt)); }
  detail(id) { const run = this.runs.get(id); return run ? { run: this.summary(run), events: run.events } : null; }
}

export function createLiveServer({ root = process.cwd(), config = {}, pollMs = 500, heartbeatMs = 15000, secrets = [process.env.TYPESAFE_API_KEY] } = {}) {
  const clients = new Set();
  const replay = [];
  const epoch = crypto.randomUUID();
  let sequence = 0;
  const send = (client, event, data, id) => {
    if (client.destroyed || client.writableEnded) { clients.delete(client); return; }
    client.write(`${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    // false from write means queued backpressure, not a failed connection.
    if (client.writableLength > 4 * MAX_LINE) { clients.delete(client); client.destroy(); }
  };
  const store = new EventStore(path.resolve(root), { secrets, onEvent(data) {
    const item = { id: `${epoch}:${++sequence}`, data };
    replay.push(item); if (replay.length > 1000) replay.shift();
    for (const client of clients) send(client, 'update', data, item.id);
  } });
  store.poll();
  const server = http.createServer((request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('Content-Security-Policy', "default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'none'; media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    const json = (code, value) => { response.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' }); response.end(JSON.stringify(value)); };
    if (!['GET', 'HEAD'].includes(request.method)) return json(405, { error: 'Read-only live feed' });
    let url;
    try { url = new URL(request.url, 'http://localhost'); } catch { return json(400, { error: 'Invalid URL' }); }
    if (url.pathname === '/health') return json(200, { status: 'ok', serverTime: new Date().toISOString() });
    if (url.pathname === '/api/runs') return json(200, { runs: store.list(), config, serverTime: new Date().toISOString() });
    if (/^\/api\/runs\/[a-f0-9]{20}$/.test(url.pathname)) {
      const detail = store.detail(url.pathname.split('/').at(-1));
      return json(detail ? 200 : 404, detail || { error: 'Unknown run' });
    }
    const videoRoute = url.pathname.match(/^\/api\/runs\/([a-f0-9]{20})\/video\/(index\.m3u8|init\.mp4|segment-\d{6,10}\.m4s)$/);
    if (videoRoute) {
      const asset = store.videoFile(videoRoute[1], videoRoute[2]);
      if (!asset) return json(404, { error: 'Video segment is not available yet or has expired' });
      let fd;
      try { fd = fs.openSync(asset.file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); }
      catch { return json(404, { error: 'Video segment expired' }); }
      const size = fs.fstatSync(fd).size;
      let start = 0, end = size - 1, code = 200;
      if (request.headers.range) {
        const range = request.headers.range.match(/^bytes=(\d*)-(\d*)$/);
        if (!range || (!range[1] && !range[2])) { fs.closeSync(fd); return json(416, { error: 'Invalid range' }); }
        start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
        end = range[1] && range[2] ? Math.min(size - 1, Number(range[2])) : size - 1;
        if (start > end || start >= size) { fs.closeSync(fd); response.setHeader('Content-Range', `bytes */${size}`); return json(416, { error: 'Range out of bounds' }); }
        code = 206; response.setHeader('Content-Range', `bytes ${start}-${end}/${size}`);
      }
      const contentType = videoRoute[2].endsWith('.m3u8') ? 'application/vnd.apple.mpegurl' : videoRoute[2].endsWith('.mp4') ? 'video/mp4' : 'video/iso.segment';
      response.writeHead(code, { 'Content-Type': contentType, 'Content-Length': Math.max(0,end - start + 1), 'Accept-Ranges': 'bytes' });
      if (size === 0) { fs.closeSync(fd); response.end(); return; }
      const stream = fs.createReadStream(asset.file, { fd, autoClose: true, start, end });
      stream.on('error', () => response.destroy());
      response.on('close', () => stream.destroy());
      stream.pipe(response);
      return;
    }
    if (url.pathname === '/api/events') {
      if (clients.size >= 50) return json(503, { error: 'Viewer limit reached; retry later' });
      response.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
      response.flushHeaders();
      clients.add(response);
      const last = request.headers['last-event-id'];
      if (last) {
        const index = replay.findIndex(item => item.id === last);
        if (index === -1) send(response, 'reset', {});
        else for (const item of replay.slice(index + 1)) send(response, 'update', item.data, item.id);
      }
      send(response, 'ready', { serverTime: new Date().toISOString() });
      response.on('close', () => clients.delete(response));
      return;
    }
    const assets = { '/': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/style.css': ['style.css', 'text/css'], '/vendor/hls.min.js': ['vendor/hls.min.js', 'text/javascript'] };
    const asset = assets[url.pathname];
    if (!asset) return json(404, { error: 'Not found' });
    try { const body = fs.readFileSync(path.join(PUBLIC, asset[0])); response.writeHead(200, { 'Content-Type': `${asset[1]}; charset=utf-8` }); response.end(body); }
    catch { json(503, { error: 'Live page is not installed' }); }
  });
  const poller = setInterval(() => store.poll(), pollMs);
  const heartbeat = setInterval(() => { for (const client of clients) send(client, 'heartbeat', { serverTime: new Date().toISOString() }); }, heartbeatMs);
  server.on('close', () => { clearInterval(poller); clearInterval(heartbeat); });
  const close = async () => { clearInterval(poller); clearInterval(heartbeat); for (const client of clients) client.end(); await new Promise(resolve => server.close(resolve)); };
  return { server, store, close };
}

async function main() {
  const root = path.resolve(process.env.LIVE_ROOT || fileURLToPath(new URL('../', import.meta.url)));
  const host = process.env.LIVE_HOST || '127.0.0.1';
  const port = Number(process.env.LIVE_PORT || 18766);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('LIVE_PORT must be 1..65535');
  const python = process.env.POKEMON_PYTHON || (fs.existsSync(path.join(root, '.venv/bin/python')) ? path.join(root, '.venv/bin/python') : 'python3');
  const emulatorAvailable = spawnSync(python, ['-c', 'import pyboy'], { timeout: 5000, stdio: 'ignore' }).status === 0;
  const config = { pokemon: { keyConfigured: !!process.env.TYPESAFE_API_KEY?.trim(), emulatorAvailable } };
  const live = createLiveServer({ root, config });
  live.server.listen(port, host, () => console.log(`JEV Live: http://${host}:${port} (read-only, JSONL → SSE)`));
  live.server.on('error', error => { console.error(error.message); live.close().finally(() => { process.exitCode = 1; }); });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => live.close());
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) main().catch(error => { console.error(error.message); process.exitCode = 1; });
