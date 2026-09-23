/** Repository contracts only; local ROMs/logs are allowed but never tracked. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const tracked = () => execFileSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8' }).split('\0').filter(Boolean);
const ignores = file => {
  const result = spawnSync('git', ['check-ignore', '--no-index', '--stdin'], { cwd: root, input: file + '\n', encoding: 'utf8' });
  assert.ok([0, 1].includes(result.status), result.stderr);
  return result.status === 0;
};

test('no generated evidence, archives, recordings, ROMs or private files are tracked', () => {
  const bad = tracked().filter(file =>
    /(^|\/)(evidence|archive|results|runs|\.work|\.live)(\/|$)/.test(file) ||
    /(^|\/)\.env($|\.)/.test(file) && !file.endsWith('.env.example') ||
    /\.(gb|gbc|gba|sav|state|jsonl|log|mp4|gif|zip|m3u8|m4s)$/.test(file) ||
    /\.state\.json$|\.progress\.json$|\.campaign\.json$/.test(file));
  assert.deepEqual(bad, []);
});

test('local outputs and secrets are ignored without hiding runtime data', () => {
  for (const file of ['.env', 'pokemon/.env', 'roms/game.gb', 'red-star-2020-08-18.gb',
    'pokemon/runs/session/report.json', 'pokemon/.work/bedroom.state', '.live/pokemon.lock',
    'pokemon/evidence/report.json', 'pokemon/results/index.html', 'pokemon/archive/old.yml',
    'outputs/session/index.html']) assert.ok(ignores(file), file);
  for (const file of ['.env.example', 'pokemon/redstar-profile.json', 'pokemon/redstar-world.json',
    'pokemon/redstar-route-regions.json', 'pokemon/plan_contract.py', 'live/public/app.js'])
    assert.equal(ignores(file), false, file);
});

test('required data and third-party runtime license remain present', () => {
  for (const file of ['pokemon/redstar-profile.json', 'pokemon/redstar-world.json',
    'pokemon/redstar-route-regions.json']) assert.equal(typeof JSON.parse(fs.readFileSync(path.join(root,file),'utf8')), 'object');
  assert.ok(fs.readFileSync(path.join(root, 'live/public/vendor/hls.js.LICENSE'), 'utf8').includes('Apache'));
  assert.ok(fs.statSync(path.join(root, 'live/public/vendor/hls.min.js')).size > 0);
});

test('canonical docs have no broken relative links', () => {
  for (const file of ['README.md', 'docs/ARCHITECTURE.md', 'docs/DECISIONS.md', 'docs/TROUBLESHOOTING.md']) {
    const content = fs.readFileSync(path.join(root, file), 'utf8');
    for (const match of content.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
      const link = match[1].split('#')[0];
      if (!link || /^(https?:|mailto:)/.test(link)) continue;
      assert.ok(fs.existsSync(path.resolve(root, path.dirname(file), decodeURIComponent(link))), `${file}: ${link}`);
    }
  }
});

test('CI remains read-only and never republishes game outputs', () => {
  const workflow = fs.readFileSync(path.join(root,'.github/workflows/redstar-memory.yml'), 'utf8');
  assert.ok(workflow.includes('contents: read'));
  for (const forbidden of ['contents: write', 'secrets.', 'publish_result.py', 'git push',
    'pokemon/test_live.py', 'pokemon/test_dual_live.py', 'pokemon/results/'])
    assert.equal(workflow.includes(forbidden), false, forbidden);
});
