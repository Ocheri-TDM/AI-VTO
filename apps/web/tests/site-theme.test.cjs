/* eslint-disable @typescript-eslint/no-require-imports -- Test the actual browser store without adding dependencies. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function browser(saved, blocked = false) {
  const values = new Map([['forma-chat-id', 'project-chat'], ['presentation-revision:doc', 'revision']]);
  if (saved !== undefined) values.set('souvenir:site-theme', saved);
  const listeners = new Map();
  const window = {
    addEventListener: (name, fn) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener: (name, fn) => listeners.get(name)?.delete(fn),
    dispatchEvent: event => { for (const fn of listeners.get(event.type) ?? []) fn(event); },
  };
  const context = vm.createContext({
    exports: {}, window, document: { documentElement: { dataset: {} } }, Event: class { constructor(type) { this.type = type; } },
    localStorage: {
      getItem: key => { if (blocked) throw Error('Storage denied'); return values.get(key) ?? null; },
      setItem: (key, value) => { if (blocked) throw Error('Storage denied'); values.set(key, value); },
    },
  });
  const source = fs.readFileSync(path.join(__dirname, '../src/lib/site-theme.ts'), 'utf8');
  vm.runInContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
  return { context, store: context.exports, values, window, listeners };
}

test('SSR uses current; head bootstrap accepts only known theme values', () => {
  for (const [saved, expected] of [[undefined, 'current'], ['current', 'current'], ['pulse', 'pulse'], ['<script>bad</script>', 'current']]) {
    const b = browser(saved);
    assert.equal(b.store.getServerSiteTheme(), 'current');
    vm.runInContext(b.store.THEME_INIT_SCRIPT, b.context);
    assert.equal(b.store.getSiteTheme(), expected);
  }
});

test('switch persists independently of project and document state and restores on F5', () => {
  const b = browser('current');
  vm.runInContext(b.store.THEME_INIT_SCRIPT, b.context);
  let calls = 0;
  const dispose = b.store.subscribeSiteTheme(() => calls++);
  b.store.setSiteTheme('pulse');
  assert.equal(b.store.getSiteTheme(), 'pulse');
  assert.equal(b.values.get('souvenir:site-theme'), 'pulse');
  assert.equal(b.values.get('forma-chat-id'), 'project-chat');
  assert.equal(b.values.get('presentation-revision:doc'), 'revision');
  assert.equal(calls, 1);
  const reload = browser(b.values.get('souvenir:site-theme'));
  vm.runInContext(reload.store.THEME_INIT_SCRIPT, reload.context);
  assert.equal(reload.store.getSiteTheme(), 'pulse');
  dispose();
  assert.equal(b.listeners.get('storage').size, 0);
});

test('other tabs synchronize only the theme key; clearing storage restores current', () => {
  const b = browser('current');
  vm.runInContext(b.store.THEME_INIT_SCRIPT, b.context);
  let calls = 0;
  b.store.subscribeSiteTheme(() => calls++);
  b.values.set('souvenir:site-theme', 'pulse');
  b.window.dispatchEvent({ type: 'storage', key: 'forma-chat-id' });
  assert.equal(calls, 0);
  b.window.dispatchEvent({ type: 'storage', key: 'souvenir:site-theme' });
  assert.equal(b.store.getSiteTheme(), 'pulse');
  b.values.delete('souvenir:site-theme');
  b.window.dispatchEvent({ type: 'storage', key: null });
  assert.equal(b.store.getSiteTheme(), 'current');
});

test('blocked storage still allows theme switching for the current page', () => {
  const b = browser('pulse', true);
  vm.runInContext(b.store.THEME_INIT_SCRIPT, b.context);
  assert.equal(b.store.getSiteTheme(), 'current');
  b.store.setSiteTheme('pulse');
  assert.equal(b.store.getSiteTheme(), 'pulse');
});
