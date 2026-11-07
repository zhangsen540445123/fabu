"""Durable visual-preset consent; source identity is evidence, not part of reusable consent."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from fabu.assets import AssetRef, AssetStore
from fabu.media.compiler import semantic_fingerprint
from fabu.media.models import ENGINE_VERSION, PlanInvalid, compatibility_version, tool_compatibility
from fabu.media.preview import PREVIEW_MAX_AGE, PreviewManifest, verify_preview
from fabu.presets import PresetStore, PresetVersion
from fabu.store import JobStore


class PreviewApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    preview_id: str
    semantic_digest: str
    engine_version: str
    approved_at: datetime


class PreviewApprovalStore:
    def __init__(
        self, store: JobStore, presets: PresetStore, assets: AssetStore, *, current_tools=None
    ):
        self.current_tools = current_tools
        self.store = store
        self.presets = presets
        self.assets = assets

    def current_engine(self):
        return tool_compatibility(self.current_tools()) if self.current_tools else ENGINE_VERSION

    def _check_tools(self, plan):
        if self.current_tools and plan.tool_identity != self.current_tools():
            raise PlanInvalid("Preview media tool build is no longer current")

    def _key(self, preset: PresetVersion, assets: dict[str, AssetRef], engine: str) -> str:
        used = {a for step in preset.steps if step.enabled for a in step.asset_ids}
        if not used.issubset(assets):
            raise PlanInvalid("A required preset asset is missing")
        for identifier in used:
            if (
                not self.assets.verify(identifier)
                or self.assets.get(identifier) != assets[identifier]
            ):
                raise PlanInvalid("Preset asset no longer matches the preview")
        return semantic_fingerprint(preset, {a: assets[a].sha256 for a in sorted(used)}, engine)

    def validate_plan_binding(self, preset: PresetVersion, plan) -> None:
        """Validate immutable media binding without asserting or recording visual consent."""
        self._check_tools(plan)
        if plan.engine_version != ENGINE_VERSION or plan.preset_digest != self._key(
            preset, plan.asset_refs, compatibility_version(plan)
        ):
            raise PlanInvalid("Media plan is not for this preset or compatible engine")

    def record_preview(self, manifest: PreviewManifest, preset_version_id: str) -> None:
        verify_preview(manifest)
        preset = self.presets.get_version(preset_version_id)
        self.validate_plan_binding(preset, manifest.plan)
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT manifest_json FROM media_previews WHERE id=?", (manifest.id,)
            ).fetchone()
            if existing:
                if PreviewManifest.model_validate_json(existing[0]) != manifest:
                    raise PlanInvalid("Preview identity is already bound to other content")
                return
            db.execute(
                "INSERT INTO media_previews VALUES (?,?,?,?)",
                (
                    manifest.id,
                    preset.id,
                    manifest.model_dump_json(),
                    manifest.created_at.isoformat(),
                ),
            )

    def get_preview(self, identifier: str) -> PreviewManifest:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT manifest_json FROM media_previews WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise KeyError("Preview does not exist")
        return PreviewManifest.model_validate_json(row[0])

    def approve_preview(self, identifier: str, *, now: datetime | None = None) -> PreviewApproval:
        now = now or datetime.now(UTC)
        if now.utcoffset() is None:
            raise PlanInvalid("Preview confirmation requires a timezone-aware time")
        with self.store.connection() as db:
            existing = db.execute(
                "SELECT * FROM preview_approvals WHERE preview_id=?", (identifier,)
            ).fetchone()
            row = db.execute("SELECT * FROM media_previews WHERE id=?", (identifier,)).fetchone()
        if existing:
            return PreviewApproval.model_validate(dict(existing))
        if row is None:
            raise KeyError("Preview does not exist")
        manifest = PreviewManifest.model_validate_json(row["manifest_json"])
        age = now - manifest.created_at
        if age > PREVIEW_MAX_AGE or age < -timedelta(minutes=5):
            raise PlanInvalid("Preview expired; generate and inspect a fresh sample")
        verify_preview(manifest)
        preset = self.presets.get_version(row["preset_version_id"])
        self._check_tools(manifest.plan)
        key = self._key(preset, manifest.plan.asset_refs, compatibility_version(manifest.plan))
        if key != manifest.plan.preset_digest or manifest.plan.engine_version != ENGINE_VERSION:
            raise PlanInvalid("Preview is stale for this preset or engine")
        approval = PreviewApproval(
            id=str(uuid4()),
            preview_id=identifier,
            semantic_digest=key,
            engine_version=compatibility_version(manifest.plan),
            approved_at=now,
        )
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO preview_approvals VALUES (?,?,?,?,?)",
                (
                    approval.id,
                    identifier,
                    key,
                    compatibility_version(manifest.plan),
                    now.isoformat(),
                ),
            )
            stored = db.execute(
                "SELECT * FROM preview_approvals WHERE preview_id=?", (identifier,)
            ).fetchone()
        return PreviewApproval.model_validate(dict(stored))

    def approval_matches(
        self, preset: PresetVersion, assets: dict[str, AssetRef], engine_compat_version: str
    ) -> bool:
        try:
            if self.current_tools and engine_compat_version != self.current_engine():
                return False
            key = self._key(preset, assets, engine_compat_version)
        except (ValueError, KeyError, OSError):
            return False
        with self.store.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM preview_approvals WHERE semantic_digest=? AND engine_version=?",
                    (key, engine_compat_version),
                ).fetchone()
                is not None
            )
