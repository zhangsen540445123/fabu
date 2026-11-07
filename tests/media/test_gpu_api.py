from types import SimpleNamespace

import pytest


@pytest.mark.media
def test_hardware_get_is_readonly_and_plan_exposes_actual_execution(context, tmp_path, media_tools):
    from test_api_security import login

    from fabu.api.app import create_app
    from fabu.settings import Settings

    service, store, _, _, _, job, _ = context
    out = service.process_job(store.claim_next("test"))
    app = create_app(
        Settings(
            state_dir=tmp_path / "state",
            data_dir=tmp_path,
            tools_dir=media_tools.ffmpeg_path.parent,
        ),
        store,
    )

    def forbidden(*a, **kw):
        raise AssertionError("GET must not start a benchmark")

    app.state.hardware = SimpleNamespace(
        cached_report=lambda tools: {
            "status": "checked",
            "stale": False,
            "report": {"available": False, "reason": "driver_incompatible"},
        },
        check=forbidden,
    )
    client, _ = login(app)
    response = client.get("/api/media/hardware")
    assert response.status_code == 200
    assert response.json()["report"]["reason"] == "driver_incompatible"
    plan = client.get(f"/api/jobs/{job.id}/plan").json()
    assert plan["execution"] is None and plan["actual_encoder"] == "libx265"
    assert plan["timings"]["encoding"] == out.timings["encoding"]


def test_controller_reports_phase_elapsed_without_changing_job(tmp_path):
    from fabu.media.controller import MediaController
    from fabu.store import JobStore

    controller = MediaController(
        JobStore(tmp_path / "state.db"), lambda _: None, tmp_path / "previews"
    )
    controller._set(phase="output_validation")
    assert controller.status()["phase"] == "output_validation"
    assert controller.status()["phase_elapsed_s"] >= 0
    controller._set(phase=None)
    assert controller.status()["phase_elapsed_s"] is None
