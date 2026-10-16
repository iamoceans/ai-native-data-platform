const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { webcrypto } = require('node:crypto');
const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../../src/api/turnRetries.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;

function store() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
function load(sessionStorage) {
  const exports = {};
  vm.runInNewContext(code, { exports, sessionStorage, crypto: webcrypto });
  return exports;
}

test('unresolved retry keys survive a module reload using tab storage', () => {
  const storage = store();
  const first = load(storage).pendingTurnRetryKey('owner', 'session', 'turn');
  assert.equal(load(storage).pendingTurnRetryKey('owner', 'session', 'turn'), first);
  assert.equal(storage.values.size, 1);
});

test('retry keys are isolated by account, conversation and original turn', () => {
  const retries = load(store());
  const keys = [['owner', 's1', 't1'], ['other', 's1', 't1'], ['owner', 's2', 't1'], ['owner', 's1', 't2']]
    .map(args => retries.pendingTurnRetryKey(...args));
  assert.equal(new Set(keys).size, 4);
});

test('confirmed success forgets only its operation and allows a new explicit retry', () => {
  const storage = store(), retries = load(storage);
  const first = retries.pendingTurnRetryKey('owner', 'session', 'turn');
  const other = retries.pendingTurnRetryKey('owner', 'session', 'other');
  retries.forgetTurnRetryKey('owner', 'session', 'turn');
  assert.equal(storage.values.size, 1);
  const restored = load(storage);
  assert.notEqual(restored.pendingTurnRetryKey('owner', 'session', 'turn'), first);
  assert.equal(restored.pendingTurnRetryKey('owner', 'session', 'other'), other);
});

test('blocked browser storage still supports repeated attempts in memory', () => {
  const denied = () => { throw new Error('Storage blocked'); };
  const retries = load({ getItem: denied, setItem: denied, removeItem: denied });
  const first = retries.pendingTurnRetryKey('owner', 'session', 'turn');
  assert.equal(retries.pendingTurnRetryKey('owner', 'session', 'turn'), first);
  retries.forgetTurnRetryKey('owner', 'session', 'turn');
  assert.notEqual(retries.pendingTurnRetryKey('owner', 'session', 'turn'), first);
});

test('invalid persisted keys are replaced before making a request', () => {
  const storage = store();
  load(storage).pendingTurnRetryKey('owner', 'session', 'turn');
  for (const key of storage.values.keys()) storage.values.set(key, 'invalid-key');
  const key = load(storage).pendingTurnRetryKey('owner', 'session', 'turn');
  assert.match(key, /^[0-9a-f-]{36}$/);
  assert.ok([...storage.values.values()].includes(key));
});
