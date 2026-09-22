/** Isolated launcher fixtures: no ROM, real API key, emulator or gameplay. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { once } from 'node:events';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { acquireRunnerLock, findResumeState, parseArguments } from '../live/pokemon.mjs';

function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pokemon-launcher-fixture-')));
  fs.mkdirSync(path.join(root, 'pokemon'), { recursive: true });
  fs.writeFileSync(path.join(root, 'pokemon/redstar-profile.json'), JSON.stringify({ rom_sha1: 'fixture-rom' }));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function save(root, name, modified, { valid = true, rom = 'fixture-rom' } = {}) {
  const file = path.join(root, 'pokemon/runs', name, 'last.state');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'offline-state-fixture-' + name);
  fs.writeFileSync(file + '.json', JSON.stringify({ rom_sha1: rom,
    state_sha256: valid ? crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') : 'incorrect' }));
  fs.utimesSync(file, modified, modified);
  return file;
}

test('wrapper flags are removed while Python options and explicit state/output are preserved', () => {
  assert.deepEqual(parseArguments(['--live', '--resume', '--steps', '5000', '--no-video']), {
    withLive: true, resume: true, pythonArgs: ['--steps', '5000', '--no-video'],
  });
  assert.deepEqual(parseArguments(['--state=local.state', '--output', 'test-output']).pythonArgs,
    ['--state=local.state', '--output', 'test-output']);
  for (const args of [['--resume', '--state', 'save'], ['--resume', '--state=save']]) {
    assert.throws(() => parseArguments(args), /either --resume or --state/);
  }
});

test('resume selects newest verified save, skipping newer corrupt or wrong-ROM files', t => {
  const root = fixture(t);
  save(root, 'older', 100);
  const selected = save(root, 'valid', 200);
  save(root, 'bad-hash', 300, { valid: false });
  save(root, 'wrong-rom', 400, { rom: 'different-fixture' });
  assert.equal(findResumeState(root), selected);
});

test('resume without a verified save fails without creating a run', t => {
  const root = fixture(t);
  assert.throws(() => findResumeState(root), /No verified previous save/);
  assert.equal(fs.existsSync(path.join(root, 'pokemon/runs')), false);
});

test('lock excludes another live runner and release is idempotent', t => {
  const lock = path.join(fixture(t), '.live/pokemon.lock');
  const release = acquireRunnerLock(lock);
  assert.throws(() => acquireRunnerLock(lock), /runner is already active/);
  release(); release();
  assert.equal(fs.existsSync(lock), false);
});

test('invalid lock is preserved; stale PID can be reclaimed without deleting another owner on release', t => {
  const lock = path.join(fixture(t), '.live/pokemon.lock');
  fs.mkdirSync(path.dirname(lock));
  fs.writeFileSync(lock, 'not-a-pid');
  assert.throws(() => acquireRunnerLock(lock), /Invalid/);
  assert.equal(fs.readFileSync(lock, 'utf8'), 'not-a-pid');
  fs.writeFileSync(lock, '123456');
  t.mock.method(process, 'kill', () => { throw Object.assign(new Error('fixture dead PID'), { code: 'ESRCH' }); });
  const release = acquireRunnerLock(lock);
  assert.equal(fs.readFileSync(lock, 'utf8'), String(process.pid));
  fs.writeFileSync(lock, '654321');
  release();
  assert.equal(fs.readFileSync(lock, 'utf8'), '654321');
});

async function launchFixture(t, args, program) {
  const root = fixture(t);
  fs.mkdirSync(path.join(root, 'live'));
  fs.copyFileSync(fileURLToPath(new URL('../live/pokemon.mjs', import.meta.url)), path.join(root, 'live/pokemon.mjs'));
  const server = new URL('../live/server.mjs', import.meta.url).href;
  fs.writeFileSync(path.join(root, 'live/server.mjs'), `export { createLiveServer } from ${JSON.stringify(server)};\n`);
  const fakePython = path.join(root, 'fake-python');
  fs.writeFileSync(fakePython, '#!/usr/bin/env node\n' + program, { mode: 0o755 });
  const saved = save(root, 'resume-fixture', 100);
  const child = spawn(process.execPath, [path.join(root, 'live/pokemon.mjs'), ...args], {
    cwd: os.tmpdir(), env: { ...process.env, POKEMON_PYTHON: fakePython, LIVE_HOST: '127.0.0.1', LIVE_PORT: '0', TYPESAFE_API_KEY: '' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const ended = once(child, 'exit');
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  let output = '';
  child.stdout.on('data', bytes => { output += bytes; });
  child.stderr.on('data', bytes => { output += bytes; });
  async function waitFor(pattern) {
    const deadline = Date.now() + 4000;
    while (!pattern.test(output)) {
      if (Date.now() > deadline || child.exitCode !== null) assert.fail('Fixture output did not match: ' + output);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    return output;
  }
  return { root, saved, child, ended, waitFor, output: () => output };
}

test('live launcher preserves resume/path forwarding and keeps website after runner exits', { skip: process.platform === 'win32' }, async t => {
  const fixture = await launchFixture(t, ['--live', '--resume', '--steps', '7'],
    "console.log('FIXTURE_ARGS ' + JSON.stringify({cwd:process.cwd(),args:process.argv.slice(2)}));\n");
  await fixture.waitFor(/Website remains available/);
  const parsed = JSON.parse(fixture.output().match(/FIXTURE_ARGS (.+)/)[1]);
  assert.equal(parsed.cwd, fixture.root);
  assert.deepEqual(parsed.args.slice(0, 4), ['-u', 'pokemon/run.py', '--steps', '7']);
  assert.equal(parsed.args[parsed.args.indexOf('--state') + 1], fixture.saved);
  assert.match(parsed.args[parsed.args.indexOf('--output') + 1], /^pokemon\/runs\//);
  assert.equal(fs.existsSync(path.join(fixture.root, '.live/pokemon.lock')), false);
  const url = fixture.output().match(/Pokémon JEV Live: (http:\/\/[^\s]+)/)[1];
  assert.equal((await fetch(url + '/api/runs')).status, 200);
  fixture.child.kill('SIGTERM');
  assert.equal((await fixture.ended)[0], 0);
});

test('SIGTERM forwards SIGINT to the runner and releases its lock', { skip: process.platform === 'win32' }, async t => {
  const fixture = await launchFixture(t, ['--steps', '9'],
    "process.on('SIGINT',()=>{console.log('FIXTURE_INTERRUPTED');process.exit(0)}); console.log('FIXTURE_READY'); setTimeout(()=>process.exit(3),3500);\n");
  await fixture.waitFor(/FIXTURE_READY/);
  assert.equal(fs.existsSync(path.join(fixture.root, '.live/pokemon.lock')), true);
  fixture.child.kill('SIGTERM');
  assert.equal((await fixture.ended)[0], 0);
  assert.match(fixture.output(), /FIXTURE_INTERRUPTED/);
  assert.equal(fs.existsSync(path.join(fixture.root, '.live/pokemon.lock')), false);
});
