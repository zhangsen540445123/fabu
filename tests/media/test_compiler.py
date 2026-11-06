import math
from uuid import uuid4

import pytest

from fabu.presets import PresetVersion, StepSpec


def preset(steps=None, output=None, name="fixture"):
    return PresetVersion(
        id=str(uuid4()),
        preset_id=str(uuid4()),
        revision=1,
        name=name,
        steps=steps or [],
        output=output or {"crf": 20},
    )


def step(kind, params=None, scope="main", assets=None, enabled=True, identifier=None):
    return StepSpec(
        id=identifier or kind,
        kind=kind,
        enabled=enabled,
        scope=scope,
        params=params or {},
        asset_ids=assets or [],
    )


def test_all_disabled_matches_baseline(tmp_path):
    from test_baseline import info_fixture

    from fabu.media.baseline import build_baseline_plan
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle

    info = info_fixture(tmp_path)
    base = build_baseline_plan(info)
    plan = compile_plan(InputBundle(source=info, assets={}), preset([step("flip", enabled=False)]))
    assert plan.output_profile == base.output_profile and plan.output_size == base.output_size
    assert plan.stages == base.stages and plan.expected_duration_ms == base.expected_duration_ms


def test_invalid_order_is_rejected_not_sorted():
    from fabu.media.registry import validate_steps

    steps = [
        step("text_layer", {"text": "hello", "font_id": "font"}, assets=["font"]),
        step("flip"),
    ]
    before = [s.id for s in steps]
    errors = validate_steps(steps)
    assert "invalid_order" in {e.code for e in errors}
    assert [s.id for s in steps] == before


@pytest.mark.parametrize(
    "bad",
    [
        step("shell", {"command": "rm"}),
        step("color", {"brightness": math.nan}),
        step("color", {"contrast": math.inf}),
        step("speed", {"rate": 3}),
        step("crop", {"x": 0.8, "y": 0, "width": 0.5, "height": 1}),
        step("flip", {"raw_filter": "movie=private"}),
        step("flip", scope="full"),
    ],
)
def test_unknown_step_or_nan_is_rejected(bad):
    from fabu.media.registry import validate_steps

    assert validate_steps([bad])


def test_valid_geometry_order_and_scope_constraints():
    from fabu.media.registry import validate_steps

    assert (
        validate_steps(
            [
                step("trim", {"start_ms": 1000, "end_ms": 5000}),
                step("speed", {"rate": 1.25}),
                step("flip"),
                step("crop", {"x": 0, "y": 0, "width": 0.8, "height": 1}),
                step("color", {"brightness": 0.1}),
            ]
        )
        == []
    )
    assert validate_steps(
        [step("speed", {"rate": 1.25}), step("trim", {"start_ms": 0, "end_ms": 3000})]
    )
    assert validate_steps([step("flip"), step("flip", identifier="flip-2")])
    assert validate_steps([step("image_layer", assets=["a"], scope="full"), step("color")])
    assert validate_steps([step("output"), step("output", identifier="out-2")])


def test_asset_references_and_text_data_are_explicit():
    from fabu.media.registry import validate_steps

    assert validate_steps([step("image_layer")])
    assert (
        validate_steps([step("text_layer", {"text": "中[文]; : '", "font_id": "a"}, assets=["a"])])
        == []
    )
    assert validate_steps([step("text_layer", {"text": "hello", "font_id": "other"}, assets=["a"])])


def test_digest_tracks_semantics_not_display_name(tmp_path):
    from test_baseline import info_fixture

    from fabu.media.compiler import compile_plan, semantic_fingerprint
    from fabu.media.models import InputBundle

    bundle = InputBundle(source=info_fixture(tmp_path), assets={})
    first = preset()
    renamed = first.model_copy(update={"name": "renamed"})
    assert compile_plan(bundle, first).digest == compile_plan(bundle, renamed).digest
    assert (
        compile_plan(bundle, first).digest
        != compile_plan(bundle, preset(output={"crf": 19})).digest
    )
    assert semantic_fingerprint(first, {}, "1") != semantic_fingerprint(first, {}, "2")
    assert semantic_fingerprint(first, {"a": "1" * 64}, "1") != semantic_fingerprint(
        first, {"a": "2" * 64}, "1"
    )


def test_enabled_unimplemented_module_never_silently_skips(tmp_path):
    from test_baseline import info_fixture

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle, PlanInvalid

    with pytest.raises(PlanInvalid, match="not yet available"):
        compile_plan(
            InputBundle(source=info_fixture(tmp_path), assets={}),
            preset([step("trim", {"start_ms": 0, "end_ms": 1000})]),
        )


def test_preset_save_rejects_unknown_raw_or_invalid_output(tmp_path):
    from fabu.assets import AssetStore
    from fabu.presets import PresetStore
    from fabu.store import JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    assets = AssetStore(tmp_path / "assets", store)
    presets = PresetStore(store, assets)
    for doc in [
        {"name": "bad", "steps": [step("shell").model_dump()], "output": {}},
        {"name": "bad", "steps": [], "output": {"codec": "anything", "crf": 20}},
    ]:
        with pytest.raises(ValueError):
            presets.save_draft(None, doc)
