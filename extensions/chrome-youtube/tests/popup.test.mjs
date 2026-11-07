import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { fakeChrome, settle } from './fake-chrome.mjs';
function fakeDocument() {
  const elements = new Map();
  for (const id of ['enable', 'disable', 'refresh', 'status']) {
    const listeners = new Map();
    elements.set(id, { disabled: false, textContent: '', addEventListener: (type, fn) => listeners.set(type, fn), click: () => listeners.get('click')?.() });
  }
  return { getElementById: id => elements.get(id) };
}
async function setup() {
  const fake = fakeChrome(), document = fakeDocument();
  fake.api.runtime.sendMessage = async message => {
    fake.calls.messages.push(message);
    return message.type === 'enable' ? { enabled: true, status: 'connecting' } : { enabled: false, status: 'disabled' };
  };
  const { mountPopup } = await import('../popup.mjs'); mountPopup(fake.api, document); await settle();
  return { fake, document };
}

test('enable calls permissions.request synchronously from the click, before messaging worker', async () => {
  const { fake, document } = await setup();
  assert.equal(fake.calls.request.length, 0);
  let clicked = false;
  fake.onRequest = () => { clicked = true; assert.equal(fake.calls.messages.filter(m => m.type === 'enable').length, 0); };
  const result = document.getElementById('enable').click();
  assert.equal(clicked, true, 'request called on the user gesture stack');
  await result; await settle();
  assert.deepEqual(fake.calls.request, [{ origins: ['https://*.youtube.com/*'] }]);
  assert.ok(fake.calls.messages.some(m => m.type === 'enable'));
});

test('denial never enables; disable never requests consent', async () => {
  const { fake, document } = await setup(); fake.requestGranted = false;
  await document.getElementById('enable').click(); await settle();
  assert.equal(fake.calls.messages.some(m => m.type === 'enable'), false);
  assert.match(document.getElementById('status').textContent, /权限/);
  await document.getElementById('disable').click(); await settle();
  assert.deepEqual(fake.calls.messages.at(-1), { type: 'disable' }); assert.equal(fake.calls.request.length, 1);
});

test('popup renders only fixed status copy, never native errors or cookie values', async () => {
  const { fake, document } = await setup();
  fake.api.runtime.sendMessage = async () => ({ enabled: true, status: 'PRIVATE cookie path value' });
  await document.getElementById('refresh').click(); await settle();
  assert.ok(!document.getElementById('status').textContent.includes('PRIVATE'));
  fake.api.runtime.sendMessage = async () => { throw new Error('PRIVATE error'); };
  await document.getElementById('disable').click(); await settle();
  assert.ok(!document.getElementById('status').textContent.includes('PRIVATE'));
});

test('packaged UI and worker entry point are local, minimal and secret-free', async () => {
  const html = await readFile(new URL('../popup.html', import.meta.url), 'utf8');
  for (const id of ['enable', 'disable', 'refresh', 'status']) assert.ok(html.includes(`id="${id}"`));
  assert.match(html, /type="module" src="popup.mjs"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /lang="zh-CN"/);
  assert.match(html, />启用连接<\/button>/);
  assert.ok(!/<script(?![^>]*src=)/.test(html));
  const entry = await readFile(new URL('../service-worker.mjs', import.meta.url), 'utf8');
  assert.match(entry, /createBridge\(chrome\)/);
  for (const file of ['worker.mjs', 'protocol.mjs', 'popup.mjs', 'service-worker.mjs']) {
    const source = await readFile(new URL('../' + file, import.meta.url), 'utf8');
    assert.ok(!/\b(?:fetch|XMLHttpRequest|WebSocket|eval)\s*\(|console\.|localStorage|https?:\/\/(?!\*\.youtube\.com\/\*)/.test(source), file);
  }
});
