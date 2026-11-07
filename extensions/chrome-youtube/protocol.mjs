export const HOST = 'com.fabu.youtube_session';
export const ORIGINS = ['https://*.youtube.com/*'];
export const MAX_COOKIES = 256;
export const MAX_BYTES = 262144;

const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const boundedText = (value, limit) => typeof value === 'string' && value.length <= limit && !/[^\x20-\x7e]/u.test(value);

export function isCookieRequest(message) {
  return record(message) && Object.keys(message).length === 4 &&
    message.type === 'cookie-request' && message.version === 1 &&
    typeof message.requestId === 'string' && message.requestId.length === 32 && /^[a-f0-9]+$/.test(message.requestId) &&
    typeof message.videoId === 'string' && message.videoId.length === 11 && /^[A-Za-z0-9_-]+$/.test(message.videoId);
}

export function errorResponse(request, error) {
  return { type: 'cookie-response', version: 1, requestId: request.requestId, videoId: request.videoId, error };
}

function youtubeDomain(domain) {
  if (typeof domain !== 'string' || domain.length > 253) return false;
  const host = domain.startsWith('.') ? domain.slice(1) : domain;
  return (host === 'youtube.com' || host.endsWith('.youtube.com')) &&
    host.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
}

export function cookieResponse(request, source, now = Date.now()) {
  const failure = () => errorResponse(request, 'read_failed');
  if (!Array.isArray(source) || source.length > MAX_COOKIES) return failure();
  const cookies = [];
  for (const cookie of source) {
    if (!record(cookie)) return failure();
    // Never flatten partitioned cookies or export non-YouTube credentials.
    if ('partitionKey' in cookie || cookie.partitioned === true || !youtubeDomain(cookie.domain)) {
      if (typeof cookie.domain !== 'string') return failure();
      continue;
    }
    if (!boundedText(cookie.name, 256) || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(cookie.name) ||
        !boundedText(cookie.value, 8192) || !boundedText(cookie.path, 2048) || !cookie.path.startsWith('/') ||
        !['secure', 'httpOnly', 'hostOnly', 'session'].every(key => typeof cookie[key] === 'boolean') ||
        (cookie.hostOnly && cookie.domain.startsWith('.')) ||
        ('expirationDate' in cookie && (typeof cookie.expirationDate !== 'number' || !Number.isFinite(cookie.expirationDate) || cookie.expirationDate < 0 || cookie.expirationDate > 253402300799)) ||
        (!cookie.session && !('expirationDate' in cookie))) return failure();
    if ('expirationDate' in cookie && cookie.expirationDate <= now / 1000) continue;
    const clean = {};
    for (const key of ['name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'hostOnly', 'session']) clean[key] = cookie[key];
    if ('expirationDate' in cookie) clean.expirationDate = cookie.expirationDate;
    cookies.push(clean);
  }
  if (!cookies.length) return errorResponse(request, 'no_cookies');
  const response = { type: 'cookie-response', version: 1, requestId: request.requestId, videoId: request.videoId, cookies };
  return new TextEncoder().encode(JSON.stringify(response)).byteLength <= MAX_BYTES ? response : failure();
}
