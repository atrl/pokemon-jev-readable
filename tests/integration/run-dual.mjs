/** Explicit, bounded dual-model test; never part of npm test or automatic CI. */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../../', import.meta.url));
const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.POKEMON_PYTHON || (existsSync(local) ? local : 'python3');
const child = spawn(python, ['tests/integration/test_dual_models.py', ...process.argv.slice(2)], {
  cwd: root, env: process.env, stdio: 'inherit',
});
child.once('error', error => { console.error(error.message); process.exitCode = 1; });
child.once('exit', code => { process.exitCode = code ?? 1; });
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => child.kill('SIGINT'));
