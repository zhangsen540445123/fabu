"""Local app assembly; application startup does not query accounts, upload, or publish."""

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from fabu.api.jobs import router as jobs_router
from fabu.api.security import LocalSecurity
from fabu.assets import AssetStore
from fabu.presets import PresetStore
from fabu.publishing.capabilities import CapabilityReport
from fabu.settings import Settings, check_environment
from fabu.store import JobStore


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: Path | None = None
    tools_dir: Path | None = None
    test_mode: Literal["local", "simulation"] | None = None


def create_app(settings: Settings, store: JobStore) -> FastAPI:
    patch_path = settings.state_dir / "ui-settings.json"
    if patch_path.is_file():
        patch = SettingsPatch.model_validate_json(patch_path.read_text(encoding="utf-8"))
        settings = Settings.model_validate(
            {**settings.model_dump(), **patch.model_dump(exclude_none=True)}
        )
    app = FastAPI(title="Fabu", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.settings_lock = threading.Lock()
    app.state.store = store
    app.state.security = LocalSecurity(settings.port)
    app.state.assets = AssetStore(settings.state_dir / "assets", store)
    app.state.presets = PresetStore(store, app.state.assets)
    app.state.capabilities = CapabilityReport(checked_at=datetime.now(UTC))
    with store.connection() as db:
        existing = db.execute(
            "SELECT id FROM preset_versions ORDER BY created_at LIMIT 1"
        ).fetchone()
    if existing:
        default = app.state.presets.get_version(existing[0])
    else:
        default = app.state.presets.save_draft(
            None,
            {
                "name": "基础2K · 保留原声",
                "steps": [],
                "output": {
                    "codec": "libx265",
                    "preset": "fast",
                    "crf": 20,
                    "fps": 30,
                    "pixel_format": "yuv420p",
                    "size_policy": "auto",
                },
            },
        )
    app.state.default_preset = default
    app.middleware("http")(app.state.security.protect)
    app.include_router(jobs_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Pydantic error details include user input by default; never echo submitted credentials.
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "fields": [
                    {"location": error["loc"], "type": error["type"]} for error in exc.errors()
                ],
            },
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok", "publishing_enabled": False}

    @app.post("/api/session")
    def bootstrap(request: Request):
        credentials = app.state.security.exchange(request.headers.get("authorization"))
        if credentials is None:
            raise HTTPException(401, "Invalid or already used bootstrap token")
        session_id, csrf = credentials
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie(
            "fabu_session",
            session_id,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=86400,
            path="/api",
        )
        return response

    @app.get("/api/session")
    def session(request: Request):
        return {"csrf_token": app.state.security.session(request).csrf}

    @app.get("/api/capabilities")
    def capabilities():
        return app.state.capabilities

    @app.get("/api/settings")
    def get_settings():
        return {
            **app.state.settings.model_dump(mode="json"),
            "credentials": {"configured": False},
            "environment": check_environment(app.state.settings).model_dump(mode="json"),
            "default_preset_version_id": app.state.default_preset.id,
        }

    @app.put("/api/settings")
    def update_settings(patch: SettingsPatch):
        with app.state.settings_lock:
            changes = patch.model_dump(exclude_none=True)
            if "data_dir" in changes and store.list_jobs(limit=1):
                raise HTTPException(409, "Data directory changes require no existing tasks")
            try:
                candidate = Settings.model_validate({**app.state.settings.model_dump(), **changes})
            except ValueError as exc:
                raise HTTPException(422, "Invalid local settings") from exc
            persisted = SettingsPatch(
                data_dir=candidate.data_dir,
                tools_dir=candidate.tools_dir,
                test_mode=candidate.test_mode,
            )
            temporary = patch_path.with_suffix(".tmp")
            temporary.write_text(persisted.model_dump_json(), encoding="utf-8")
            os.replace(temporary, patch_path)
            app.state.settings = candidate
            return get_settings()

    distribution = Path(__file__).parents[3] / "frontend" / "dist"
    if (distribution / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=distribution / "assets"), name="ui-assets")

    @app.get("/")
    def index():
        if (distribution / "index.html").is_file():
            return FileResponse(distribution / "index.html")
        return JSONResponse(
            {"message": "Build the frontend before opening the dashboard"}, status_code=503
        )

    return app
