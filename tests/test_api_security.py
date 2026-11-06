import pytest
from fastapi.testclient import TestClient

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def app(tmp_path):
    from fabu.api.app import create_app
    from fabu.settings import load_settings
    from fabu.store import JobStore

    settings = load_settings(
        {"LOCALAPPDATA": str(tmp_path), "FABU_DATA_DIR": str(tmp_path / "media")}
    )
    return create_app(settings, JobStore(settings.db_path))


def login(app):
    client = TestClient(app, base_url=BASE)
    token = app.state.security.bootstrap_token
    response = client.post(
        "/api/session", headers={"Origin": BASE, "Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    return client, {"Origin": BASE, "X-CSRF-Token": csrf}


def payload(app):
    return {
        "source_url": "https://youtu.be/L6imSiJLd9Q",
        "title": "剃须仙人的日常",
        "preset_version_id": app.state.default_preset.id,
        "target_ids": [],
        "publish_mode": "local",
    }


def test_health_public_and_sensitive_gets_require_session(app):
    client = TestClient(app, base_url=BASE)
    assert client.get("/api/health").json() == {"status": "ok", "publishing_enabled": False}
    for path in ("/api/jobs", "/api/settings", "/api/capabilities", "/api/session"):
        assert client.get(path).status_code == 401


def test_cross_origin_write_denied(app):
    client, headers = login(app)
    headers["Origin"] = "https://malicious.invalid"
    assert client.post("/api/jobs", json=payload(app), headers=headers).status_code == 403
    headers.pop("Origin")
    assert client.post("/api/jobs", json=payload(app), headers=headers).status_code == 403


def test_csrf_required_even_with_valid_cookie(app):
    client, _ = login(app)
    assert client.post("/api/jobs", json=payload(app), headers={"Origin": BASE}).status_code == 403


def test_bootstrap_single_use_and_cookie_flags(app):
    client = TestClient(app, base_url=BASE)
    token = app.state.security.bootstrap_token
    headers = {"Origin": BASE, "Authorization": f"Bearer {token}"}
    response = client.post("/api/session", headers=headers)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/session", headers=headers).status_code == 401
    assert token not in response.text


def test_dns_rebinding_host_denied(app):
    client = TestClient(app, base_url="http://evil.invalid:8765")
    assert client.get("/api/health").status_code == 403


def test_jobs_create_read_cancel_and_persist(app):
    from fabu.store import JobStore

    client, headers = login(app)
    response = client.post("/api/jobs", json=payload(app), headers=headers)
    assert response.status_code == 201
    job = response.json()
    assert job["state"] == "NEW"
    assert client.get("/api/jobs").json()[0]["id"] == job["id"]
    assert client.get(f"/api/jobs/{job['id']}").json()["spec"]["title"] == "剃须仙人的日常"
    assert JobStore(app.state.store.path).get(job["id"]).state == "NEW"
    canceled = client.post(
        f"/api/jobs/{job['id']}/cancel",
        headers=headers,
        json={"expected_revision": job["revision"]},
    )
    assert canceled.status_code == 200 and canceled.json()["state"] == "CANCELED"


def test_real_publication_mode_rejected_at_api(app):
    client, headers = login(app)
    response = client.post(
        "/api/jobs", json={**payload(app), "publish_mode": "immediate"}, headers=headers
    )
    assert response.status_code == 409
    assert client.get("/api/jobs").json() == []


def test_media_path_not_arbitrary_file_read(app):
    client, _ = login(app)
    response = client.get("/api/media", params={"path": "C:/Users/77465/private/auth.json"})
    assert response.status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 404


def test_secrets_never_returned(app):
    client, headers = login(app)
    response = client.put(
        "/api/settings", json={"api_key": "synthetic-secret-xyz"}, headers=headers
    )
    assert response.status_code == 422
    assert "synthetic-secret-xyz" not in response.text
    assert "bootstrap" not in client.get("/api/settings").text
    assert "csrf" not in client.get("/api/settings").text


def test_settings_reject_public_bind_and_show_configured_only(app):
    client, headers = login(app)
    data = client.get("/api/settings").json()
    assert data["credentials"] == {"configured": False}
    response = client.put("/api/settings", json={"bind_host": "0.0.0.0"}, headers=headers)
    assert response.status_code == 422


def test_capabilities_are_unknown_and_not_auto_queried(app):
    client, _ = login(app)
    data = client.get("/api/capabilities").json()
    assert data["upload"] == "UNKNOWN"
    assert data["native_schedule"] == "UNKNOWN"


def test_invalid_source_and_missing_preset_rejected(app):
    client, headers = login(app)
    for changes in [{"source_url": "http://localhost/secrets"}, {"preset_version_id": "missing"}]:
        response = client.post("/api/jobs", json={**payload(app), **changes}, headers=headers)
        assert response.status_code in (400, 422)
    assert client.get("/api/jobs").json() == []


def test_settings_changes_persist_without_leaking_config(app):
    client, headers = login(app)
    response = client.put("/api/settings", json={"test_mode": "local"}, headers=headers)
    assert response.status_code == 200
    assert client.get("/api/settings").json()["test_mode"] == "local"
    assert (app.state.settings.state_dir / "ui-settings.json").is_file()


def test_malformed_port_does_not_become_server_error(app):
    client, headers = login(app)
    response = client.post(
        "/api/jobs",
        json={**payload(app), "source_url": "https://youtu.be:invalid/video"},
        headers=headers,
    )
    assert response.status_code == 422


def test_settings_concurrent_updates_remain_valid(app):
    import json
    from concurrent.futures import ThreadPoolExecutor

    client, headers = login(app)

    def update(index):
        return client.put(
            "/api/settings",
            json={"test_mode": "local" if index % 2 else "simulation"},
            headers=headers,
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(update, range(8))) == [200] * 8
    data = json.loads((app.state.settings.state_dir / "ui-settings.json").read_text())
    assert data["test_mode"] in ("local", "simulation")
