import hashlib
from uuid import uuid4

import pytest
from PIL import Image
from test_api_security import BASE, login
from test_publish_api import prepare_api


def test_cross_site_cannot_import_or_publish(publish_app, publishing_context, tmp_path):
    client, headers = login(publish_app)
    image = tmp_path / "valid.jpg"
    Image.new("RGB", (10, 10), "red").save(image)
    run = prepare_api(client, headers, publishing_context)
    before = len(client.get("/api/assets").json())
    for altered in [
        {**headers, "Origin": "https://evil.invalid"},
        {**headers, "Sec-Fetch-Site": "cross-site"},
        {"Origin": BASE},
    ]:
        response = client.post(
            "/api/assets", json={"path": str(image), "media_type": "image"}, headers=altered
        )
        assert response.status_code == 403
        response = client.post(
            f"/api/jobs/{run['job_id']}/submit",
            json={"snapshot_digest": run["snapshot_digest"]},
            headers=altered,
        )
        assert response.status_code == 403
    assert len(client.get("/api/assets").json()) == before
    assert not publishing_context[0].attempts(run["job_id"])


def test_media_id_cannot_read_credentials(publish_app, publishing_context):
    from fabu.credentials import Credentials

    client, _ = login(publish_app)
    vault = publish_app.state.credentials
    vault.save(
        Credentials(api_key="SYNTHETIC_PRIVATE_KEY", local_client_id="SYNTHETIC_PRIVATE_CLIENT")
    )
    path = vault.path
    identifier = str(uuid4())
    # Corrupt a stored media row to point at private non-media: defense must also reject
    # database path substitution, not merely a ../ string in the request URL.
    with publishing_context[0].transaction() as db:
        row = db.execute("SELECT * FROM assets LIMIT 1").fetchone()
        columns = list(row.keys())
        values = dict(row)
        values.update(
            id=identifier,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            local_path=str(path),
            size_bytes=path.stat().st_size,
        )
        db.execute(
            "INSERT INTO assets ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(values[k] for k in columns),
        )
    response = client.get(f"/api/assets/{identifier}/file")
    assert response.status_code == 404
    assert response.content != path.read_bytes()
    assert client.get("/api/assets/..%2fcredentials%2fcredentials.dpapi/file").status_code == 404


def test_secret_not_in_logs_or_db(publish_app, publishing_context, caplog):
    import threading

    from fabu.credentials import Credentials
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.worker import Worker

    client, headers = login(publish_app)
    key = "SYNTHETIC_KEY_security_only"
    private_url = "https://storage.invalid/signed?token=SYNTHETIC_TEMP_UPLOAD_SECRET"
    publish_app.state.credentials.save(
        Credentials(api_key=key, local_client_id="SYNTHETIC_CLIENT_security_only")
    )
    run = prepare_api(client, headers, publishing_context)

    class Failing(FakePublisher):
        def submit(self, prepared, permit, target_id):
            raise ValueError(key + private_url)

    queue = publish_app.state.publication_queue
    queue.publisher_factory = lambda authority, cancel: Failing(authority)
    queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "submit")
    with Worker(publishing_context[0]) as worker:
        queue.process_next(worker, threading.Event(), lambda **state: None)
    output = client.get(f"/api/jobs/{run['job_id']}/targets").text
    with publishing_context[0].connection() as db:
        dump = "\n".join(db.iterdump())
    for secret in [
        key,
        private_url,
        "SYNTHETIC_TEMP_UPLOAD_SECRET",
        "SYNTHETIC_CLIENT_security_only",
    ]:
        assert secret not in caplog.text and secret not in dump and secret not in output
    assert "UNKNOWN" in output


@pytest.mark.parametrize(
    "endpoint", ["/api/publish/preflight", "/api/jobs/fake/submit", "/api/publishing/connection"]
)
def test_write_authentication_cannot_be_bypassed_with_query_flags(publish_app, endpoint):
    from fastapi.testclient import TestClient

    client = TestClient(publish_app, base_url=BASE)
    response = client.post(endpoint + "?live=true&approved=true", json={}, headers={"Origin": BASE})
    assert response.status_code == 401
