# Automatic local preset processing implementation plan

> **For agentic workers:** Use superpowers:executing-plans for backend; scoped frontend delegation in parallel. Follow RED/GREEN and a final independent review.

**Goal:** Download integrity success goes directly into selected-preset processing, without a mandatory sample review.
**Architecture:** Remove render-only approval gate; split verified local output from approved publishing output. Optional preview remains usable.
**Tech Stack:** Existing Python/FastAPI/FFmpeg and React/TypeScript.
**Spec:** docs/superpowers/specs/2026-11-07-fabu-auto-preset-design.md

## Constraints
No fabricated approvals; keep immutable preset/asset/plan/tool/output checks, publish safety/capability/permit gates and source cache. No automatic bulk job resume.

1. [x] Backend RED/GREEN: unapproved preset renders, verified output can be downloaded, approved publishing output still gated; update controller/cache/tool-identity fixtures for READY instead of preview pause.
2. [x] Frontend RED/GREEN: automatic-flow copy, legacy Continue, optional preview on READY, never resume READY merely when reviewing sample. Update local E2E flow.
3. [x] Full regression, independent boundary review, fix necessary findings; preserve real source-download evidence in operations docs.
4. [x] Idle owned runtime restart, explicitly resume only the selected current local job, observe actual preset processing to validated result; never publish.

Actual acceptance: job d17cc547-9b14-4512-86d5-8d3a10f38a1e reached READY at 2026-11-07 15:04:30+08; verified at15:06:03. Source/preset unchanged;2560x1440/30fps/HEVC/AAC output,713.066667s,1247459987bytes. Local range endpoint206 before approval;preview approvals0,new previews since resume0,target attempts/publication runs0. One preexisting unapproved preview from13:55:16 retained. See operations acceptance and live-output-verified.json. No real publishing.
