import { HOST, ORIGINS, isCookieRequest, cookieResponse, errorResponse } from './protocol.mjs';

const ALARM = 'fabu-reconnect';
const permission = { origins: ORIGINS };
const RETRIES = [1, 2, 4, 8, 16, 30];

export function createBridge(chrome, { now = Date.now } = {}) {
  let enabled = false, status = 'disabled', epoch = 0, connection = null, retryDelay = 1;
  let tail = Promise.resolve();
  const getState = () => ({ enabled, status });
  const live = (token, candidate = connection) => enabled && epoch === token && candidate === connection;

  function detach(candidate) {
    if (!candidate) return;
    candidate.port.onMessage.removeListener(candidate.onMessage);
    candidate.port.onDisconnect.removeListener(candidate.onDisconnect);
    try { candidate.port.disconnect(); } catch { /* Already disconnected. */ }
  }

  function invalidate(nextStatus) {
    ++epoch;
    enabled = false;
    status = nextStatus;
    retryDelay = 1;
    const old = connection;
    connection = null;
    detach(old);
  }

  async function cleanup() {
    // Independent attempts: a storage failure must not prevent consent revocation.
    const results = await Promise.allSettled([
      chrome.storage.local.set({ enabled: false, retryDelay: 1 }),
      chrome.alarms.clear(ALARM),
      chrome.permissions.remove(permission)
    ]);
    if (results.some(result => result.status === 'rejected')) status = 'unavailable';
  }

  function serial(work) {
    tail = tail.then(work).catch(async () => { invalidate('unavailable'); await cleanup(); });
    return tail.then(getState);
  }

  function disable(nextStatus = 'disabled') {
    // Synchronous invalidation precedes all queued I/O, including enable/startup.
    invalidate(nextStatus);
    return serial(cleanup);
  }

  async function schedule(token) {
    if (!live(token) || connection) return;
    status = 'reconnecting';
    await chrome.alarms.create(ALARM, { delayInMinutes: retryDelay });
    if (!live(token)) { await chrome.alarms.clear(ALARM); return; }
    retryDelay = Math.min(30, retryDelay * 2);
    await chrome.storage.local.set({ retryDelay });
  }

  function lost(candidate) {
    if (connection !== candidate) return;
    connection = null;
    detach(candidate);
    const token = ++epoch;
    if (enabled) void serial(() => schedule(token));
  }

  function send(candidate, token, message) {
    if (!live(token, candidate)) return;
    try { candidate.port.postMessage(message); } catch { lost(candidate); }
  }

  async function serve(candidate, request) {
    const token = epoch;
    if (!live(token, candidate) || !isCookieRequest(request) || candidate.seen.has(request.requestId)) return;
    if (candidate.busy) { send(candidate, token, errorResponse(request, 'read_failed')); return; }
    candidate.seen.add(request.requestId);
    if (candidate.seen.size > 128) candidate.seen.delete(candidate.seen.values().next().value);
    candidate.busy = true;
    const started = now();
    try {
      const allowed = await chrome.permissions.contains(permission);
      if (!live(token, candidate)) return;
      if (!allowed) {
        send(candidate, token, errorResponse(request, 'permission_required'));
        void disable('permission_required');
        return;
      }
      // The sole cookie-read site. No prefetch, other domains, stores or URLs.
      const cookies = await chrome.cookies.getAll({ domain: 'youtube.com' });
      if (!live(token, candidate)) return;
      const stillAllowed = await chrome.permissions.contains(permission);
      if (!live(token, candidate)) return;
      if (!stillAllowed) {
        send(candidate, token, errorResponse(request, 'permission_required'));
        void disable('permission_required');
        return;
      }
      send(candidate, token, now() - started >= 25_000
        ? errorResponse(request, 'read_failed') : cookieResponse(request, cookies, now()));
    } catch {
      send(candidate, token, errorResponse(request, 'read_failed'));
    } finally {
      candidate.busy = false;
    }
  }

  async function connect(token) {
    if (!live(token) || connection) return;
    const allowed = await chrome.permissions.contains(permission);
    if (!live(token) || connection) return;
    if (!allowed) { invalidate('permission_required'); await cleanup(); return; }
    await chrome.alarms.clear(ALARM);
    if (!live(token) || connection) return;
    status = 'connecting';
    let port;
    try { port = chrome.runtime.connectNative(HOST); }
    catch { await schedule(token); return; }
    const candidate = { port, busy: false, seen: new Set() };
    candidate.onMessage = message => {
      if (!enabled || connection !== candidate) return;
      if (message?.type === 'status' && message.version === 1 && message.status === 'connected' && Object.keys(message).length === 3) {
        status = 'connected';
        retryDelay = 1;
        void serial(async () => {
          if (enabled && connection === candidate) await chrome.storage.local.set({ retryDelay: 1 });
        });
      } else { void serve(candidate, message); }
    };
    candidate.onDisconnect = () => {
      // Read Chrome's lastError to acknowledge it, never log its contents.
      void chrome.runtime.lastError;
      lost(candidate);
    };
    connection = candidate;
    port.onMessage.addListener(candidate.onMessage);
    port.onDisconnect.addListener(candidate.onDisconnect);
  }

  function enable() {
    const token = ++epoch;
    return serial(async () => {
      if (token !== epoch) return;
      const allowed = await chrome.permissions.contains(permission);
      if (token !== epoch) return;
      if (!allowed) { invalidate('permission_required'); await cleanup(); return; }
      enabled = true;
      await chrome.storage.local.set({ enabled: true });
      if (live(token)) await connect(token);
    });
  }

  async function wake() {
    if (!enabled || connection) return;
    const token = epoch;
    const alarm = await chrome.alarms.get(ALARM);
    if (!live(token)) return;
    if (alarm) status = 'reconnecting';
    else await connect(token);
  }

  // Register synchronously so MV3 can dispatch events while storage is loading.
  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm.name === ALARM) void serial(() => connect(epoch));
  });
  chrome.runtime.onStartup.addListener(() => { void serial(wake); });
  chrome.runtime.onInstalled.addListener(details => {
    void serial(wake);
    // Only our declared extension page; no tabs permission or consent prompt.
    if (details?.reason === 'install') void chrome.runtime.openOptionsPage().catch(() => {});
  });
  chrome.permissions.onRemoved.addListener(() => { if (enabled) void disable('permission_required'); });
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender?.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html') ||
        !message || Object.keys(message).length !== 1 || !['enable', 'disable', 'get-state'].includes(message.type)) return false;
    const operation = message.type === 'enable' ? enable() : message.type === 'disable' ? disable() : tail.then(getState);
    void operation.then(respond);
    return true;
  });

  const initialEpoch = epoch;
  const ready = serial(async () => {
    const saved = await chrome.storage.local.get(['enabled', 'retryDelay']);
    if (initialEpoch !== epoch) return;
    enabled = saved.enabled === true;
    retryDelay = RETRIES.includes(saved.retryDelay) ? saved.retryDelay : 1;
    if (!enabled) { await chrome.alarms.clear(ALARM); return; }
    const allowed = await chrome.permissions.contains(permission);
    if (!live(initialEpoch)) return;
    if (!allowed) { invalidate('permission_required'); await cleanup(); return; }
    await wake();
  });
  return { ready, enable, disable, getState };
}
