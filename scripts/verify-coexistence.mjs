import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const original = '57b0ef300c255a21817185e356e9f5b1f5e367a1';
const tree = execFileSync('git', ['ls-tree', '-r', '-z', original], { cwd: root }).toString();
let count = 0;
for (const record of tree.split('\0').filter(Boolean)) {
  const [meta, name] = record.split('\t');
  const [, type, expected] = meta.split(' ');
  assert.equal(type, 'blob', `Unsupported snapshot entry: ${name}`);
  const bytes = fs.readFileSync(path.join(root, 'legacy', 'v1', name));
  const blobHash = (data) => createHash('sha1').update(`blob ${data.length}\0`).update(data).digest('hex');
  // Git checkout/archive may apply CRLF on Windows. Only allow that text conversion.
  const normalized = bytes.includes(0) ? bytes : Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'));
  assert.ok(blobHash(bytes) === expected || blobHash(normalized) === expected,
    `Missing or changed v1 snapshot: ${name}`);
  count++;
}
for (const entry of ['desktop/package.json', 'orchestrator/package.json', 'scripts/start.ps1',
  'legacy/v1/server.py', 'legacy/v1/frontend/package.json', 'legacy/v1/requirements.txt']) {
  assert.ok(fs.existsSync(path.join(root, entry)), `Missing entry point: ${entry}`);
}
for (const entry of ['desktop/tsconfig.json', 'orchestrator/tsconfig.json', 'scripts/start.ps1']) {
  assert.doesNotMatch(fs.readFileSync(path.join(root, entry), 'utf8'), /legacy[\\/]v1/);
}
const wrapper = fs.readFileSync(path.join(root, 'scripts/start-v1.ps1'), 'utf8');
assert.match(wrapper, /ASTOCK_DATA_HOME/);
assert.match(wrapper, /VR_DATA_DIR/);
assert.match(wrapper, /8910/);
console.log(`PASS: ${count} v1 files match ${original}; v1/v2 entry points are isolated.`);
