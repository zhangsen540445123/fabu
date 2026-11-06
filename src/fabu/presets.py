"""Immutable preset versions. Concrete processor parameter schemas are added by M04."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fabu.assets import AssetStore
from fabu.store import JobStore


class StepSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    enabled: bool = False
    scope: Literal["main", "full"] = "main"
    params: dict = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list)


class PresetDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    schema_version: int = Field(default=1, ge=1, le=1)
    steps: list[StepSpec] = Field(default_factory=list, max_length=50)
    output: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_steps(self):
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("Step IDs must be unique")
        return self


class PresetVersion(PresetDocument):
    id: str
    preset_id: str
    revision: int


class PresetStore:
    def __init__(self, store: JobStore, assets: AssetStore):
        self.store = store
        self.assets = assets

    def save_draft(self, preset_id: str | None, document: dict) -> PresetVersion:
        doc = PresetDocument.model_validate(document)
        asset_ids = {asset_id for step in doc.steps for asset_id in step.asset_ids}
        for asset_id in asset_ids:
            if not self.assets.verify(asset_id):
                raise ValueError("Referenced asset is missing or failed integrity verification")
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            if preset_id is None:
                preset_id = str(uuid4())
                db.execute("INSERT INTO presets VALUES (?,?,?)", (preset_id, doc.name, now))
            elif not db.execute("SELECT 1 FROM presets WHERE id=?", (preset_id,)).fetchone():
                raise KeyError("Preset does not exist")
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM preset_versions WHERE preset_id=?",
                (preset_id,),
            ).fetchone()[0]
            version = PresetVersion(
                **doc.model_dump(), id=str(uuid4()), preset_id=preset_id, revision=revision
            )
            db.execute(
                "INSERT INTO preset_versions VALUES (?,?,?,?,?)",
                (version.id, preset_id, revision, version.model_dump_json(), now),
            )
            for asset_id in asset_ids:
                # FK and transaction prevent concurrent deletion racing a new reference.
                db.execute(
                    "INSERT INTO asset_refs VALUES (?,?,?)",
                    (asset_id, "preset_version", version.id),
                )
        return version

    def get_version(self, version_id: str) -> PresetVersion:
        UUID(version_id)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT document_json FROM preset_versions WHERE id=?", (version_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Preset version does not exist")
        return PresetVersion.model_validate_json(row[0])
