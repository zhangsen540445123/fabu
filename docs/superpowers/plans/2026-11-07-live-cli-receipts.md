# Real CLI receipt integration plan

Date: 2026-11-07. Base: 282e433. Existing isolated worktree, inline implementation.

## Scope
Continue user-authorized real integration preparation; no external upload/post without specific confirmation. Zero spending, no automatic purchase, local publish route only. Preserve current user-configured task (now revision27/content3), two existing live preflight runs, output and approvals.

## Findings
Authenticated CLI account queries succeed; desktop reports connected/free plan with counters0/3, cloud0B/0B, material13MB/500MB. A historical85% upload is left untouched. Public docs differentiate temporary cloud-publish uploads from material-library registration. CLI upload dry-run only returns a request description and cannot verify upload/file quota.

## Steps
- [x] Run current task's saved live configuration through installed CLI validate + dry-run for all3 targets using a cloned DB and deliberately invalid/offline credentials. Confirm exact payload comparison, no network writes.
- [x] RED: installed official CLI against loopback-only synthetic gateway returns canonical action=publish,data.taskSetId without channel echoes; current Fabu wrongly labels it channel_mismatch.
- [x] GREEN: accept this as PROVIDER_ACCEPTED only. Missing echoed channel stays unknown (actual_channel=null); explicit cloud/wrong client is rejected. Preserve returned task ID on uncertain outcomes. Never infer SUBMITTED/PUBLISHED or permit retries from provider acknowledgement.
- [x] Tests: unknown action/version, invalid task ID, timeout, channel mismatch, response without channel, metadata preservation and no duplicate attempts. Keep current live execution gates intact.
- [x] Run complete tests, final independent review, document concrete blockers and prepare exact scoped next-step confirmation. No claim of end-to-end publication without real accepted resources/platform evidence.

## Remaining external blockers
Temporary upload limits, free-publishing counter semantics/failed attempts, temporary resource lifetime are not exposed in the reviewed CLI read-only commands. Public free CLI access and desktop0/3 are not substitutes. No fabricated SafetyEvidence will be entered. These need authoritative clarification before the existing controlled-test gate can be issued. Actual source declaration is taken from the user's saved live draft, never derived from processing.


## Review/verification ledger
- Installed CLI loopback and canonical acknowledgement regression both failed before patch; corrected state is PROVIDER_ACCEPTED with actual_channel=null.
- Independent reviewer found normalized nested response could hide explicit channel/client contradiction. Reproduced 8 failures (including 2 actual installed-CLI loopback object-response cases); fixed conservatively and all52 focused tests passed. Reviewer re-review: no new findings.
- Initial full suite676 passed333.03s. Frontend47 passed/build good. Six E2E passed. Post-review full suite684 passed290.80s; Ruff127 files clean.
- Runtime remains PID31344; do not restart just to deploy a currently gated acknowledgement parser. No production data mutations by this turn; read-only comparison matches the offline clone, including user's newer draft and2 existing runs.
- Next user decision: permit a maximum of1 temporary upload of the exact174432-byte cover for this job (hash documented in operations record), no video, no publish, no purchases/paid route, no automatic retry. Must implement/use a distinct bounded upload-only probe, not fabricate full production SafetyEvidence.
