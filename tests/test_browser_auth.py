import io
import json
import struct
import threading
import time

import pytest

VIDEO = "VbDUeWoESMY"
ORIGIN = "chrome-extension://" + "a" * 32 + "/"


def cookie(**changes):
    return {
        "name": "SID",
        "value": "synthetic-youtube-session",
        "domain": ".youtube.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "hostOnly": False,
        "session": True,
        **changes,
    }


def test_cookie_scope_serialization_and_expiry():
    from fabu.browser_auth.cookies import serialize_cookies

    text = serialize_cookies(
        [cookie(), cookie(name="expired", session=False, expirationDate=5)], now=10
    )
    assert text.startswith("# Netscape HTTP Cookie File\n")
    assert "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsynthetic-youtube-session" in text
    assert "expired" not in text
    assert "www.youtube.com\tFALSE" in serialize_cookies(
        [cookie(domain="www.youtube.com", hostOnly=True)]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"domain": ".google.com"},
        {"domain": "youtube.com.evil.test"},
        {"domain": ".evilyoutube.com"},
        {"domain": ""},
        {"value": "secret\nINJECT"},
        {"name": "a\tb"},
        {"path": "https://evil.test"},
        {"secure": "false"},
        {"hostOnly": 1},
        {"session": False},
        {"expirationDate": float("nan"), "session": False},
        {"partitionKey": {"topLevelSite": "x"}},
    ],
)
def test_cookie_invalid_input_fails_without_secret_in_error(change):
    from fabu.browser_auth.cookies import BrowserSessionError, serialize_cookies

    with pytest.raises(BrowserSessionError) as raised:
        serialize_cookies([cookie(**change)])
    assert "synthetic" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_cookie_empty_and_limits():
    from fabu.browser_auth.cookies import BrowserSessionError, serialize_cookies

    for rows in (
        [],
        [cookie()] * 257,
        [cookie(value="x" * 8193)],
        [cookie(session=False, expirationDate=1)],
    ):
        with pytest.raises(BrowserSessionError):
            serialize_cookies(rows)


def test_native_framing_roundtrip_and_partial_reads():
    from fabu.browser_auth.protocol import read_message, write_message

    stream = io.BytesIO()
    write_message(stream, {"type": "status", "message": "中文"})
    stream.seek(0)

    class Partial:
        def read(self, n):
            return stream.read(min(n, 2))

    assert read_message(Partial()) == {"type": "status", "message": "中文"}
    assert read_message(stream) is None


@pytest.mark.parametrize(
    "raw",
    [
        b"x",
        struct.pack("<I", 262145),
        struct.pack("<I", 0),
        struct.pack("<I", 2) + b"xx",
        struct.pack("<I", 2) + b"[]",
        struct.pack("<I", 4) + b"{}",
    ],
)
def test_native_framing_rejects_malformed(raw):
    from fabu.browser_auth.cookies import BrowserSessionError
    from fabu.browser_auth.protocol import read_message

    with pytest.raises(BrowserSessionError):
        read_message(io.BytesIO(raw))


def test_native_framing_rejects_duplicate_keys():
    from fabu.browser_auth.cookies import BrowserSessionError
    from fabu.browser_auth.protocol import read_message

    body = b'{"type":"a","type":"b"}'
    with pytest.raises(BrowserSessionError):
        read_message(io.BytesIO(struct.pack("<I", len(body)) + body))


def answer_once(host, errors, *, wrong_video=False):
    try:
        until = time.monotonic() + 3
        while time.monotonic() < until:
            requests = host.pending_requests()
            if requests:
                r = requests[0]
                host.accept_response(
                    {
                        **r,
                        "type": "cookie-response",
                        "cookies": [cookie()],
                        "videoId": "AAAAAAAAAAA" if wrong_video else r["videoId"],
                    }
                )
                return
            time.sleep(0.01)
        raise AssertionError("No pending synthetic request")
    except Exception as exc:
        errors.append(exc)


def test_session_roundtrip_protects_and_cleans_files(tmp_path):
    from fabu.browser_auth.bridge import BrowserSessionClient, HostSession

    root = tmp_path / "auth"
    client = BrowserSessionClient(root)
    assert client.status()["connected"] is False
    assert not root.exists()
    errors = []
    with HostSession(root, ORIGIN, ORIGIN) as host:
        assert client.status()["connected"] is True
        worker = threading.Thread(target=answer_once, args=(host, errors))
        worker.start()
        with client.cookie_file(VIDEO, threading.Event(), timeout_s=2) as path:
            assert b"synthetic-youtube-session" in path.read_bytes()
            for file in root.iterdir():
                if file.is_file() and file != path and file.name != "host.lock":
                    assert b"synthetic-youtube-session" not in file.read_bytes()
            import win32security

            sd = win32security.GetNamedSecurityInfo(
                str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
            )
            acl = sd.GetSecurityDescriptorDacl()
            assert acl.GetAceCount() == 2
        worker.join()
        assert not errors
        assert not path.exists()
        assert not list(root.glob("request-*.json"))
        assert not list(root.glob("response-*.dpapi"))
    assert client.status()["connected"] is False


def test_session_rejects_wrong_origin_and_duplicate_host(tmp_path):
    from fabu.browser_auth.bridge import HostSession
    from fabu.browser_auth.cookies import BrowserSessionError

    with pytest.raises(BrowserSessionError):
        with HostSession(tmp_path / "auth", "chrome-extension://" + "b" * 32 + "/", ORIGIN):
            pass
    assert not (tmp_path / "auth").exists()
    with HostSession(tmp_path / "auth", ORIGIN, ORIGIN):
        with pytest.raises(BrowserSessionError):
            with HostSession(tmp_path / "auth", ORIGIN, ORIGIN):
                pass


def test_session_timeout_cancel_and_bad_response_cleanup(tmp_path):
    from fabu.browser_auth.bridge import BrowserSessionClient, HostSession
    from fabu.browser_auth.cookies import BrowserSessionError

    root = tmp_path / "auth"
    client = BrowserSessionClient(root)
    with HostSession(root, ORIGIN, ORIGIN) as host:
        with pytest.raises(BrowserSessionError, match="timeout"):
            with client.cookie_file(VIDEO, threading.Event(), timeout_s=0.1):
                pass
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(BrowserSessionError, match="canceled"):
            with client.cookie_file(VIDEO, cancel):
                pass
        errors = []
        worker = threading.Thread(
            target=answer_once, args=(host, errors), kwargs={"wrong_video": True}
        )
        worker.start()
        with pytest.raises(BrowserSessionError):
            with client.cookie_file(VIDEO, threading.Event(), timeout_s=0.3):
                pass
        worker.join()
        assert errors and isinstance(errors[0], BrowserSessionError)
        assert not list(root.glob("request-*.json"))
        assert not list(root.glob("response-*.dpapi"))
        assert not list(root.glob("cookies-*.txt"))


def test_host_ignores_unbound_expired_and_traversal_requests(tmp_path):
    from fabu.browser_auth.bridge import HostSession

    root = tmp_path / "auth"
    with HostSession(root, ORIGIN, ORIGIN) as host:
        now = time.time()
        for index, changes in enumerate(
            (
                {"expiresAt": now - 1},
                {"requestId": "../evil"},
                {"videoId": "bad"},
                {"instanceId": "b" * 32},
            )
        ):
            rid = f"{index:032x}"
            value = {
                "version": 1,
                "requestId": rid,
                "videoId": VIDEO,
                "createdAt": now,
                "expiresAt": now + 20,
                "instanceId": host.instance_id,
                **changes,
            }
            (root / f"request-{rid}.json").write_text(json.dumps(value))
        assert host.pending_requests() == []


def test_native_host_eof_and_invalid_origin_never_reads_cookies(tmp_path):
    from fabu.browser_auth.native_host import run_host
    from fabu.browser_auth.protocol import read_message

    output = io.BytesIO()
    assert run_host(tmp_path / "host", ORIGIN, ORIGIN, io.BytesIO(), output) == 0
    output.seek(0)
    assert read_message(output) == {"type": "status", "version": 1, "status": "connected"}
    assert not (tmp_path / "host" / "connected.json").exists()
    assert run_host(tmp_path / "denied", "wrong-origin", ORIGIN, io.BytesIO(), io.BytesIO()) == 1
    assert not (tmp_path / "denied").exists()


def test_native_host_real_subprocess_roundtrip(tmp_path):
    import os
    import subprocess
    import sys

    from fabu.browser_auth.bridge import BrowserSessionClient
    from fabu.browser_auth.protocol import read_message, write_message

    config = tmp_path / "native-config.json"
    root = tmp_path / "auth"
    config.write_text(json.dumps({"version": 1, "root": str(root), "allowed_origin": ORIGIN}))
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "fabu.browser_auth.native_host",
            "--config",
            str(config),
            ORIGIN,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    errors = []

    def reply():
        try:
            assert read_message(process.stdout)["status"] == "connected"
            req = read_message(process.stdout)
            write_message(process.stdin, {**req, "type": "cookie-response", "cookies": [cookie()]})
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=reply, daemon=True)
    worker.start()
    try:
        client = BrowserSessionClient(root)
        until = time.monotonic() + 5
        while not client.status()["connected"] and time.monotonic() < until:
            time.sleep(0.02)
        with client.cookie_file(VIDEO, threading.Event(), timeout_s=3) as path:
            assert "synthetic-youtube-session" in path.read_text()
        worker.join(3)
        assert not worker.is_alive() and not errors
        process.stdin.close()
        assert process.wait(timeout=5) == 0
        assert process.stderr.read() == b""
        assert not client.status()["connected"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_orphaned_plaintext_cleanup_is_scoped_to_dead_process_lease(tmp_path):
    import os

    import psutil

    from fabu.browser_auth.bridge import HostSession

    root = tmp_path / "auth"
    root.mkdir()
    dead = "d" * 32
    active = "c" * 32
    me = psutil.Process(os.getpid())
    for rid, created in ((dead, me.create_time() - 1000), (active, me.create_time())):
        (root / f"lease-{rid}.json").write_text(json.dumps({"pid": me.pid, "created": created}))
        (root / f"cookies-{rid}.txt").write_text("synthetic-orphan")
        (root / f"response-{rid}.dpapi").write_bytes(b"ciphertext")
    sentinel = root / "user-note.txt"
    sentinel.write_text("keep")
    with HostSession(root, ORIGIN, ORIGIN):
        assert not (root / f"cookies-{dead}.txt").exists()
        assert not (root / f"response-{dead}.dpapi").exists()
        assert not (root / f"lease-{dead}.json").exists()
        assert (root / f"cookies-{active}.txt").exists()
        assert sentinel.read_text() == "keep"


def test_cookie_file_is_removed_when_downloader_raises(tmp_path):
    from fabu.browser_auth.bridge import BrowserSessionClient, HostSession

    root = tmp_path / "auth"
    with HostSession(root, ORIGIN, ORIGIN) as host:
        errors = []
        worker = threading.Thread(target=answer_once, args=(host, errors))
        worker.start()
        with pytest.raises(RuntimeError, match="synthetic-child-error"):
            with BrowserSessionClient(root).cookie_file(VIDEO, threading.Event()) as path:
                raise RuntimeError("synthetic-child-error")
        worker.join(2)
        assert not errors and not path.exists()
        assert not list(root.glob("lease-*.json"))


def test_native_error_type_is_validated_without_unhashable_crash(tmp_path):
    from fabu.browser_auth.bridge import HostSession
    from fabu.browser_auth.cookies import BrowserSessionError

    with HostSession(tmp_path / "auth", ORIGIN, ORIGIN) as host:
        now = time.time()
        rid = "a" * 32
        req = {
            "version": 1,
            "requestId": rid,
            "videoId": VIDEO,
            "createdAt": now,
            "expiresAt": now + 20,
            "instanceId": host.instance_id,
        }
        (host.root / f"request-{rid}.json").write_text(json.dumps(req))
        message = host.pending_requests()[0]
        with pytest.raises(BrowserSessionError):
            host.accept_response({**message, "type": "cookie-response", "error": []})


def test_connected_host_periodically_reaps_dead_client_but_preserves_live_download(tmp_path):
    import os

    import psutil

    from fabu.browser_auth.bridge import HostSession

    root = tmp_path / "auth"
    with HostSession(root, ORIGIN, ORIGIN) as host:
        me = psutil.Process(os.getpid())
        for rid, created in (("d" * 32, me.create_time() - 100), ("a" * 32, me.create_time())):
            (root / f"lease-{rid}.json").write_text(json.dumps({"pid": me.pid, "created": created}))
            (root / f"cookies-{rid}.txt").write_text("synthetic-cookie")
        host.heartbeat()
        assert not (root / f"cookies-{'d' * 32}.txt").exists()
        assert (root / f"cookies-{'a' * 32}.txt").exists()


def test_cancel_cleanup_removes_a_response_racing_with_request_unlink(tmp_path, monkeypatch):
    from pathlib import Path

    from fabu.browser_auth.bridge import BrowserSessionClient, HostSession
    from fabu.browser_auth.cookies import BrowserSessionError

    root = tmp_path / "auth"
    cancel = threading.Event()
    monkeypatch.setattr(cancel, "wait", lambda _: True)
    original_unlink = Path.unlink
    injected = []
    with HostSession(root, ORIGIN, ORIGIN) as host:

        def interleaved_unlink(path, *args, **kwargs):
            if path.name.startswith("request-") and not injected:
                request = host.pending_requests()[0]
                injected.append(request["requestId"])
                host.accept_response({**request, "type": "cookie-response", "cookies": [cookie()]})
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", interleaved_unlink)
        with pytest.raises(BrowserSessionError, match="canceled"):
            with BrowserSessionClient(root).cookie_file(VIDEO, cancel):
                pass
        assert injected
        assert not list(root.glob("response-*.dpapi"))
        assert not list(root.glob("request-*.json"))
        assert not list(root.glob("lease-*.json"))


def test_old_unleased_encrypted_reply_is_reaped_without_touching_live_reply(tmp_path):
    import os

    from fabu.browser_auth.bridge import HostSession

    root = tmp_path / "auth"
    with HostSession(root, ORIGIN, ORIGIN) as host:
        old = root / f"response-{'b' * 32}.dpapi"
        old.write_bytes(b"synthetic-ciphertext")
        fresh = root / f"response-{'c' * 32}.dpapi"
        fresh.write_bytes(b"synthetic-ciphertext")
        malformed = root / "response-user-file.dpapi"
        malformed.write_bytes(b"keep")
        os.utime(old, (time.time() - 60, time.time() - 60))
        host.heartbeat()
        assert not old.exists() and fresh.exists() and malformed.exists()
