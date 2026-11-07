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
        # Keep connection replacement and temporary-config cleanup outside active reads.
        with self.app.state.settings_lock:
            status = connection_status(self.app)
            if not status["configured"] or not status["package_root"]:
                raise ValueError("Configure the official CLI connection first")
            root = _package(Path(status["package_root"]))
            with self.app.state.credentials.temporary_cli_config() as config:
                client = self.app.state.readonly_cli_factory(
                    root / "bin-native/yxer.exe", config, root
                )
                client.cancel = self.cancel
                if client.query("version").get("version") != CLI_CONTRACT_VERSION:
                    raise ValueError("CLI version is not the reviewed contract")
                if self.cancel.is_set():
                    raise ValueError("Reconciliation canceled")
                client.details(attempt.provider_task_id)
        # Neither a provider 'success' nor elapsed time proves a platform publication.
        # Discard raw provider data; a verified platform receipt adapter is still required.
        return ReconcileResult(
            target_id=attempt.target_id,
            state="UNKNOWN",
            provider_task_id=attempt.provider_task_id,
            reason="platform_evidence_contract_unverified",
        )
