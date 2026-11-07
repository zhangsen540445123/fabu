export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export const settle = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
export const event = () => {
  const listeners = new Set();
  return { addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn), emit: (...args) => [...listeners].map(fn => fn(...args)) };
};
export const request = (n = 1) => ({ type: 'cookie-request', version: 1, requestId: n.toString(16).padStart(32, '0'), videoId: 'VbDUeWoESMY' });
export const syntheticCookie = { name: 'SID', value: 'FAKE-COOKIE-NEVER-LOG-OR-STORE', domain: '.youtube.com', path: '/', secure: true, httpOnly: true, hostOnly: false, session: true };

export function fakeChrome(saved = {}) {
  const calls = { connect: [], cookies: [], contains: [], request: [], remove: [], storage: [], alarms: [], messages: [] };
  const data = { ...saved }, alarms = new Map(), ports = [];
  const fake = { calls, data, alarms, ports, permission: true, requestGranted: true, cookieResult: [syntheticCookie] };
  const runtime = {
    id: 'test-extension', getURL: path => `chrome-extension://test-extension/${path}`,
    onMessage: event(), onStartup: event(), onInstalled: event(), lastError: undefined,
    connectNative(name) {
      calls.connect.push(name);
      if (fake.connectError) throw new Error('PRIVATE native path');
      const port = { onMessage: event(), onDisconnect: event(), sent: [], disconnected: false,
        postMessage(message) { if (fake.postError) throw new Error('PRIVATE post error'); this.sent.push(structuredClone(message)); },
        disconnect() { this.disconnected = true; this.onDisconnect.emit(this); }
      };
      ports.push(port);
      return port;
    },
    sendMessage(message, sender = { id: 'test-extension', url: 'chrome-extension://test-extension/popup.html' }) {
      calls.messages.push(message);
      return new Promise(resolve => {
        const keepAlive = runtime.onMessage.emit(message, sender, resolve);
        if (!keepAlive.includes(true)) resolve(undefined);
      });
    }
  };
  fake.api = {
    runtime,
    storage: { local: {
      async get() { if (fake.getHook) await fake.getHook(); return { ...data }; },
      async set(value) { calls.storage.push(structuredClone(value)); if (fake.setHook) await fake.setHook(value); Object.assign(data, value); }
    } },
    permissions: {
      onRemoved: event(), onAdded: event(),
      async contains(value) { calls.contains.push(value); return fake.containsHook ? fake.containsHook() : fake.permission; },
      request(value) { calls.request.push(value); fake.onRequest?.(); return Promise.resolve(fake.requestGranted); },
      async remove(value) { calls.remove.push(value); if (fake.removeHook) await fake.removeHook(); fake.permission = false; fake.api.permissions.onRemoved.emit(value); return true; }
    },
    cookies: { async getAll(value) { calls.cookies.push(value); return fake.cookieHook ? fake.cookieHook() : fake.cookieResult; } },
    alarms: {
      onAlarm: event(),
      async get(name) { return alarms.get(name); },
      async clear(name) { return alarms.delete(name); },
      async create(name, options) { calls.alarms.push({ name, ...options }); if (fake.alarmHook) await fake.alarmHook(); alarms.set(name, { name, ...options }); }
    }
  };
  fake.fireAlarm = async (name = 'fabu-reconnect') => { alarms.delete(name); fake.api.alarms.onAlarm.emit({ name }); await settle(); };
  return fake;
}
