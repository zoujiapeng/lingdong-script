#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const includeRuntime = process.argv.includes('--runtime');

if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log(`Usage:
  npm run clean          Remove generated build outputs, caches, and pycache.
  npm run clean:runtime  Also remove ignored python-runtime/.

Default clean removes: dist, release, test/browser caches, E2E shots, and
Python __pycache__ under source/test helper directories. It does not remove
node_modules or python-runtime.`);
  process.exit(0);
}

const rootEntries = [
  'dist',
  'release',
  '.pytest_cache',
  '.playwright-mcp',
  '.playwright-cli',
  '_e2e_shots',
  '.e2e',
];

if (includeRuntime) {
  rootEntries.push('python-runtime');
}

const pycacheRoots = ['python', 'tests', 'utils', 'vision'];

function insideRoot(target) {
  const rel = path.relative(root, target);
  return rel && !rel.startsWith('..') && !path.isAbsolute(rel);
}

function dirSize(target) {
  if (!fs.existsSync(target)) return 0;
  const stat = fs.statSync(target);
  if (stat.isFile()) return stat.size;
  let total = 0;
  for (const entry of fs.readdirSync(target, { withFileTypes: true })) {
    total += dirSize(path.join(target, entry.name));
  }
  return total;
}

function removeEntry(target) {
  const abs = path.resolve(root, target);
  if (!insideRoot(abs)) {
    throw new Error(`Refusing to remove outside workspace: ${abs}`);
  }
  if (!fs.existsSync(abs)) return 0;
  const before = dirSize(abs);
  fs.rmSync(abs, { recursive: true, force: true });
  return before;
}

function collectPycache(base) {
  const abs = path.resolve(root, base);
  const found = [];
  if (!fs.existsSync(abs)) return found;
  for (const entry of fs.readdirSync(abs, { withFileTypes: true })) {
    const full = path.join(abs, entry.name);
    if (!entry.isDirectory()) continue;
    if (entry.name === '__pycache__') {
      found.push(full);
    } else {
      found.push(...collectPycache(path.relative(root, full)));
    }
  }
  return found;
}

let freed = 0;
for (const entry of rootEntries) {
  const bytes = removeEntry(entry);
  if (!bytes) continue;
  freed += bytes;
  console.log(`removed ${entry} (${(bytes / 1024 / 1024).toFixed(2)} MB)`);
}

for (const base of pycacheRoots) {
  for (const cache of collectPycache(base)) {
    const bytes = dirSize(cache);
    fs.rmSync(cache, { recursive: true, force: true });
    freed += bytes;
    console.log(`removed ${path.relative(root, cache)} (${(bytes / 1024).toFixed(1)} KB)`);
  }
}

console.log(`freed ${(freed / 1024 / 1024).toFixed(2)} MB`);
