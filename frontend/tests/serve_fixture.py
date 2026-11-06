"""Synthetic local E2E fixture, separate from the real user runtime and credentials."""

import tempfile
from pathlib import Path

import uvicorn

from fabu.api.app import create_app
from fabu.settings import load_settings
from fabu.store import JobStore

with tempfile.TemporaryDirectory(prefix="fabu-e2e-") as folder:
    root = Path(folder)
    settings = load_settings(
        {"LOCALAPPDATA": str(root), "FABU_DATA_DIR": str(root / "media"), "FABU_PORT": "8766"}
    )
    app = create_app(settings, JobStore(settings.db_path))
    # This fixed token exists only in this isolated test process; never a production setting.
    app.state.security.bootstrap_token = "synthetic-e2e-bootstrap-not-a-real-credential"
    uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False, log_level="warning")
