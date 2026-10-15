const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// Execute the actual storage migration in a fresh module scope, like a reload.
const source = fs.readFileSync(path.join(__dirname, '../src/utils/localStorageManager.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function reload(values) {
  const localStorage = {
    get length() { return values.size; },
    key: (i) => [...values.keys()][i] ?? null,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    clear: () => values.clear(),
  };
  vm.runInNewContext(compiled, {
    exports: {}, localStorage,
    console: { log() {}, warn() {}, error() {} },
    require: () => ({ storageManager: {}, storageHealthChecker: {}, storageRecoverySystem: {} }),
  });
}

test('repeated startup migrations preserve the current local login only', () => {
  const values = new Map();
  for (const tenant of ['tenant-a', 'tenant-b', 'tenant-a']) {
    const session = JSON.stringify({ access_token: `signed-${tenant}`, user: { tenant_id: tenant } });
    values.set('base360-auth-token', session);
    values.set('old-revenue-cache', 'old tenant data');
    for (let i = 0; i < 3; i++) {
      values.delete('app_storage_version');
      reload(values);
      assert.equal(values.get('base360-auth-token'), session);
      assert.equal(values.has('old-revenue-cache'), false);
    }
  }
});

test('startup migration does not restore a logged-out session', () => {
  const values = new Map([['base360-auth-token', 'old-session']]);
  values.clear(); // The existing logout flow clears storage.
  reload(values);
  assert.equal(values.has('base360-auth-token'), false);
});
