# Preset-driven local processing without mandatory preview — 2026-11-07

User explicitly requested: after download integrity verification, proceed directly to video processing using the selected task preset; do not require sample confirmation. This supersedes the original local-processing preview gate. User delegated implementation choices; no additional approach interview is needed.

## Scope
Remove the mandatory preview approval gate before local rendering. Preserve source/video identity, immutable asset hashes, preset-version selection, plan validation/binding, tool fingerprint checks, output validation and cancellation/checkpoint behavior. Do not fabricate preview records or approval rows.

Separate verified local-output access from publication authorization: local output download requires current READY state and valid immutable output/plan/assets/engine/tools, but not a preview-approval record. Existing publishing-specific preview/capability/permit/quota gates are unchanged in this focused change; no real upload/post is authorized. Optional samples remain available for local quality review; reviewing one must not restart an already READY output.

Legacy PAUSED/preview_required jobs get a normal Continue action. No bulk auto-resume or schema migration. Resume only this user's explicitly authorized job d17cc547-9b14-4512-86d5-8d3a10f38a1e after idle runtime update and verifying source/preset unchanged. Preserve other tasks.

UI copy must describe download -> integrity check -> preset processing; no claim that new presets must be sample-approved for local rendering. Optional preview UI works on PAUSED and READY snapshots, approval optional and never forged. Publishing-specific requirements must not be silently removed.

## Verification
Tests must show an unapproved preset directly renders to READY; local output download succeeds before optional sample review; corruption/incompatible plan/tools remain rejected; source/output cache reuse survives resume; publication remains gated. Update UI tests and isolated real-FFmpeg E2E to exercise automatic local rendering followed by optional preview. Actual current task should reuse the verified downloaded source, process its selected preset, and produce validated output without adding preview approval rows.
