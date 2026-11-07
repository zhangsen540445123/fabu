# Live Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track checkboxes and use failing tests first.

**Goal:** Connect the user-approved new-job workflow without replaying existing jobs.
**Architecture:** Extend the existing SQLite coordinator and official local CLI transport; separate user-job authorization, provider observations and platform verification.
**Tech Stack:** Python 3.12 / FastAPI / SQLite / React / official CLI 3.2.32.
**Spec:** docs/superpowers/specs/2026-11-08-live-workflow.md

## Global Constraints
No automatic old-job replay, no paid/cloud-publish fallback, no secrets in public state, fixed local channel, at most three per-target tasks, no false platform success or quota evidence.

## Review Focus
- stale runtime/import identity and lost-looking configuration: launcher agent test.
- partial provider acceptance followed by timeout: coordinator and queue tests.
- automatic restart causing duplicate upload/post: journal/claim/budget tests.
- malformed/short upload or ambiguous provider details: receipt/tracker tests.
- default profile missing/account stale or unsupported scheduling: API/UI tests.

### Task 1: Runtime identity and installation consistency
- [x] Root-cause current runtime config/store mismatch, add regression test, fix only launcher/runtime paths, verify signed live instance reads original data.

### Task 2: Job-authorized live execution
- [x] Write tests for new local policy, durable budget, bound permit, exact CLI identity, upload receipt and no fallback.
- [x] Add policy module and migration012, extend permit/gateway/coordinator/queue and auto handoff; preserve controlled-test verification.
- [x] Test three distinct provider accepts continue, unknown stops without retry, source dedup and old jobs untouched.

### Task 3: Provider tracking
- [x] Add provider_tracking module and migration011 with separate observations, finite retry windows and restart-safe polling.
- [x] Test synthetic canonical provider responses plus sanitized real shape; no promotion to verified platform evidence.

### Task 4: UI/default workflow integration
- [x] Add dynamic readiness endpoint and frontend states; enable new authorized automatic live jobs from saved defaults.
- [x] Preserve simulation, explain per-target quotas and platform quality status; test missing data/status failures.

### Task 5: Integrated verification
- [x] Run Python/frontend/static checks and synthetic end-to-end without external writes.
- [x] Review changes, build frontend, controlled restart only if idle, verify actual config/job state.
- [x] Record remaining real-world test and Bilibili-quality evidence honestly.


## Delivery evidence (2026-11-08)
- Full Python suite at the final integration checkpoint: 834 passed (388.58s); later pure progress-projection tests: 6 passed; final real-FFmpeg workflow test passed with provider-success projection while persisted platform status remained unverified.
- Frontend: 82 unit tests passed; production TypeScript/Vite build passed; existing + new browser suite 8 passed; Chrome extension 42 tests passed.
- Independent review found default-runner gate mismatch, cached remote metadata bypass, pre-start cancellation ambiguity and historic attempt undercount. Each reproduced with a failing regression and repaired.
- Main runtime restarted through authenticated ownership; signed identity confirms D:\auto-deploy and original physical MSIX state. API reports 1 PAUSED/frozen historical job, configured credentials, correct FFmpeg/Node, 3-account live defaults, readiness ready. All 3 historical provider observations read back success; none promoted to platform/quality proof and no historical job/attempt changed.
- Desktop Chrome visually checked: default preset v3, default real profile and automatic immediate publish selected. No new job or external write executed.
- Local machine profile is ignored by Git; backup made before state migration and helper reconfiguration. Source stays in main workspace.

## Remaining real acceptance
- [ ] User supplies an unpublished authorized YouTube URL and title for an explicit new three-platform run.
- [ ] Observe real download → processing → upload → three accepted submissions through the application, then independently verify platform video durations (especially Bilibili).
- Existing Bilibili 2-second root cause remains unresolved; neither offline tests nor provider success closes that issue. Native scheduling remains unavailable until separately validated.
