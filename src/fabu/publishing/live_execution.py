"""One factory for the bound real transport, never a separate operator submission path."""

from pathlib import Path

from fabu.publishing.cli_gateway import CliPublisher
from fabu.publishing.connection import connection_status
from fabu.publishing.journal import SqliteUploadJournal


def make_publisher(app, snapshot, permit, cancel):
    app.state.live_policy.validate(permit, snapshot)
    status = connection_status(app)
    publisher = CliPublisher(
        Path(status["package_root"]),
        app.state.credentials,
        app.state.settings.tools_dir,
        app.state.settings.state_dir / "publishing-payloads",
        app.state.publication_authority,
        journal=SqliteUploadJournal(app.state.store, snapshot, scope="live"),
        cancel=cancel,
    )
    publisher.bind_user_job(app.state.live_policy, permit, snapshot)
    return publisher
