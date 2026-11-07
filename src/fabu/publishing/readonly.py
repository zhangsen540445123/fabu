"""Query existing live attempts without constructing any write-capable gateway."""

from pathlib import Path

from fabu.publishing.connection import _package, connection_status
from fabu.publishing.gateway import ReconcileResult
from fabu.publishing.models import CLI_CONTRACT_VERSION


class LiveReconciler:
    execution_scope = "live"

    def __init__(self, app, cancel):
        self.app, self.cancel = app, cancel

    def reconcile(self, attempt):
        if self.cancel.is_set():
            raise ValueError("Reconciliation canceled")
        if not attempt.provider_task_id:
            return ReconcileResult(
                target_id=attempt.target_id, state="UNKNOWN", reason="missing_provider_identity"
            )
        query_provider_details(self.app, attempt.provider_task_id, self.cancel)
        # Neither a provider 'success' nor elapsed time proves a platform publication.
        # Discard raw provider data; a verified platform receipt adapter is still required.
        return ReconcileResult(
            target_id=attempt.target_id,
            state="UNKNOWN",
            provider_task_id=attempt.provider_task_id,
            reason="platform_evidence_contract_unverified",
        )


def query_provider_details(app, provider_task_id, cancel):
    """Shared version-checked read path; no write-capable gateway is constructed."""
    if cancel.is_set():
        raise ValueError("Reconciliation canceled")
    # Keep connection replacement and temporary-config cleanup outside active reads.
    with app.state.settings_lock:
        if cancel.is_set():
            raise ValueError("Reconciliation canceled")
        status = connection_status(app)
        if not status["configured"] or not status["package_root"]:
            raise ValueError("Configure the official CLI connection first")
        root = _package(Path(status["package_root"]))
        with app.state.credentials.temporary_cli_config() as config:
            client = app.state.readonly_cli_factory(root / "bin-native/yxer.exe", config, root)
            client.cancel = cancel
            if client.query("version").get("version") != CLI_CONTRACT_VERSION:
                raise ValueError("CLI version is not the reviewed contract")
            if cancel.is_set():
                raise ValueError("Reconciliation canceled")
            return client.details(provider_task_id)
