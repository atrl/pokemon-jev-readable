/** Filesystem contracts: source vs runtime output, restored assets, one tests root. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const assets = {
  'roms/red-star-2020-08-18.gb': '3cc54b4c888b0cbda1c27996806ae4729199b377',
  'roms/1636 - Pokemon Fire Red (U)(Squirrels).gba': 'a0d042c360c18b860da3ef71ebac0d826e136cdc',
};
const tracked = () => execFileSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8' }).split('\0').filter(Boolean);
const ignores = file => {
  const result = spawnSync('git', ['check-ignore', '--no-index', '--stdin'], { cwd: root, input: file + '\n', encoding: 'utf8' });
  assert.ok([0, 1].includes(result.status), result.stderr);
  return result.status === 0;
};

test('generated evidence, archives, recordings and secrets stay untracked', () => {
  const bad = tracked().filter(file => !(file in assets) && (
    /(^|\/)(evidence|archive|results|runs|\.work|\.live)(\/|$)/.test(file) ||
    /(^|\/)\.env($|\.)/.test(file) && !file.endsWith('.env.example') ||
    /\.(gb|gbc|gba|sav|state|jsonl|log|mp4|gif|zip|m3u8|m4s)$/.test(file) ||
    /\.state\.json$|\.progress\.json$|\.campaign\.json$/.test(file)));
  assert.deepEqual(bad, []);
});

test('the original user assets are restored byte-for-byte by Git blob identity', () => {
  const staged = execFileSync('git', ['ls-files', '-s', '-z'], { cwd: root, encoding: 'utf8' }).split('\0');
  for (const [file, sha] of Object.entries(assets)) {
    const row = staged.find(row => row.endsWith('\t' + file));
    assert.ok(row, 'Missing restored asset: ' + file);
    assert.equal(row.split(' ')[1], sha, file);
  }
});

test('only the two explicitly restored ROMs are excepted from ignore rules', () => {
  for (const file of ['.env', 'pokemon/.env', 'roms/another.gb', 'red-star-2020-08-18.gb',
    'pokemon/runs/session/report.json', 'pokemon/.work/bedroom.state', '.live/pokemon.lock',
    'pokemon/evidence/report.json', 'pokemon/results/index.html', 'pokemon/archive/old.yml',
    'outputs/tests/rom/report.json']) assert.ok(ignores(file), file);
  for (const file of [...Object.keys(assets), 'roms/README.md', '.env.example', 'pokemon/data/redstar-profile.json',
    'prompts/system2/planner.txt', 'prompts/system1/button.txt']) assert.equal(ignores(file), false, file);
});

test('test source is only under tests and model prompts have their own directories', () => {
  const files = tracked();
  assert.deepEqual(files.filter(file => /(^|\/)test_[^/]+\.py$|\.test\.mjs$/.test(file) && !file.startsWith('tests/')), []);
  assert.equal(files.some(file => file.startsWith('pokemon/tests/')), false);
  assert.equal(files.some(file => file.startsWith('pokemon/prompts/')), false);
  for (const folder of ['python', 'web', 'integration']) assert.ok(fs.statSync(path.join(root, 'tests', folder)).isDirectory());
  const source = fs.readFileSync(path.join(root,'pokemon/planning.py'), 'utf8');
  assert.ok(source.includes('load_prompt("system2/planner.txt")'));
  assert.equal(source.includes('You are System Two, the primary'), false);
});

test('required data and third-party runtime license remain present', () => {
  for (const file of ['redstar-profile.json', 'redstar-world.json', 'redstar-route-regions.json'])
    assert.equal(typeof JSON.parse(fs.readFileSync(path.join(root, 'pokemon/data', file),'utf8')), 'object');
  assert.ok(fs.readFileSync(path.join(root, 'live/public/vendor/hls.js.LICENSE'), 'utf8').includes('Apache'));
  assert.ok(fs.statSync(path.join(root, 'live/public/vendor/hls.min.js')).size > 0);
});

test('canonical docs have no broken relative links', () => {
  for (const file of ['README.md', 'docs/ARCHITECTURE.md', 'docs/DECISIONS.md', 'docs/TROUBLESHOOTING.md',
                     'tests/README.md', 'prompts/README.md', 'roms/README.md']) {
    const content = fs.readFileSync(path.join(root, file), 'utf8');
    for (const match of content.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
      const link = match[1].split('#')[0];
      if (!link || /^(https?:|mailto:)/.test(link)) continue;
      assert.ok(fs.existsSync(path.resolve(root, path.dirname(file), decodeURIComponent(link))), `${file}: ${link}`);
    }
  }
});

test('CI is read-only and never calls models or republishes results into Git', () => {
  const workflow = fs.readFileSync(path.join(root,'.github/workflows/redstar-memory.yml'), 'utf8');
  assert.ok(workflow.includes('contents: read'));
  for (const forbidden of ['contents: write', 'secrets.', 'publish_result.py', 'git push',
    'test_dual_models.py', 'pokemon/results/']) assert.equal(workflow.includes(forbidden), false, forbidden);
  assert.ok(workflow.includes('tests/integration/test_rom.py'));
});
