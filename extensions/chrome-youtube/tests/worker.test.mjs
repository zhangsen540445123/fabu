import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fakeChrome, deferred, settle, request, syntheticCookie } from './fake-chrome.mjs';
const load = () => import('../worker.mjs');
async function setup(saved = {}, options = {}) {
  const fake = fakeChrome(saved);
  Object.assign(fake, options);
  const { createBridge } = await load();
  const bridge = createBridge(fake.api);
  await bridge.ready;
  return { fake, bridge };
}
async function enabled() { const result = await setup(); await result.bridge.enable(); return { ...result, port: result.fake.ports[0] }; }

test('disabled by default; startup, install and irrelevant alarms do not connect or read', async () => {
  const { fake, bridge } = await setup();
  fake.api.runtime.onStartup.emit(); fake.api.runtime.onInstalled.emit();
  await fake.fireAlarm(); await fake.fireAlarm('unrelated');
  assert.deepEqual(bridge.getState(), { enabled: false, status: 'disabled' });
  assert.equal(fake.calls.connect.length, 0); assert.equal(fake.calls.cookies.length, 0);
});

test('restores explicit enabled state, connects only the fixed host and never prefetches', async () => {
  const { fake, bridge } = await setup({ enabled: true });
  assert.equal(bridge.getState().enabled, true);
  assert.deepEqual(fake.calls.connect, ['com.fabu.youtube_session']);
  assert.equal(fake.calls.cookies.length, 0);
  assert.deepEqual(fake.calls.contains[0], { origins: ['https://*.youtube.com/*'] });
  const wrong = await setup({ enabled: 'true' });
  assert.equal(wrong.fake.ports.length, 0);
});

test('enable requires already granted optional consent; worker never prompts', async () => {
  const { fake, bridge } = await setup({}, { permission: false });
  assert.deepEqual(await bridge.enable(), { enabled: false, status: 'permission_required' });
  assert.equal(fake.ports.length, 0); assert.equal(fake.calls.request.length, 0);
  fake.permission = true;
  await bridge.enable();
  assert.equal(fake.data.enabled, true); assert.equal(fake.ports.length, 1);
});

test('simultaneous enables, alarms and startup events cannot duplicate the native port', async () => {
  const { fake, bridge } = await setup();
  await Promise.all([bridge.enable(), bridge.enable(), bridge.enable(), fake.fireAlarm()]);
  fake.api.runtime.onStartup.emit(); fake.api.runtime.onInstalled.emit();
  await fake.fireAlarm();
  assert.equal(fake.ports.length, 1);
});

test('only a valid active-native-port request reads the exact YouTube domain', async () => {
  const { fake, port } = await enabled();
  for (const message of [null, {}, { type: 'status', version: 1, status: 'connected' }, { ...request(), requestId: 'UPPER' }, { ...request(), videoId: '../bad' }, { ...request(), version: 2 }, { ...request(), extra: true }]) port.onMessage.emit(message);
  await fake.api.runtime.sendMessage(request()); await settle();
  assert.equal(fake.calls.cookies.length, 0);
  port.onMessage.emit(request()); await settle();
  assert.deepEqual(fake.calls.cookies, [{ domain: 'youtube.com' }]);
  assert.deepEqual(port.sent, [{ ...request(), type: 'cookie-response', cookies: [syntheticCookie] }]);
  assert.equal(port.disconnected, false, 'a reply does not close the heartbeat connection');
});

test('host connected status changes only sanitized status and resets backoff', async () => {
  const { fake, bridge, port } = await enabled();
  port.onMessage.emit({ type: 'status', version: 1, status: 'PRIVATE secret' });
  assert.equal(bridge.getState().status, 'connecting');
  port.onMessage.emit({ type: 'status', version: 1, status: 'connected' }); await settle();
  assert.deepEqual(bridge.getState(), { enabled: true, status: 'connected' });
  await fake.fireAlarm();
  fake.api.runtime.onStartup.emit(); await settle();
  assert.equal(port.disconnected, false); assert.equal(fake.ports.length, 1);
  assert.equal(fake.calls.cookies.length, 0);
});

test('cookie API failures and empty results have sanitized protocol errors', async () => {
  const { fake, port } = await enabled();
  fake.cookieHook = async () => { throw new Error(syntheticCookie.value); };
  port.onMessage.emit(request(1)); await settle();
  fake.cookieHook = async () => [];
  port.onMessage.emit(request(2)); await settle();
  assert.deepEqual(port.sent.map(m => m.error), ['read_failed', 'no_cookies']);
  assert.ok(!JSON.stringify(port.sent).includes(syntheticCookie.value));
});

test('missing permission answers permission_required without reading then disables', async () => {
  const { fake, bridge, port } = await enabled();
  fake.permission = false;
  port.onMessage.emit(request()); await settle();
  assert.equal(port.sent[0].error, 'permission_required');
  assert.equal(fake.calls.cookies.length, 0); assert.equal(bridge.getState().enabled, false);
  assert.equal(port.disconnected, true);
});

test('disable disconnects immediately, revokes permission and persists across restart', async () => {
  const { fake, bridge, port } = await enabled();
  await bridge.disable();
  assert.equal(port.disconnected, true); assert.equal(fake.data.enabled, false);
  assert.deepEqual(fake.calls.remove, [{ origins: ['https://*.youtube.com/*'] }]);
  await fake.fireAlarm(); fake.api.runtime.onStartup.emit(); fake.api.permissions.onAdded.emit({ origins: ['https://*.youtube.com/*'] }); await settle();
  assert.equal(fake.ports.length, 1);
  const restart = await setup(fake.data);
  assert.equal(restart.fake.ports.length, 0);
  fake.permission = true; await bridge.enable(); assert.equal(fake.ports.length, 2);
});

test('external revocation disables and does not reconnect on later grant', async () => {
  const { fake, bridge, port } = await enabled();
  fake.permission = false; fake.api.permissions.onRemoved.emit({ origins: ['https://*.youtube.com/*'] }); await settle();
  assert.equal(bridge.getState().enabled, false); assert.equal(port.disconnected, true);
  fake.permission = true; fake.api.permissions.onAdded.emit({ origins: ['https://*.youtube.com/*'] });
  await fake.fireAlarm(); assert.equal(fake.ports.length, 1);
});

test('disable gates a pending consent check before any cookie read', async () => {
  const { fake, bridge, port } = await enabled();
  const pending = deferred(); fake.containsHook = () => pending.promise;
  port.onMessage.emit(request()); await settle();
  await bridge.disable(); pending.resolve(true); await settle();
  assert.equal(fake.calls.cookies.length, 0); assert.deepEqual(port.sent, []);
});

test('disable then re-enable cannot leak an old asynchronous cookie result', async () => {
  const { fake, bridge, port } = await enabled();
  const pending = deferred(); fake.cookieHook = () => pending.promise;
  port.onMessage.emit(request()); await settle();
  assert.equal(fake.calls.cookies.length, 1);
  await bridge.disable(); fake.permission = true; await bridge.enable();
  pending.resolve([syntheticCookie]); await settle();
  assert.deepEqual(port.sent, []); assert.deepEqual(fake.ports[1].sent, []);
});

test('native disconnect invalidates old messages and asynchronous reads', async () => {
  const { fake, port } = await enabled();
  const pending = deferred(); fake.cookieHook = () => pending.promise;
  port.onMessage.emit(request()); await settle(); port.disconnect(); await settle();
  await fake.fireAlarm();
  port.onMessage.emit(request(2)); pending.resolve([syntheticCookie]); await settle();
  assert.deepEqual(port.sent, []); assert.equal(fake.calls.cookies.length, 1); assert.equal(fake.ports.length, 2);
});

test('single-flight requests and replay IDs do not issue duplicate cookie reads', async () => {
  const { fake, port } = await enabled(); const pending = deferred(); fake.cookieHook = () => pending.promise;
  port.onMessage.emit(request()); port.onMessage.emit(request()); port.onMessage.emit(request(2)); await settle();
  assert.equal(fake.calls.cookies.length, 1);
  pending.resolve([syntheticCookie]); await settle(); port.onMessage.emit(request()); await settle();
  assert.equal(fake.calls.cookies.length, 1);
  assert.equal(port.sent.filter(m => m.cookies).length, 1);
});

test('failed native connections use a capped alarm backoff without hot loops', async () => {
  const { fake, bridge } = await setup({}, { connectError: true });
  await bridge.enable(); await settle();
  assert.equal(bridge.getState().enabled, true); assert.equal(bridge.getState().status, 'reconnecting');
  assert.equal(fake.calls.connect.length, 1);
  for (let i = 0; i < 7; i++) await fake.fireAlarm();
  assert.deepEqual(fake.calls.alarms.map(a => a.delayInMinutes), [1, 2, 4, 8, 16, 30, 30, 30]);
  assert.ok(fake.calls.alarms.every(a => a.name === 'fabu-reconnect'));
  await bridge.disable(); await fake.fireAlarm(); assert.equal(fake.calls.connect.length, 8);
});

test('startup respects an existing reconnect alarm instead of reconnecting immediately', async () => {
  const fake = fakeChrome({ enabled: true, retryDelay: 8 });
  fake.alarms.set('fabu-reconnect', { name: 'fabu-reconnect', scheduledTime: Date.now() + 100_000 });
  const { createBridge } = await load(); const bridge = createBridge(fake.api); await bridge.ready;
  assert.equal(fake.ports.length, 0); assert.equal(bridge.getState().status, 'reconnecting');
  await fake.fireAlarm(); assert.equal(fake.ports.length, 1);
});

test('a pending enable cannot overtake a later disable', async () => {
  const { fake, bridge } = await setup(); const pending = deferred(); fake.containsHook = () => pending.promise;
  const enabling = bridge.enable(); await settle();
  const disabling = bridge.disable(); pending.resolve(true); await Promise.all([enabling, disabling]);
  assert.equal(fake.ports.length, 0); assert.equal(fake.data.enabled, false);
});

test('startup storage read cannot restore consent after a concurrent disable', async () => {
  const fake = fakeChrome({ enabled: true }); const pending = deferred(); fake.getHook = () => pending.promise;
  const { createBridge } = await load(); const bridge = createBridge(fake.api);
  const disabling = bridge.disable(); pending.resolve(); await Promise.all([bridge.ready, disabling]);
  assert.equal(fake.ports.length, 0); assert.equal(fake.data.enabled, false);
});

test('popup commands require the exact internal popup sender; no IDs or secrets in state/storage', async () => {
  const { fake, bridge, port } = await enabled();
  for (const sender of [{ id: 'other', url: fake.api.runtime.getURL('popup.html') }, { id: fake.api.runtime.id, url: 'https://youtube.com' }, { id: fake.api.runtime.id, url: 'https://youtube.com', tab: { id: 1 } }]) await fake.api.runtime.sendMessage({ type: 'disable' }, sender);
  assert.equal(bridge.getState().enabled, true);
  port.onMessage.emit(request()); await settle();
  assert.deepEqual(await fake.api.runtime.sendMessage({ type: 'get-state' }), bridge.getState());
  for (const record of fake.calls.storage) assert.ok(Object.keys(record).every(key => ['enabled', 'retryDelay'].includes(key)));
  assert.ok(!JSON.stringify([fake.data, bridge.getState()]).includes(syntheticCookie.value));
  await fake.api.runtime.sendMessage({ type: 'disable' }); assert.equal(bridge.getState().enabled, false);
});

test('first install opens own onboarding page without consent/read/connect; updates do not', async () => {
  const { fake, bridge } = await setup();
  let opened = 0;
  fake.api.runtime.openOptionsPage = async () => { opened++; };
  fake.api.runtime.onInstalled.emit({ reason: 'install' }); await settle();
  assert.equal(opened, 1);
  assert.deepEqual(bridge.getState(), { enabled: false, status: 'disabled' });
  assert.equal(fake.calls.connect.length, 0); assert.equal(fake.calls.cookies.length, 0); assert.equal(fake.calls.request.length, 0);
  fake.api.runtime.onInstalled.emit({ reason: 'update' }); fake.api.runtime.onInstalled.emit({ reason: 'chrome_update' }); await settle();
  assert.equal(opened, 1);
});

test('onboarding failure does not disable or disconnect an enabled native session', async () => {
  const { fake, bridge, port } = await enabled();
  fake.api.runtime.openOptionsPage = async () => { throw new Error('PRIVATE setup path'); };
  fake.api.runtime.onInstalled.emit({ reason: 'install' }); await settle();
  assert.equal(bridge.getState().enabled, true); assert.equal(port.disconnected, false);
});

test('disable clears a late-created reconnect alarm and persists false after a pending write', async () => {
  const { fake, bridge, port } = await enabled();
  const pending = deferred(); fake.alarmHook = () => pending.promise;
  port.disconnect(); await settle();
  const disabling = bridge.disable(); pending.resolve(); await disabling; await settle();
  assert.equal(fake.alarms.size, 0); assert.equal(fake.data.enabled, false);
  await fake.fireAlarm(); assert.equal(fake.ports.length, 1);
});

test('rechecks permission after the cookie promise resolves before sending any values', async () => {
  const { fake, bridge, port } = await enabled();
  const pending = deferred(); fake.cookieHook = () => pending.promise;
  port.onMessage.emit(request()); await settle(); fake.permission = false;
  pending.resolve([syntheticCookie]); await settle();
  assert.deepEqual(port.sent, [{ ...request(), type: 'cookie-response', error: 'permission_required' }]);
  assert.equal(bridge.getState().enabled, false);
});

test('late cookie reads never send session values beyond the request lifetime', async () => {
  const fake = fakeChrome({ enabled: true }); let clock = 1_800_000_000_000;
  const { createBridge } = await load(); const bridge = createBridge(fake.api, { now: () => clock }); await bridge.ready;
  const pending = deferred(); fake.cookieHook = () => pending.promise;
  fake.ports[0].onMessage.emit(request()); await settle(); clock += 25_000;
  pending.resolve([syntheticCookie]); await settle();
  assert.equal(fake.ports[0].sent[0].error, 'read_failed');
});

test('storage errors fail closed without preventing permission revocation or leaking text', async () => {
  const { fake, bridge, port } = await enabled();
  fake.setHook = async () => { throw new Error(syntheticCookie.value); };
  const state = await bridge.disable();
  assert.deepEqual(state, { enabled: false, status: 'unavailable' });
  assert.equal(port.disconnected, true); assert.equal(fake.permission, false);
  await fake.fireAlarm(); assert.equal(fake.ports.length, 1);
});

test('postMessage failure disconnects the broken native port and waits for alarm', async () => {
  const { fake, bridge, port } = await enabled(); fake.postError = true;
  port.onMessage.emit(request()); await settle();
  assert.equal(port.disconnected, true); assert.equal(bridge.getState().status, 'reconnecting');
  assert.equal(fake.ports.length, 1); assert.equal(fake.alarms.size, 1);
});

test('own onboarding page can control consent when Chrome supplies a tab sender', async () => {
  const { fake, bridge } = await setup();
  const sender = { id: fake.api.runtime.id, url: fake.api.runtime.getURL('popup.html'), tab: { id: 7 } };
  await fake.api.runtime.sendMessage({ type: 'enable' }, sender);
  assert.equal(bridge.getState().enabled, true);
  await fake.api.runtime.sendMessage({ type: 'disable' }, sender);
  assert.equal(bridge.getState().enabled, false);
});
