/** Launch the Python game runner, optionally alongside its read-only website. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createLiveServer } from './server.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));

export function parseArguments(args) {
  const resume = args.includes('--resume');
  const pythonArgs = args.filter(arg => !['--live', '--resume'].includes(arg));
  if (resume && hasOption(pythonArgs, '--state')) throw new Error('Choose either --resume or --state.');
  return { withLive: args.includes('--live'), resume, pythonArgs };
}

function hasOption(args, option) {
  return args.some(arg => arg === option || arg.startsWith(option + '='));
}

function pythonExecutable(root) {
  const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return process.env.POKEMON_PYTHON || (fs.existsSync(local) ? local : 'python3');
}

/** Choose the newest save whose ROM and content hashes match its manifest. */
export function findResumeState(root) {
  const runs = path.join(root, 'pokemon/runs');
  const romSha1 = JSON.parse(fs.readFileSync(path.join(root, 'pokemon/data/redstar-profile.json'), 'utf8')).rom_sha1;
  const candidates = [];
  for (const entry of fs.existsSync(runs) ? fs.readdirSync(runs, { withFileTypes: true }) : []) {
    if (!entry.isDirectory()) continue;
    const file = path.join(runs, entry.name, 'last.state');
    try {
      const manifest = JSON.parse(fs.readFileSync(file + '.json', 'utf8'));
      const digest = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
      if (manifest.rom_sha1 === romSha1 && manifest.state_sha256 === digest) {
        candidates.push({ file, modified: fs.statSync(file).mtimeMs });
      }
    } catch { /* Incomplete/corrupt saves are not resume candidates. */ }
  }
  candidates.sort((a, b) => b.modified - a.modified);
  if (!candidates.length) throw new Error('No verified previous save found; start without --resume or provide --state.');
  return candidates[0].file;
}

/** Hold one runner lock until Python exits; a leftover dead PID can be reclaimed. */
export function acquireRunnerLock(lock) {
  fs.mkdirSync(path.dirname(lock), { recursive: true });
  const owner = String(process.pid);
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      fs.writeFileSync(lock, owner, { flag: 'wx', mode: 0o600 });
      let released = false;
      return () => {
        if (released) return;
        released = true;
        try {
          if (fs.readFileSync(lock, 'utf8') === owner) fs.rmSync(lock);
        } catch (error) { if (error.code !== 'ENOENT') throw error; }
      };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const pid = Number(fs.readFileSync(lock, 'utf8'));
      if (!Number.isInteger(pid) || pid < 1) throw new Error('Invalid .live/pokemon.lock; inspect the previous runner first.');
      try {
        process.kill(pid, 0);
        throw new Error('A Pokémon runner is already active.');
      } catch (running) {
        if (running.code !== 'ESRCH') throw running;
      }
      fs.rmSync(lock, { force: true });
    }
  }
  throw new Error('Could not acquire Pokémon runner lock.');
}

async function startWebsite(root) {
  const live = createLiveServer({ root, config: { pokemon: { keyConfigured: !!process.env.TYPESAFE_API_KEY } } });
  const host = process.env.LIVE_HOST || '127.0.0.1';
  try {
    await new Promise((resolve, reject) => {
      live.server.once('error', reject);
      live.server.listen(Number(process.env.LIVE_PORT || 18766), host, resolve);
    });
  } catch (error) {
    await live.close();
    throw error;
  }
  console.log(`Pokémon JEV Live: http://${host}:${live.server.address().port}`);
  return live;
}

async function main() {
  let release = () => {};
  let live;
  try {
    const { withLive, resume, pythonArgs } = parseArguments(process.argv.slice(2));
    const help = pythonArgs.includes('--help') || pythonArgs.includes('-h');
    if (help) {
      console.log('Launcher options: --live starts the website; --resume loads the newest verified save.\nOther options are passed to the Python runner below.');
    } else {
      release = acquireRunnerLock(path.join(ROOT, '.live/pokemon.lock'));
      process.once('exit', release);
      if (resume) {
        const state = findResumeState(ROOT);
        pythonArgs.push('--state', state);
        console.log(`Resume verified save: ${path.relative(ROOT, state)}`);
      }
      if (!hasOption(pythonArgs, '--output')) {
        const run = new Date().toISOString().replaceAll(':', '-') + '-' + process.pid;
        pythonArgs.push('--output', path.join('pokemon/runs', run));
      }
      if (withLive) live = await startWebsite(ROOT);
    }

    const child = spawn(pythonExecutable(ROOT), ['-u', 'pokemon/run.py', ...pythonArgs], {
      cwd: ROOT, stdio: 'inherit', env: process.env,
    });
    child.once('error', async error => {
      console.error(`Python runner: ${error.message}`);
      release();
      if (live) await live.close();
      process.exitCode = 1;
    });
    child.once('exit', (code, signal) => {
      release();
      if (live) console.log(`Runner ended (${signal || code}). Website remains available. Ctrl+C closes it.`);
      else process.exitCode = code ?? 1;
    });
    for (const signal of ['SIGINT', 'SIGTERM']) {
      process.once(signal, async () => {
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGINT');
        if (live) await live.close();
      });
    }
  } catch (error) {
    console.error(error.message);
    release();
    if (live) await live.close();
    process.exitCode = 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  await main();
}
