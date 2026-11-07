# Fabu YouTube session extension

Stable extension ID: `kodlebnlbllemoihjlkchlldpaollgll`.
Native host: `com.fabu.youtube_session`. Configure its allowed origin as
`chrome-extension://kodlebnlbllemoihjlkchlldpaollgll/`. The manifest contains a fixed RSA public key;
the generating private key was discarded in memory and never saved. Do not regenerate the key.

## Runtime package (top-level files only)

Copy these seven files together, retaining their names:

- `manifest.json`
- `service-worker.mjs`
- `worker.mjs`
- `protocol.mjs`
- `popup.html`
- `popup.mjs`
- `popup.css`

No build step, dependencies, generated bundles, icons, or package manifest are needed.
`README.md` and `tests/` are development-only and need not be installed.

## User-owned setup

Fabu's installer registers the native host separately. This directory does not install
anything, edit browser profiles, or touch the registry. Chrome 120+ is the declared minimum.
When the user installs this unpacked extension, its first-install event opens its own
control page (the same page used by the toolbar popup). Updates do not reopen it.
Opening that page does not enable access or request permission.

Click **Enable** to request only `https://*.youtube.com/*` from a user gesture.
If approved, the worker persists enabled state and keeps one native port open.
The host's exact v1 `connected` status updates the UI; **Refresh status** reads only
connection state, never cookies. The extension must be installed in the intended,
YouTube-signed-in profile. This does not bypass YouTube restrictions or promise a download.

**Disable** immediately invalidates pending work and disconnects, then clears the retry
alarm, persists disabled state, and removes YouTube permission. External permission
revocation also disables access. Granting permission later does not reconnect by itself:
the user must explicitly enable again. If storage or permission APIs fail, the UI reports
unavailability rather than exposing their error text.

## Protocol and bounds

- Only the current native port can request cookies. Exact v1 request envelopes require
  a 32-character lowercase hexadecimal request ID and an 11-character YouTube video ID.
- The sole read is `chrome.cookies.getAll({domain: 'youtube.com'})`, after consent is
  checked. Consent and connection generation are checked again after asynchronous work.
- Only unpartitioned YouTube-domain cookies are returned. Partitioned, hostile-domain,
  and expired cookies are dropped. Malformed fields fail closed with `read_failed`.
- Replies preserve the IDs and contain either `cookies` or a fixed protocol error:
  `permission_required`, `no_cookies`, or `read_failed`. No raw exceptions are forwarded.
- At most 256 input cookies and 262144 UTF-8 bytes per response; name/value/path limits
  are 256/8192/2048 characters and domain limit is 253. Cookie fields are printable ASCII;
  names use the native host's token grammar; expiration is finite and at most 253402300799.
- One read at a time. A bounded in-memory set remembers the last 128 accepted IDs per
  connection. Results completing after 25 seconds are errors, never cookie-value replies.
  The host owns the end-to-end request timeout; an outstanding Chrome API call cannot be aborted.
- Native connection failures retry by one-shot alarm after 1, 2, 4, 8, 16, then 30 minutes
  (30-minute cap). Enabled state and retry delay persist; startup respects an existing
  alarm. Successful host acknowledgement resets backoff. No timers or HTTP polling.
- Storage contains only `enabled` and `retryDelay`. The UI receives only enabled/status;
  no cookies, request/video IDs, browser-supplied errors, or logs. No remote requests,
  content scripts, broad hosts, tabs/history/debug/downloads permissions, or browser database access.

## Tests and review

Use the Node built-in runner from the repository root (fake Chrome APIs and synthetic
cookies only; no dependencies, real profiles, browser launches, network, or downloads):

```powershell
& 'D:\devtools\nodejs\node.exe' --test 'extensions/chrome-youtube/tests/protocol.test.mjs' 'extensions/chrome-youtube/tests/worker.test.mjs' 'extensions/chrome-youtube/tests/popup.test.mjs'
```

Observed TDD checkpoints:

1. `--test extensions/chrome-youtube/tests/protocol.test.mjs`: **RED 7 failures**
   for missing manifest/protocol, then **GREEN 7/7**.
2. `--test extensions/chrome-youtube/tests/worker.test.mjs extensions/chrome-youtube/tests/popup.test.mjs`:
   **RED 23 failures** for missing worker/UI, then full suite **GREEN 30/30**.
3. Onboarding manifest/open-page assertions: **RED 2 failures (38/40 passing)**.
   The focused `own onboarding page can control consent when Chrome supplies a tab sender`
   test independently failed **RED 1/1**; after implementation, **GREEN 41/41**.
4. Focused `cookie fields match the native host ASCII/token and expiration bounds`:
   **RED 1/1**, then full suite **GREEN 42/42**.

Final review: self-review (no subagent tool). Checked consent races, stale ports/reads,
protocol bounds and the Python host's cookie contract read-only. No independent review
or real Chrome/native-host end-to-end validation is claimed.

Implementation decisions: one in-flight read and capped replay memory bound resource use
(concurrent requests receive a sanitized error); the same exact internal page is accepted
whether popup- or tab-backed so onboarding controls work without broader sender access.
Invalid non-ASCII/token cookies fail closed to match the native host (unusual cookie sets
can therefore produce a sanitized read failure rather than credentials).
