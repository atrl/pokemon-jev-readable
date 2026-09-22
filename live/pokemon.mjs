/** One entry point: a real Python game runner and optionally its read-only website. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createLiveServer } from './server.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const args = process.argv.slice(2);
const withLive = args.includes('--live');
const forwarded = args.filter(arg => arg !== '--live');
const defaultPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.POKEMON_PYTHON || (fs.existsSync(defaultPython) ? defaultPython : 'python3');
const runtime = path.join(root, '.live');
fs.mkdirSync(runtime, { recursive: true });
const lock = path.join(runtime, 'pokemon.lock');
let locked = false;
let live;
let child;

function unlock() {
  if (locked) { fs.rmSync(lock, { force: true }); locked = false; }
}
function acquire() {
  for (let attempt = 0; attempt < 2; attempt++) {
    try { fs.writeFileSync(lock, String(process.pid), { flag: 'wx', mode: 0o600 }); locked = true; return; }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const pid = Number(fs.readFileSync(lock, 'utf8'));
      if (!Number.isInteger(pid) || pid < 1) throw new Error('Invalid .live/pokemon.lock; inspect the previous runner first.');
      try { process.kill(pid, 0); throw new Error('A Pokémon runner is already active.'); }
      catch (running) { if (running.code !== 'ESRCH') throw running; }
      fs.rmSync(lock, { force: true });
    }
  }
  throw new Error('Could not acquire Pokémon runner lock.');
}

try {
  acquire();
  if (!forwarded.some(arg => arg === '--output' || arg.startsWith('--output='))) forwarded.push('--output', path.join('pokemon/runs', new Date().toISOString().replaceAll(':', '-') + '-' + process.pid));
  if (withLive) {
    live = createLiveServer({ root, config: { pokemon: { keyConfigured: !!process.env.TYPESAFE_API_KEY } } });
    await new Promise((resolve, reject) => {
      live.server.once('error', reject);
      live.server.listen(Number(process.env.LIVE_PORT || 18766), process.env.LIVE_HOST || '127.0.0.1', resolve);
    });
    console.log(`Pokémon JEV Live: http://${process.env.LIVE_HOST || '127.0.0.1'}:${live.server.address().port}`);
  }
  child = spawn(python, ['-u', 'pokemon/run.py', ...forwarded], { cwd: root, stdio: 'inherit', env: process.env });
  child.once('error', async error => { console.error(`Python runner: ${error.message}`); unlock(); if (live) await live.close(); process.exitCode = 1; });
  child.once('exit', (code, signal) => {
    unlock();
    if (live) console.log(`Runner ended (${signal || code}). Website remains available. Ctrl+C closes it.`);
    else process.exitCode = code ?? 1;
  });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGINT');
    if (live) await live.close();
  });
} catch (error) {
  console.error(error.message);
  unlock();
  if (live) await live.close();
  process.exitCode = 1;
}
process.once('exit', unlock);
