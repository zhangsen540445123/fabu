import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createPublicKey, createHash } from 'node:crypto';

const root = new URL('../', import.meta.url);
const protocol = () => import('../protocol.mjs');
const now = 1_800_000_000_000;
const request = { type: 'cookie-request', version: 1, requestId: 'a'.repeat(32), videoId: 'VbDUeWoESMY' };
const cookie = (extra = {}) => ({ name: 'SID', value: 'synthetic-only', domain: '.youtube.com', path: '/', secure: true, httpOnly: true, hostOnly: false, session: false, expirationDate: now / 1000 + 300, ...extra });

test('manifest uses only narrow optional consent and a stable public RSA key', async () => {
  const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual([...manifest.permissions].sort(), ['alarms', 'cookies', 'nativeMessaging', 'storage']);
  assert.deepEqual(manifest.optional_host_permissions, ['https://*.youtube.com/*']);
  for (const field of ['host_permissions', 'optional_permissions', 'content_scripts', 'externally_connectable', 'web_accessible_resources', 'update_url']) assert.equal(manifest[field], undefined, field);
  assert.equal(manifest.background.type, 'module');
  assert.equal(manifest.background.service_worker, 'service-worker.mjs');
  assert.equal(manifest.action.default_popup, 'popup.html');
  assert.match(manifest.content_security_policy.extension_pages, /connect-src 'none'/);
  const der = Buffer.from(manifest.key, 'base64');
  const key = createPublicKey({ key: der, format: 'der', type: 'spki' });
  assert.equal(key.asymmetricKeyType, 'rsa');
  assert.ok(key.asymmetricKeyDetails.modulusLength >= 2048);
  const id = createHash('sha256').update(der).digest('hex').slice(0, 32).replace(/[0-9a-f]/g, c => String.fromCharCode(97 + parseInt(c, 16)));
  const readme = await readFile(new URL('README.md', root), 'utf8');
  assert.ok(readme.includes(id), 'document stable extension ID for the native host');
});

test('request schema accepts only exact v1 envelope and bounded identifiers', async () => {
  const { isCookieRequest } = await protocol();
  assert.equal(isCookieRequest(request), true);
  for (const bad of [null, [], {}, { ...request, version: '1' }, { ...request, version: 2 }, { ...request, type: 'status' }, { ...request, requestId: 'A'.repeat(32) }, { ...request, requestId: 'a'.repeat(33) }, { ...request, requestId: 'a'.repeat(32) + '\n' }, { ...request, videoId: '../evil.com' }, { ...request, videoId: 'VbDUeWoESMY\n' }, { ...request, videoId: 123 }, { ...request, cookies: [] }]) assert.equal(isCookieRequest(bad), false);
  assert.equal(isCookieRequest({ ...request, videoId: '_-012345678' }), true);
});

test('cookie response has the exact v1 envelope and whitelisted fields only', async () => {
  const { cookieResponse } = await protocol();
  const input = cookie({ storeId: '0', sameSite: 'lax', priority: 'high' });
  const result = cookieResponse(request, [input], now);
  assert.deepEqual(result, { type: 'cookie-response', version: 1, requestId: request.requestId, videoId: request.videoId, cookies: [cookie()] });
  assert.equal(input.storeId, '0', 'does not mutate Chrome objects');
});

test('drops partitioned, hostile-domain and expired cookies without flattening', async () => {
  const { cookieResponse } = await protocol();
  const discarded = [cookie({ partitionKey: { topLevelSite: 'https://youtube.com' } }), cookie({ partitionKey: {} }), cookie({ partitioned: true }), ...['google.com', 'evilyoutube.com', 'youtube.com.evil.test', '..youtube.com', 'youtube.com.', 'x..youtube.com', '-bad.youtube.com', 'youtube.com:443', 'youtube.com\n', 'a'.repeat(64) + '.youtube.com'].map(domain => cookie({ domain })), cookie({ expirationDate: now / 1000 }), cookie({ expirationDate: 1 })];
  assert.equal(cookieResponse(request, discarded, now).error, 'no_cookies');
  assert.deepEqual(cookieResponse(request, [...discarded, cookie({ domain: 'www.youtube.com', hostOnly: true })], now).cookies, [cookie({ domain: 'www.youtube.com', hostOnly: true })]);
});

test('session cookies omit optional expiration instead of inventing it', async () => {
  const { cookieResponse } = await protocol();
  const input = cookie({ session: true });
  delete input.expirationDate;
  assert.deepEqual(cookieResponse(request, [input], now).cookies, [input]);
});

test('malformed cookie types, injection characters and oversized fields fail closed', async () => {
  const { cookieResponse } = await protocol();
  for (const bad of [null, {}, cookie({ name: '' }), cookie({ value: 'secret\tline' }), cookie({ name: 'SID\nINJECT' }), cookie({ path: '/\rline' }), cookie({ path: 'not-absolute' }), cookie({ secure: 'true' }), cookie({ httpOnly: 1 }), cookie({ hostOnly: null }), cookie({ session: 0 }), cookie({ hostOnly: true }), cookie({ expirationDate: NaN }), cookie({ expirationDate: Infinity }), cookie({ expirationDate: '123' }), cookie({ value: 'x'.repeat(8193) }), cookie({ name: 'x'.repeat(257) }), cookie({ path: '/' + 'x'.repeat(2048) })]) {
    assert.deepEqual(cookieResponse(request, [bad], now), { type: 'cookie-response', version: 1, requestId: request.requestId, videoId: request.videoId, error: 'read_failed' });
  }
});

test('cookie collection and encoded response bytes are bounded', async () => {
  const { cookieResponse } = await protocol();
  assert.equal(cookieResponse(request, [], now).error, 'no_cookies');
  for (const input of [null, {}, Array.from({ length: 257 }, () => cookie()), Array.from({ length: 100 }, () => cookie({ value: 'x'.repeat(4096) })), Array.from({ length: 100 }, () => cookie({ value: '漢'.repeat(1024) }))]) assert.equal(cookieResponse(request, input, now).error, 'read_failed');
  assert.equal(cookieResponse(request, Array.from({ length: 256 }, () => cookie()), now).cookies.length, 256);
});

test('request IDs reject final line terminators even at the exact required length', async () => {
  const { isCookieRequest } = await protocol();
  for (const ending of ['\n', '\r', '\u2028', '\u2029']) {
    assert.equal(isCookieRequest({ ...request, requestId: 'a'.repeat(31) + ending }), false);
    assert.equal(isCookieRequest({ ...request, videoId: 'abcdefghij' + ending }), false);
  }
});

test('subdomain labels reject embedded line terminators before youtube.com', async () => {
  const { cookieResponse } = await protocol();
  for (const ending of ['\n', '\r', '\u2028', '\u2029']) {
    assert.equal(cookieResponse(request, [cookie({ domain: `attacker${ending}.youtube.com` })], now).error, 'no_cookies');
  }
});

test('onboarding is an own-extension options page with no added permission', async () => {
  const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'));
  assert.deepEqual(manifest.options_ui, { page: 'popup.html', open_in_tab: true });
  const expected = ['manifest.json', 'service-worker.mjs', 'worker.mjs', 'protocol.mjs', 'popup.html', 'popup.mjs', 'popup.css'];
  for (const file of expected) {
    const contents = await readFile(new URL(file, root), 'utf8');
    assert.ok(contents.length > 0);
    assert.ok(!/BEGIN (?:RSA )?PRIVATE KEY/.test(contents));
  }
});

test('cookie fields match the native host ASCII/token and expiration bounds', async () => {
  const { cookieResponse } = await protocol();
  for (const bad of [cookie({ name: 'bad name' }), cookie({ name: 'bad=name' }), cookie({ name: 'bad;name' }), cookie({ value: 'unicode-漢' }), cookie({ path: '/café' }), cookie({ expirationDate: 253402300800 })]) {
    assert.equal(cookieResponse(request, [bad], now).error, 'read_failed');
  }
  const valid = cookie({ name: "!#$%&'*+.^_`|~-SID", expirationDate: 253402300799 });
  assert.deepEqual(cookieResponse(request, [valid], now).cookies, [valid]);
});
