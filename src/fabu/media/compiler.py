"""Pure compilation; unsupported enabled operations are rejected, never silently skipped."""

import hashlib
import json

from fabu.media.baseline import build_baseline_plan
from fabu.media.models import (
    ENGINE_VERSION,
    InputBundle,
    MediaPlan,
    OutputProfile,
    PlanInvalid,
    plan_digest,
)
from fabu.media.registry import validate_steps
from fabu.presets import PresetVersion


def semantic_fingerprint(
    preset: PresetVersion, asset_hashes: dict[str, str], engine_version: str
) -> str:
    data = {
        "steps": [s.model_dump(mode="json", exclude={"id"}) for s in preset.steps if s.enabled],
        "output": OutputProfile.model_validate(preset.output).model_dump(mode="json"),
        "assets": asset_hashes,
        "engine": engine_version,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def compile_plan(bundle: InputBundle, preset: PresetVersion) -> MediaPlan:
    errors = validate_steps(preset.steps)
    if errors:
        raise PlanInvalid("; ".join(f"{e.step_id}: {e.code}" for e in errors))
    profile = OutputProfile.model_validate(preset.output)
    active = [step for step in preset.steps if step.enabled]
    used_assets = {key for step in active for key in step.asset_ids}
    if not used_assets.issubset(bundle.assets):
        raise PlanInvalid("A required immutable asset is missing")
    if active:
        raise PlanInvalid(
            "Enabled editing modules are not yet available in this implementation stage"
        )
    plan = build_baseline_plan(bundle.source)
    asset_hashes = {key: bundle.assets[key].sha256 for key in sorted(used_assets)}
    plan = plan.model_copy(
        update={
            "output_profile": profile,
            "asset_hashes": asset_hashes,
            "preset_digest": semantic_fingerprint(preset, asset_hashes, ENGINE_VERSION),
        }
    )
    return plan.model_copy(update={"digest": plan_digest(plan)})
