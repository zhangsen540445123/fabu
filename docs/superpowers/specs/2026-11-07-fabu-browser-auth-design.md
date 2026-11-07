# Fabu Chrome YouTube session design — 2026-11-07

The user delegated the technical decision and explicitly asked to solve downloads without further approach questions. This is a focused extension to the existing downloader, not a redesign of publishing.

## Evidence and decision
The existing installed yt-dlp/EJS/Node path still returns login verification for VbDUeWoESMY (anonymous metadata-only probe, no video/auth reads). Direct Chrome database extraction has Windows compatibility and overly broad credential-access drawbacks. Manual cookie export is rejected by the user. Choose a small local MV3 extension using the official cookies API and Native Messaging. No paid service, remote debugger, browser-security downgrade, profile database reads, or automatic GUI fallback.

## Behavior and boundaries
Anonymous downloading remains first choice. Only an explicit authentication failure enables a single browser-auth attempt. After authenticated metadata succeeds, subsequent download commands for that same video request a fresh session before running. Never retry authentication in a loop. Chrome must be running with the extension enabled; otherwise pause with an actionable message. Existing paused tasks are not automatically resumed; uploads/publications remain untouched.

The extension starts disabled. The user installs it in the Chrome profile already logged into YouTube and enables the YouTube-only optional host permission. That final browser confirmation is user-owned. The development implementation and local host registration can be prepared by Fabu. Do not modify Chrome profile files or force-install the extension.

Only youtube.com and its subdomains are allowed. No passwords, Google-wide cookies, history, tabs, or arbitrary URL reads. Unpartitioned cookies only (do not flatten partitioned cookies). Cookies are fetched only for a pending, bounded download request, not continuously exported. Native Messaging accepts only the installed extension's exact origin. The extension has no HTTP credential-sending endpoint; only the native channel.

## Components
- `extensions/chrome-youtube/`: reviewable MV3 extension, optional YouTube permission, enable/disable popup and local connection status. Stable manifest public key; no private key needed.
- `src/fabu/browser_auth/`: validated cookie serialization, bounded native protocol, DPAPI-encrypted response transport in ACL-restricted local storage, read-only connection status and isolated setup CLI.
- `YtDlpRunner`: optional browser provider. Keep ignore-config, no-plugin-dirs, no-remote-components, no-cookies-from-browser; replace only no-cookies during an explicitly scoped authenticated subprocess. Cookie temp file removed in finally; raw stdout/stderr from authenticated subprocess not exposed in diagnostics.
- Bootstrap wires provider at settings.state_dir/browser-auth. Frontend explains browser connection/login required. No publishing modifications.

## Local protocol (v1)
Native host name `com.fabu.youtube_session`. Extension connects only when enabled. Host authenticates the origin argument against installed configuration. Native JSON is little-endian uint32-length-prefixed, max 262144 bytes, rejects malformed JSON/protocol.
Host -> extension: `{type:"cookie-request", version:1, requestId:<32 lowercase hex>, videoId:<11 valid characters>}`.
Extension -> host: `{type:"cookie-response", version:1, requestId, videoId, cookies:[...]}` OR same envelope with `error:"permission_required"|"no_cookies"|"read_failed"`.
Cookie shape: name,value,domain,path,secure,httpOnly,hostOnly,session,expirationDate(optional finite number). Reject arbitrary domains, injection characters, malformed types or oversized collections. Expired cookies dropped. No cookie values in status/errors/logs.
Host may send `{type:"status",version:1,status:"connected"}`. Unknown types ignored/disconnected, never executed. Extension reconnect uses bounded/alarm-driven backoff, not a hot loop.

Native host publishes a short-lived heartbeat. Fabu creates random request JSON bound to video ID/time; host validates it and emits a request. Valid matching response is DPAPI encrypted and stored atomically, expires within 30 seconds, consumed and deleted by the requester. Requests, responses, heartbeat and transient Netscape files remain under a current-user/SYSTEM-only directory with reparse checks. The client cancels/cleans up on timeout, cancellation, error and success. Native host exits on stdin EOF; only one host per state root owns heartbeat. No indefinitely cached cookie jar.

## Acceptance
Unit tests for framing, host scope, expiry, injection, ACL/temp cleanup, request binding, cancellation and bounded retries. Extension tests use fake Chrome APIs only. Native subprocess integration uses synthetic cookies, no Chrome credentials. Real download is not claimed until extension installation/enabling and actual media/probe validation succeed. Authenticated access still may encounter YouTube restrictions/PO-token requirements; report exact sanitized category rather than promise success.
