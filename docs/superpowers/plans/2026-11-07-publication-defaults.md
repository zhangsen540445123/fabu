# Default publishing intent and automatic guarded handoff

Date: 2026-11-07. Base: 3dd639f. Implementation: inline TDD in existing isolated worktree.

## Contract
New UI-created tasks default to the user's explicitly configured live profile: the current three stable account IDs, title-based topic selection, self_created + confirmed source declaration, immediate mode. Defaults are opt-in persisted configuration, not a hardcoded ownership claim. No future accounts are silently added. Existing jobs, source declarations, approvals, outputs, attempts and schedules must remain unchanged. Manual local-only and simulation workflows remain available.

Processing completion automatically enters local publication preflight exactly once for live profiles. It verifies frozen output and current account/topic metadata, then reports that real execution is not enabled. This release MUST NOT enqueue a live upload/post, fabricate preview approval, weaken quota/capability/authorization checks, or retry uncertain attempts. Actual posting remains a separate acceptance task.

## Files and tasks
1. `src/fabu/migrations/010_publication_defaults.sql`, `src/fabu/publishing/defaults.py`, `src/fabu/api/publishing.py`: singleton pointer to immutable default profile with explicit declaration provenance; authenticated GET/PUT defaults; job-specific initial form projection with saved-draft/profile precedence. Tests: `tests/test_publication_defaults.py` (defaults, persistence, exact IDs, validation, unchanged historical state, draft preservation).
2. `src/fabu/publishing/profiles.py`: permit live workflow construction only as preflight-only; check output then persist a blocked handoff without submission. Keep existing simulation execution/resume semantics. Tests: same plus existing `tests/test_workflow.py` (once-only live transition, media tamper, unmatched topic, no attempts/commands, local READY untouched).
3. `frontend/src/api.ts`, `frontend/src/pages/NewJobPage.tsx`, `frontend/src/components/PublishForm.tsx`: load server defaults and job intent before form interaction; choose default profile for new jobs; hydrate saved declarations, selections and schedule; explicit save-as-default action; show automatic preflight/blocker and never imply platform publication. Tests: frontend publication and new-job suites. Fail closed on loading errors.
4. Verify Python/full frontend/build/E2E and independent final review. Add operations notes. Deploy only if owned runtime is idle; configure default IDs via authenticated API, verify existing job remains unchanged and no live effects. Commit locally, no push.

## Decisions / ledger
- User's six requested defaults are explicit configuration authorization; they do not authorize posting an existing video as a development test.
- Save defaults via a dedicated PUT, allowing configuration without an approved/output-bearing job. Read-only GET never creates profiles or changes declarations.
- Live `can_run` means workflow can reach preflight, not platform submission; add `execution=preflight_only` in public profiles.
- Saved job configurations take precedence over default profile. UI source changes clear confirmation. Unknown/mixed declarations must not be silently normalized.
- Existing publication visual-approval and live capability gates are not removed in this defaults task.

## Verification ledger
- Defaults API RED (missing endpoint) -> GREEN; UI five default/refresh/handoff tests RED -> GREEN.
- Regression RED -> GREEN: manual task metadata edit detaches old profile; absent targets remain visible/removable and cannot silently disappear; changed content revisions reload saved declarations.
- Independent reviewer P2: live handoff failures were not recoverable. Reproduced RED, fixed by retaining READY media plus separately paused preflight and guarded scope-aware retry; interruption and restored-account tests GREEN. Reviewer rechecked: no issues.
- Final Python: 662 passed in 284.24s. Frontend: 47 passed. Build passed. Ruff check/format passed (125 files). E2E: 6 passed, including real FFmpeg synthetic-media live-default handoff (no external writes).
- E2E inspection found duplicate topic text and test snapshot taken during an earlier render. Removed duplicate display; now waits for natural completion before comparing historical state.
- Deployed 2026-11-07 16:54:53 +08:00: old runtime29548 shut down cleanly, new runtime31344 ready. No forced termination.
- Saved explicit live default profile3dc6e711-b566-4ef1-aae6-e4c8bc7dc622 with the 3 observed active stable IDs, automatic topics, self_created+confirmed, immediate mode. This is user-configured intent, not copyright inference or a transport permit.
- Existing job/presets/approval/drafts unchanged; original output SHA256 unchanged and Range206 verified. All upload attempts, target attempts, publication runs and commands remain0. Existing READY local job was not resumed/rebound.
- Evidence (ignored local): `.superpowers/sdd/2026-11-07-publication-defaults/` contains test logs, backup, deployment report. Real upload/post remains disabled pending separate capability/free-quota/controlled acceptance; this feature completes automatic entry to preflight, not platform publication.
