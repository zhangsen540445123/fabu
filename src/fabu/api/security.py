"""Single-use bootstrap and cookie/CSRF protection for the loopback UI."""

import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Session:
    csrf: str
    expires_at: float


class LocalSecurity:
    def __init__(self, port: int):
        self.origin = f"http://127.0.0.1:{port}"
        self.host = f"127.0.0.1:{port}"
        self.bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_used = False
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def exchange(self, authorization: str | None) -> tuple[str, str] | None:
        expected = f"Bearer {self.bootstrap_token}"
        with self._lock:
            if self._bootstrap_used or not secrets.compare_digest(authorization or "", expected):
                return None
            self._bootstrap_used = True
            identifier, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._sessions[identifier] = Session(csrf, time.monotonic() + 86400)
            return identifier, csrf

    def session(self, request: Request) -> Session | None:
        identifier = request.cookies.get("fabu_session", "")
        with self._lock:
            current = self._sessions.get(identifier)
            if current and current.expires_at <= time.monotonic():
                self._sessions.pop(identifier, None)
                return None
            return current

    async def protect(self, request: Request, call_next):
        if request.headers.get("host") != self.host:
            return JSONResponse({"error": "invalid_host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin != self.origin:
            return JSONResponse({"error": "cross_origin_denied"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "cross_site_denied"}, status_code=403)
        if request.url.path.startswith("/api/"):
            write = request.method not in {"GET", "HEAD", "OPTIONS"}
            bootstrap = request.url.path == "/api/session" and request.method == "POST"
            public = request.url.path == "/api/health" and request.method == "GET"
            if write and origin != self.origin:
                return JSONResponse({"error": "origin_required"}, status_code=403)
            if not bootstrap and not public:
                session = self.session(request)
                if session is None:
                    return JSONResponse({"error": "session_required"}, status_code=401)
                if write and not secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""), session.csrf
                ):
                    return JSONResponse({"error": "csrf_required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        )
        return response
