# CPU/GPU implementation plan — 2026-11-07

Approved proposal: ../specs/2026-11-07-fabu-cpu-gpu-optimization-proposal.md. User said 可以实施. Execute inline with TDD, one final independent review, no repeated approval interview. Existing named worktree is reused; preserve all prior output/presets and original checkout changes.

## Preflight evidence and scope ruling

Idle production job is READY. One bounded 3-frame synthetic 1440p HEVC NVENC probe failed before encoding: installed FFmpeg requires NVENC API13.1, driver exposes12.0. No upgrade or alternate binary is authorized. Implement software support and fail-closed reporting; actual GPU throughput/quality acceptance remains blocked by driver compatibility. Do not treat mocked GPU tests as hardware acceptance. GPU-fast automatic rollout must remain disabled until equivalence/quality is validated; implement conservative compatibility gating and report that state, not an unverified fast default. Full-output scan removal is deferred until equivalent validator coverage is proven; implement phase timing first to measure this safely.

## Interfaces / file ownership

- models.py: optional acceleration intent on OutputProfile and optional frozen ExecutionProfile on MediaPlan; legacy missing fields must preserve prior semantic/plan hashes. New execution has schema_version1, path cpu/mixed/cuda, codec, device ID/fingerprint and fixed encoder quality configuration. Keep legacy preset/output accessible.
- encoders.py (new): pure encoder_args(plan), bind_execution(plan, report, previous=None). No shell, I/O or mid-render fallback. CPU uses existing args; NVENC uses independent VBR/CQ configuration (initial candidate p5/CQ20, not claimed equivalent to CRF20). GPU mode requires successful probe; auto records fallback reason.
- hardware.py (new): HardwareProbe.check(tools,cancel), cached_report(tools); scoped Windows GPU inventory fingerprint and bounded synthetic encode/metadata/decode validation. Only worker processing triggers tests; GET is read-only. Cache keyed by device/driver/tool hashes and probe schema, bounded lifetime, cancel never cached as healthy. Raw external errors never become UI text.
- service.py/bootstrap.py: resolve execution before frozen checkpoint, preserve old execution on retry, detect environment change rather than quietly replan. Report stages. No change to source downloader/publishing.
- controller.py/renderer.py/validator.py: stage callbacks + elapsed times; keep full validation. Output manifest stores timings separately from immutable processing plan.
- api/media.py + frontend api/JobsPage/PipelinePage: read-only hardware status, persisted execution/phase timing, acceleration intent selector; existing presets default CPU, new presets can select auto. No forced preview gate.
- tests/media/test_gpu*.py + API/frontend tests: schema, commands, legacy hashes, probe failures/cancellation/cache, source/output guards, auto fallback and forced-GPU pause, frozen retry and UI persistence.

## Tasks

1. [x] RED/GREEN models + encoder strategies. Legacy digests byte-identical with optional fields absent; reject CPU/GPU parameter mixing and unknown modes. Commands never include x265 flags for NVENC. Render unresolved acceleration plans must fail closed.
2. [x] RED/GREEN hardware detection. Bounded probes; verify generated output is HEVC 1440p30 with successful decode; fixed reason codes driver_incompatible/no_device/encoder_unavailable/probe_failed/canceled. Positive/negative cache expiry, changed tool/device invalidation, no GET probe and no stale positive on cancellation.
3. [x] RED/GREEN worker integration and telemetry. Auto CPU fallback records exact reason, strict GPU stops before render; old CPU plans and READY output stay valid. Device changes cannot alter frozen task. Stages input_validation/encoding/output_validation reflect real boundaries, metrics persist without entering plan digest.
4. [x] RED/GREEN UI + API. Acceleration mode saves immutable preset version. Actual path/fallback reason visible; do not claim GPU active based on hardware name. Separate 99% render progress from full validation. Old API fixtures remain compatible.
5. [x] Full backend, lint/format, frontend unit/build/E2E. Final independent safety/compatibility review; fix necessary findings with failing tests. Test any preserved historical READY output before and after idle deployment.
6. [x] Actual-machine recheck using production probe (expect truthful driver incompatibility), isolated short-source auto CPU fallback acceptance, no GPU performance claim. Record driver blocker and next exact acceptance action. Local checkpoint commit only; no driver install, real posting, push or merge.

## Commands

Set FABU_TEST_FFMPEG and FABU_TEST_FFPROBE to the existing Fabu-comparison tools. `python -m pytest -q`, `python -m ruff check src tests frontend/tests/serve_fixture.py`, `python -m ruff format --check src tests frontend/tests/serve_fixture.py`; frontend npm test -- --run, npm run build, npm run test:e2e.

## Review focus

1. New default fields invalidating old output/approval hashes.
2. Auto resolution changing frozen plan on resume or serving an invalid GPU plan after hardware changes.
3. Compiler listing or failed/partial probe accepted as working GPU.
4. Cache identity/expiry/cancel and GET unexpectedly launching work.
5. Hiding CPU fallback as GPU acceleration or removing complete validation for speed.

Final verification:647Python passed292.74s,37frontend passed/build,5isolatedE2E passed38.7s,Ruff123clean. ReviewerP2 malformedFraction0/0 corrected withtwoRED→GREEN regressions andfresh fullsuite;P3bounded10secondinventorycancellationlatency deferred. Actual machine driverincompatible verified;3s sourceexcerpt autoCPUfallback READY andproductiontaskunchanged. Deployment acceptance follows beforeTask6closure.

Task6 operational acceptance passed16:02:06: deployedPID29548, existingtask/presetsunchanged,originaloutputRange206+bytecheck and serverhash verified, existingapproval1preserved, publishcounts0,no resumedtasks/driverupdates. Oldidleowner4040 required exactidentity+closedport/nochild/idleDB termination after graceful45second timeout; cause not fixed. This closes only this scoped incremental delivery; GPU performance/quality,CUDAfast and validation-passreduction remain external-validation/deferred scope, as documented.
