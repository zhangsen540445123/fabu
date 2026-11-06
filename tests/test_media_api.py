from PIL import Image
from test_api_security import login, payload


def test_base_preset_preserves_audio_and_frame(app):
    client, _ = login(app)
    response = client.get("/api/presets")
    assert response.status_code == 200
    first = response.json()[0]
    assert first["version"]["steps"] == []
    assert first["version"]["output"]["crf"] == 20
    assert first["approved"] is False
    catalog = client.get("/api/presets/catalog").json()
    assert {"trim", "text_layer", "intro", "subtitles"}.issubset({item["kind"] for item in catalog})
    assert all("raw_filter" not in item["parameters"]["properties"] for item in catalog)


def test_preset_versions_immutable_and_approved_cannot_be_forged(app):
    client, headers = login(app)
    doc = {"name": "我的预设", "steps": [], "output": {}}
    assert (
        client.post("/api/presets", json={**doc, "approved": True}, headers=headers).status_code
        == 422
    )
    saved = client.post("/api/presets", json=doc, headers=headers)
    assert saved.status_code == 201
    first = saved.json()
    second = client.post(
        f"/api/presets/{first['preset_id']}/versions",
        json={**doc, "output": {"crf": 19}},
        headers=headers,
    )
    assert second.status_code == 201 and second.json()["revision"] == 2
    versions = client.get(f"/api/presets/{first['preset_id']}/versions").json()
    assert versions[1]["id"] == first["id"] and versions[1]["output"] == {}
    assert (
        client.post(
            "/api/previews/fake/approve", json={"approved": True}, headers=headers
        ).status_code
        == 422
    )


def test_validation_returns_backend_order_issues_without_sorting(app):
    client, headers = login(app)
    steps = [
        {
            "id": "layout",
            "kind": "layout",
            "enabled": True,
            "params": {},
            "scope": "main",
            "asset_ids": [],
        },
        {
            "id": "crop",
            "kind": "crop",
            "enabled": True,
            "params": {"width": 0.9, "height": 0.9},
            "scope": "main",
            "asset_ids": [],
        },
    ]
    doc = {"name": "wrong order", "steps": steps, "output": {}}
    result = client.post("/api/presets/validate", json=doc, headers=headers)
    assert result.status_code == 200 and result.json()["valid"] is False
    assert any(
        issue["step_id"] == "crop" and issue["code"] == "invalid_order"
        for issue in result.json()["issues"]
    )
    saved = client.post("/api/presets", json=doc, headers=headers)
    assert saved.status_code == 422
    assert saved.json()["detail"]["issues"] == result.json()["issues"]
    assert [s["id"] for s in steps] == ["layout", "crop"]


def test_asset_import_and_id_bound_read(app, tmp_path):
    client, headers = login(app)
    image = tmp_path / "示例.png"
    Image.new("RGB", (100, 100), "red").save(image)
    response = client.post(
        "/api/assets", json={"path": str(image), "media_type": "image"}, headers=headers
    )
    assert response.status_code == 201
    ref = response.json()
    assert ref["display_name"] == "示例.png"
    rows = client.get("/api/assets").json()
    assert rows[0]["id"] == ref["id"] and rows[0]["display_name"] == "示例.png"
    content = client.get(f"/api/assets/{ref['id']}/file")
    assert content.status_code == 200 and content.headers["content-type"] == "image/png"
    assert content.content == image.read_bytes()
    assert client.get("/api/assets/not-a-uuid/file").status_code == 404
    assert client.get("/api/media", params={"path": str(image)}).status_code == 404


def test_topics_do_not_select_media_preset_and_playlist_rejected(app):
    client, headers = login(app)
    for title in ["剃光头", "剃须仙人的日常", "沉浸式剪发"]:
        job = client.post("/api/jobs", json={**payload(app), "title": title}, headers=headers)
        assert job.status_code == 201
        assert job.json()["spec"]["preset_version_id"] == app.state.default_preset.id
    assert (
        client.post(
            "/api/jobs",
            json={**payload(app), "source_url": "https://www.youtube.com/playlist?list=bad"},
            headers=headers,
        ).status_code
        == 422
    )


def test_unready_job_cannot_request_preview_or_read_output(app):
    client, headers = login(app)
    job = client.post("/api/jobs", json=payload(app), headers=headers).json()
    result = client.post(
        "/api/previews",
        json={"job_id": job["id"], "expected_revision": job["revision"]},
        headers=headers,
    )
    assert result.status_code == 409
    assert client.get(f"/api/jobs/{job['id']}/output").status_code == 409
    assert client.get("/api/media/status").json()["publishing_enabled"] is False


def test_paused_task_can_choose_new_preset_as_new_content_revision(app):
    client, headers = login(app)
    job = client.post("/api/jobs", json=payload(app), headers=headers).json()
    changed = client.post(
        "/api/presets", json={"name": "new", "steps": [], "output": {"crf": 19}}, headers=headers
    ).json()
    response = client.post(
        f"/api/jobs/{job['id']}/revise",
        headers=headers,
        json={
            "expected_revision": job["revision"],
            "spec": {**job["spec"], "preset_version_id": changed["id"]},
        },
    )
    assert response.status_code == 200
    assert response.json()["content_revision"] == 2
    assert response.json()["spec"]["preset_version_id"] == changed["id"]
    assert response.json()["output_manifest"] is None
