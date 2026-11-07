# Fabu Chrome YouTube session implementation plan

> **For agentic workers:** Use superpowers:executing-plans for the core, with one bounded parallel extension implementation and a final independent review. Follow TDD.

**Goal:** Replace manual cookie export with explicit YouTube-only Chrome session access for local downloads.
**Architecture:** Opt-in MV3 extension -> native host -> short-lived DPAPI response -> scoped yt-dlp subprocess. Anonymous first; no publishing changes.
**Tech Stack:** Python 3.12/pywin32, existing yt-dlp, Chrome MV3, native Node test runner.
**Spec:** docs/superpowers/specs/2026-11-07-fabu-browser-auth-design.md

## Global constraints
- No direct Chrome database access/security downgrade, paid services, credential logs or external credential services.
- Current-user/SYSTEM ACLs, exact extension origin, YouTube-only domains, finite requests and temporary files.
- Installation/enabling remains a browser confirmation; preserve live application and paused task until explicitly resumed.

## Tasks
1. [x] Core session transport: tests first for cookies, framing, DPAPI request-response matching, expiry/cancel cleanup; implement browser_auth package. Run focused pytest.
2. [x] Parallel extension: manifest, popup, worker and node tests in extensions/chrome-youtube only; no profile/registry/browser operations. Validate restricted permissions and fake-cookie protocol.
3. [x] Downloader + setup integration: failing tests, optional provider, bounded authentication retry and sanitized errors, service bootstrap, frontend copy. Setup packages local host/extension without force-installing browser changes. Test generated manifests/paths.
4. [x] Verification: native subprocess fixture, backend regressions, extension tests, frontend build/tests, independent review; document install and truthful real-world blocker. Prepare local package, no uploads/posts and no background job auto-resume.

## Review focus
Credential isolation, hostile cookie/request input, origin and request identity, cleanup/expiry, keeping anonymous and publishing behavior unchanged.

## Remaining external acceptance
- [ ] User completes Chrome extension installation/enabling, then actual authorized source download and media validation. This is not implied by local tests or successful host registration.
