"""Short-lived, ACL-restricted, DPAPI-protected per-download session rendezvous."""

import json
import msvcrt
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psutil
import pywintypes
import win32crypt

from fabu.assets import assert_no_reparse
from fabu.browser_auth.cookies import (
    MAX_MESSAGE,
    ORIGIN,
    REQUEST_ID,
    VIDEO_ID,
    BrowserSessionError,
    finite_number,
    serialize_cookies,
)
from fabu.browser_auth.protocol import decode_message
from fabu.credentials import secure_directory

_ERROR_CODES = {"permission_required", "no_cookies", "read_failed"}


def _read(path, limit=4096):
    assert_no_reparse(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise BrowserSessionError("browser_protocol_invalid")
    return data


def _atomic(path, data):
    assert_no_reparse(path)
    temp = path.parent / f"write-{uuid4().hex}.tmp"
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _json(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _request_valid(value, instance_id, now):
    if type(value) is not dict or set(value) != {
        "version",
        "requestId",
        "videoId",
        "instanceId",
        "createdAt",
        "expiresAt",
    }:
        return False
    return (
        type(value["version"]) is int
        and value["version"] == 1
        and type(value["requestId"]) is str
        and bool(REQUEST_ID.fullmatch(value["requestId"]))
        and type(value["videoId"]) is str
        and bool(VIDEO_ID.fullmatch(value["videoId"]))
        and value["instanceId"] == instance_id
        and finite_number(value["createdAt"])
        and finite_number(value["expiresAt"])
        and now - 30 <= value["createdAt"] <= now + 2
        and now < value["expiresAt"] <= value["createdAt"] + 30
    )


class BrowserSessionClient:
    """Construction/status are read-only; no session is read without a pending video request."""

    def __init__(self, root: Path):
        if not root.is_absolute():
            raise ValueError("Browser session storage must be absolute")
        assert_no_reparse(root)
        self.root = root

    def status(self):
        try:
            value = decode_message(_read(self.root / "connected.json"))
            stamp = value.get("updatedAt")
            instance = value.get("instanceId")
            connected = (
                type(value.get("version")) is int
                and value["version"] == 1
                and finite_number(stamp)
                and 0 <= time.time() - stamp < 6
                and type(instance) is str
                and bool(REQUEST_ID.fullmatch(instance))
            )
            if connected:
                return {"connected": True, "instanceId": instance}
        except (OSError, ValueError):
            pass
        return {"connected": False}

    @contextmanager
    def cookie_file(self, video_id, cancel, *, timeout_s=20):
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            raise BrowserSessionError("browser_request_invalid")
        if not finite_number(timeout_s) or not 0 < timeout_s <= 25:
            raise BrowserSessionError("browser_request_invalid")
        if cancel.is_set():
            raise BrowserSessionError("browser_request_canceled")
        status = self.status()
        if not status["connected"]:
            raise BrowserSessionError("browser_not_connected")
        secure_directory(self.root)
        request_id = uuid4().hex
        now = time.time()
        request = {
            "version": 1,
            "requestId": request_id,
            "videoId": video_id,
            "instanceId": status["instanceId"],
            "createdAt": now,
            "expiresAt": now + timeout_s,
        }
        request_path = self.root / f"request-{request_id}.json"
        response_path = self.root / f"response-{request_id}.dpapi"
        cookie_path = self.root / f"cookies-{request_id}.txt"
        lease_path = self.root / f"lease-{request_id}.json"
        try:
            _atomic(
                lease_path, _json({"pid": os.getpid(), "created": psutil.Process().create_time()})
            )
            _atomic(request_path, _json(request))
            deadline = time.monotonic() + timeout_s
            while not response_path.exists():
                if cancel.wait(min(0.05, max(0, deadline - time.monotonic()))):
                    raise BrowserSessionError("browser_request_canceled")
                if time.monotonic() >= deadline:
                    raise BrowserSessionError("browser_request_timeout")
            if cancel.is_set():
                raise BrowserSessionError("browser_request_canceled")
            try:
                _, plaintext = win32crypt.CryptUnprotectData(
                    _read(response_path, MAX_MESSAGE + 4096), None, None, None, 1
                )
                response = decode_message(plaintext)
                if response.get("request") != request or not _request_valid(
                    request, status["instanceId"], time.time()
                ):
                    raise BrowserSessionError("browser_response_mismatch")
                if "error" in response:
                    raise BrowserSessionError("browser_session_unavailable")
                text = serialize_cookies(response.get("cookies"))
            except pywintypes.error:
                raise BrowserSessionError("browser_response_invalid") from None
            # Plaintext exists only for the child command and inherits the private directory ACL.
            assert_no_reparse(cookie_path)
            with cookie_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            yield cookie_path
        finally:
            # Invalidate first: a concurrent host response must observe cancellation,
            # or be deleted by the later response cleanup below.
            for path in (request_path, cookie_path, response_path, lease_path):
                assert_no_reparse(path)
                path.unlink(missing_ok=True)


class HostSession:
    """One enabled extension connection; responses must match a still-live issued request."""

    def __init__(self, root: Path, origin: str, allowed_origin: str):
        if not isinstance(origin, str) or not ORIGIN.fullmatch(origin) or origin != allowed_origin:
            raise BrowserSessionError("browser_origin_denied")
        if not root.is_absolute():
            raise BrowserSessionError("browser_storage_invalid")
        self.root = root
        self.instance_id = uuid4().hex
        self.pending = {}
        self._lock = None

    def __enter__(self):
        secure_directory(self.root)
        path = self.root / "host.lock"
        assert_no_reparse(path)
        stream = path.open("a+b")
        try:
            if not path.stat().st_size:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            raise BrowserSessionError("browser_host_already_connected") from None
        self._lock = stream
        try:
            self.heartbeat()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def _cleanup_dead_sessions(self):
        # Only a generated lease with a proven-dead PID incarnation authorizes cleanup.
        # Never age-delete an active long-running download's cookie file.
        for index, lease in enumerate(self.root.glob("lease-*.json")):
            if index >= 128:
                break
            request_id = lease.stem.removeprefix("lease-")
            if not REQUEST_ID.fullmatch(request_id):
                continue
            try:
                value = decode_message(_read(lease))
                if (
                    set(value) != {"pid", "created"}
                    or type(value["pid"]) is not int
                    or value["pid"] <= 0
                    or not finite_number(value["created"])
                ):
                    continue
                try:
                    if psutil.Process(value["pid"]).create_time() == value["created"]:
                        continue
                except psutil.NoSuchProcess:
                    pass
                for filename in (
                    f"request-{request_id}.json",
                    f"cookies-{request_id}.txt",
                    f"response-{request_id}.dpapi",
                    lease.name,
                ):
                    path = self.root / filename
                    assert_no_reparse(path)
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, psutil.AccessDenied):
                continue

    def _cleanup_orphan_replies(self):
        # A host can itself crash between writing a response and checking cancellation.
        # Only expired generated replies lacking BOTH request and lease are reclaimable.
        for index, path in enumerate(self.root.glob("response-*.dpapi")):
            if index >= 128:
                break
            request_id = path.stem.removeprefix("response-")
            if not REQUEST_ID.fullmatch(request_id):
                continue
            try:
                assert_no_reparse(path)
                if (
                    time.time() - path.stat().st_mtime > 30
                    and not (self.root / f"request-{request_id}.json").exists()
                    and not (self.root / f"lease-{request_id}.json").exists()
                ):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue

    def heartbeat(self):
        # The native process outlives individual Fabu workers. Reap on the bounded
        # two-second heartbeat, not merely at the next browser connection.
        self._cleanup_dead_sessions()
        self._cleanup_orphan_replies()
        _atomic(
            self.root / "connected.json",
            _json({"version": 1, "instanceId": self.instance_id, "updatedAt": time.time()}),
        )

    def pending_requests(self):
        now = time.time()
        self.pending = {
            key: value for key, value in self.pending.items() if value["expiresAt"] > now
        }
        messages = []
        for index, path in enumerate(self.root.glob("request-*.json")):
            if index >= 64:
                break
            try:
                request = decode_message(_read(path))
                if (
                    not _request_valid(request, self.instance_id, now)
                    or path.name != f"request-{request['requestId']}.json"
                    or request["requestId"] in self.pending
                ):
                    continue
                # Completed requests must not be re-issued while their caller is consuming them.
                if (self.root / f"response-{request['requestId']}.dpapi").exists():
                    continue
                self.pending[request["requestId"]] = request
                messages.append(
                    {
                        "type": "cookie-request",
                        "version": 1,
                        "requestId": request["requestId"],
                        "videoId": request["videoId"],
                    }
                )
            except (OSError, ValueError):
                continue
        return messages

    def accept_response(self, response):
        if type(response) is not dict:
            raise BrowserSessionError("browser_response_invalid")
        request_id = response.get("requestId")
        request = self.pending.get(request_id) if type(request_id) is str else None
        if (
            request is None
            or not _request_valid(request, self.instance_id, time.time())
            or response.get("type") != "cookie-response"
            or type(response.get("version")) is not int
            or response["version"] != 1
            or response.get("videoId") != request["videoId"]
        ):
            raise BrowserSessionError("browser_response_mismatch")
        fields = {"type", "version", "requestId", "videoId"}
        if set(response) == fields | {"cookies"}:
            serialize_cookies(response["cookies"])
            result = {"request": request, "cookies": response["cookies"]}
        elif (
            set(response) == fields | {"error"}
            and type(response["error"]) is str
            and response["error"] in _ERROR_CODES
        ):
            result = {"request": request, "error": response["error"]}
        else:
            raise BrowserSessionError("browser_response_invalid")
        # Do not persist a reply after the original task has canceled/deleted its request.
        request_path = self.root / f"request-{request_id}.json"
        try:
            if decode_message(_read(request_path)) != request:
                raise BrowserSessionError("browser_response_mismatch")
        except OSError:
            raise BrowserSessionError("browser_response_expired") from None
        encrypted = win32crypt.CryptProtectData(
            _json(result), "Fabu YouTube session", None, None, None, 1
        )
        response_path = self.root / f"response-{request_id}.dpapi"
        _atomic(response_path, encrypted)
        if not request_path.exists() or time.time() >= request["expiresAt"]:
            response_path.unlink(missing_ok=True)
        self.pending.pop(request_id, None)

    def __exit__(self, *args):
        try:
            path = self.root / "connected.json"
            if decode_message(_read(path)).get("instanceId") == self.instance_id:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        finally:
            if self._lock is not None:
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
                self._lock.close()
                self._lock = None
