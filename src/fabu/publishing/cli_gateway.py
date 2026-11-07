"""Exact official CLI, explicit local channel, protected payloads, and fail-closed receipts."""

import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from uuid import uuid4

from fabu.assets import assert_no_reparse, sha256_file
from fabu.credentials import CredentialStore, secure_directory
from fabu.diagnostics import normalize_readonly
from fabu.publishing.capabilities import CapabilityReport
from fabu.publishing.cli_contract import matches_local_preflight
from fabu.publishing.cli_receipts import parse_publish_acknowledgement
from fabu.publishing.gateway import (
    PLATFORM_NAMES,
    MemoryUploadJournal,
    ReconcileResult,
    SubmissionReceipt,
    UploadReceipt,
    assemble_prepared,
    build_local_payloads,
    resource_metadata,
    verify_prepared,
)
from fabu.publishing.models import CLI_CONTRACT_VERSION, verify_publish_snapshot
from fabu.tools import CommandSpec, run_command

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


class CliPublisher:
    execution_scope = "live"

    def __init__(
        self,
        package_root: Path,
        credentials: CredentialStore,
        media_tools: Path,
        workspace: Path,
        authority,
        *,
        runner=run_command,
        journal=None,
        cancel: Event | None = None,
    ):
        for path in [package_root, media_tools, workspace]:
            if not path.is_absolute():
                raise ValueError("Publishing paths must be absolute")
            assert_no_reparse(path)
        self.package_root = package_root
        self.executable = package_root / "bin-native/yxer.exe"
        if not self.executable.is_file() or not all(
            (package_root / name).is_dir() for name in ["schemas", "references"]
        ):
            raise ValueError("Select the installed official CLI package root")
        if not all((media_tools / name).is_file() for name in ["ffmpeg.exe", "ffprobe.exe"]):
            raise ValueError("Publishing requires explicitly configured media tools")
        if runner is run_command and not getattr(journal, "durable", False):
            raise ValueError("Real publishing requires a durable upload journal")
        if runner is run_command and getattr(journal, "scope", None) != "live":
            raise ValueError("Real publishing requires a live-scoped upload journal")
        self.media_tools, self.credentials, self.workspace = media_tools, credentials, workspace
        self.authority, self.runner = authority, runner
        self.journal = journal if journal is not None else MemoryUploadJournal()
        self.cancel = cancel or Event()
        self._version_hash = None
        self.verification_context = None

    def bind_controlled_test(self, service, gate, permit, snapshot):
        if service.authority is not self.authority:
            raise ValueError("Verification belongs to another process authority")
        expected = service.controlled_gate(permit, snapshot, gate.evidence_id)
        if gate != expected:
            raise ValueError("Controlled gate is invalid")
        self.verification_context = (service, gate, permit, snapshot)

    def _controlled_identity(self, snapshot, permit):
        self.authority.validate(permit, snapshot, real=True)
        if self.verification_context is None:
            if self.runner is run_command:
                raise ValueError(
                    "Real transport needs a verified cost boundary and controlled gate"
                )
            return
        service, gate, original, bound = self.verification_context
        if (
            permit != original
            or snapshot != bound
            or gate != service.controlled_gate(permit, snapshot, gate.evidence_id)
        ):
            raise ValueError("Controlled execution scope changed")
        client = hashlib.sha256(self.credentials.load().local_client_id.encode()).hexdigest()
        if client != gate.client_sha256 or sha256_file(self.executable) != gate.cli_sha256:
            raise ValueError("Controlled CLI binary/client identity changed")

    def _call(self, args, *, timeout=60):
        if args and (args[0] == "upload" or args[0] == "publish" and "--dry-run" not in args):
            if self.verification_context is not None:
                service, gate, permit, snapshot = self.verification_context
                self._controlled_identity(snapshot, permit)
                deadline = min(gate.expires_at, permit.expires_at)
                remaining = (deadline - self.authority.clock()).total_seconds()
                if remaining <= 0:
                    raise ValueError("Write deadline expired")
                timeout = min(timeout, remaining)
            elif self.runner is run_command:
                raise ValueError("No verified cost boundary for real external writes")
        if self.cancel.is_set():
            raise ValueError("Publishing operation canceled before start")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("YIXIAOER_", "YXER_"))}
        system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
        # The CLI resolves media tools on PATH; inherit only explicitly bound directories.
        env["PATH"] = str(self.media_tools) + os.pathsep + str(system)
        env["YIXIAOER_PROJECT_DIR"] = str(self.package_root)
        env["YIXIAOER_API_URL"] = "https://www.yixiaoer.cn/api"
        with self.credentials.temporary_cli_config() as config:
            env["YIXIAOER_CONFIG"] = str(config)
            result = self.runner(
                CommandSpec(
                    executable=self.executable,
                    args=args,
                    cwd=self.package_root,
                    env=env,
                    timeout_s=timeout,
                    max_output_bytes=4_194_304,
                ),
                self.cancel,
            )
        try:
            raw = json.loads((result.stdout or result.stderr).decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        good = (
            not result.exit_code
            and not result.timed_out
            and not result.canceled
            and not result.output_limit_exceeded
            and raw.get("ok") is True
            and raw.get("version") == CLI_CONTRACT_VERSION
        )
        return raw, good

    def _version(self):
        assert_no_reparse(self.executable)
        digest = sha256_file(self.executable)
        if self._version_hash is not None:
            if digest != self._version_hash:
                raise ValueError("CLI executable changed; recheck its contract before publishing")
            return
        _, good = self._call(["--version"])
        if not good:
            raise ValueError("CLI version does not match the reviewed contract")
        self._version_hash = digest

    def _files(self, snapshot):
        verify_publish_snapshot(snapshot)
        for item in [snapshot.output, snapshot.cover]:
            path = item.path if hasattr(item, "path") else item.local_path
            assert_no_reparse(path)
            if (
                not path.is_file()
                or path.stat().st_size != item.size_bytes
                or sha256_file(path) != item.sha256
            ):
                raise ValueError("Frozen publication resource is missing or changed")

    @contextmanager
    def _payload_file(self, payload):
        secure_directory(self.workspace)
        path = self.workspace / f"payload-{uuid4()}.json"
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
            yield path
        finally:
            assert_no_reparse(path)
            path.unlink(missing_ok=True)

    def preflight(self, snapshot):
        verify_publish_snapshot(snapshot)
        self._version()
        raw, good = self._call(["accounts", "list", "--json"])
        try:
            observed = normalize_readonly("accounts", raw)["accounts"] if good else []
        except (ValueError, RuntimeError):
            observed = []
            good = False
        accounts = {a["id"]: a for a in observed}
        valid = all(
            t.account.account_id in accounts
            and accounts[t.account.account_id]["active"]
            and accounts[t.account.account_id]["platform"] == t.account.platform
            for t in snapshot.targets
        )
        report = CapabilityReport(
            checked_at=self.authority.clock(),
            tool_version=CLI_CONTRACT_VERSION,
            account_status="SUPPORTED" if good and valid else "UNKNOWN",
            accounts=[
                a for a in observed if a["id"] in {t.account.account_id for t in snapshot.targets}
            ],
            evidence_refs=["official-cli:read-only-accounts"],
            blockers=["free_write_and_platform_acceptance_not_verified"],
        )

        if self.verification_context is not None:
            service, gate, permit, bound = self.verification_context
            self._controlled_identity(snapshot, permit)
            report = service.prepare_controlled_report(
                gate,
                permit,
                snapshot,
                report,
                cli_sha256=sha256_file(self.executable),
                client_sha256=hashlib.sha256(
                    self.credentials.load().local_client_id.encode()
                ).hexdigest(),
            )
        return report

    def _upload(self, snapshot, permit, kind):
        self._controlled_identity(snapshot, permit)
        if self.cancel.is_set():
            raise ValueError("Publishing operation canceled before upload")
        cached = self.journal.get(snapshot.digest, kind)
        if cached is not None:
            if self.verification_context is not None:
                service, gate, bound_permit, bound_snapshot = self.verification_context
                if not service.resource_reusable(gate, permit, snapshot, cached):
                    raise ValueError("Cached upload lifetime/reuse contract is unverified")
            expected = snapshot.output.sha256 if kind == "video" else snapshot.cover.sha256
            if cached.sha256 != expected:
                raise ValueError("Cached resource belongs to different media")
            return cached
        self.authority.consume(permit, snapshot, "upload")
        self.journal.begin(snapshot.digest, kind)
        path = snapshot.output.path if kind == "video" else snapshot.cover.local_path
        raw, good = self._call(
            ["upload", str(path), "--bucket", "cloud-publish", "--json"], timeout=86400
        )
        data = raw.get("data", {})
        if not good or not isinstance(data, dict):
            raise ValueError("Resource upload outcome unknown; do not repeat blindly")
        key = data.get("key")
        if (
            not isinstance(key, str)
            or not key.startswith("cloud-publish/")
            or ".." in key
            or len(key) > 2048
            or any(ord(c) < 32 for c in key)
            or data.get("compressed") is True
        ):
            raise ValueError("Upload receipt did not identify unchanged temporary media")
        metadata = resource_metadata(snapshot, kind)
        if any(
            field in data and data[field] != metadata[field]
            for field in ("size", "width", "height")
            if field in metadata
        ):
            raise ValueError("Uploaded media metadata differs from the frozen file")
        self._files(snapshot)
        receipt = UploadReceipt(
            resource=kind,
            sha256=snapshot.output.sha256 if kind == "video" else snapshot.cover.sha256,
            key=key,
            metadata=metadata,
            raw=data,
        )
        self.journal.complete(snapshot.digest, receipt)
        return receipt

    def _validate_payloads(self, snapshot, permit, payloads, client_id, *, dry_run):
        for target in snapshot.targets:
            platform = PLATFORM_NAMES[target.account.platform]
            with self._payload_file(payloads[target.account.account_id]) as path:
                channel = [
                    "--publish-channel",
                    "local",
                    "--client-id",
                    client_id,
                    "--json",
                ]
                commands = [["validate", platform, "video", str(path), *channel]]
                if dry_run:
                    commands.append(
                        ["publish", "video", platform, str(path), "--dry-run", *channel]
                    )
                for command in commands:
                    self.authority.validate(permit, snapshot, real=True)
                    raw, good = self._call(command)
                    phase = "validate" if command[0] == "validate" else "dry_run"
                    if not good or not matches_local_preflight(
                        raw, payloads[target.account.account_id], phase
                    ):
                        raise ValueError(
                            "Local-channel preflight is unverified; submission remains blocked"
                        )

    def prepare(self, snapshot, permit):
        self._controlled_identity(snapshot, permit)
        self.authority.validate(permit, snapshot, real=True)
        self._files(snapshot)
        self._version()
        credentials = self.credentials.load()
        if not _ID.fullmatch(credentials.local_client_id):
            raise ValueError("Unsupported local client ID format")
        # Structural checks use unmistakably non-uploaded keys in unsigned payloads only.
        # Validate ALL targets before beginning any journal row or spending upload budget.
        structural = build_local_payloads(
            snapshot,
            {**resource_metadata(snapshot, "video"), "key": "fabu-preflight-only/video"},
            {**resource_metadata(snapshot, "cover"), "key": "fabu-preflight-only/cover"},
            credentials.local_client_id,
        )
        self._validate_payloads(
            snapshot, permit, structural, credentials.local_client_id, dry_run=False
        )
        self._files(snapshot)
        self._version()
        if self.credentials.load().local_client_id != credentials.local_client_id:
            raise ValueError("Local client binding changed during structural validation")
        resources = tuple(self._upload(snapshot, permit, kind) for kind in ("video", "cover"))
        prepared = assemble_prepared(
            snapshot, resources, credentials.local_client_id, self.authority
        )
        self._validate_payloads(
            snapshot, permit, prepared.payloads, prepared.client_id, dry_run=True
        )
        return prepared

    def submit(self, prepared, permit, target_id):
        self._controlled_identity(prepared.snapshot, permit)
        verify_prepared(prepared, self.authority)
        snapshot = prepared.snapshot
        self.authority.validate(permit, snapshot, real=True)
        self._files(snapshot)
        self._version()
        if self.credentials.load().local_client_id != prepared.client_id:
            raise ValueError("Local client binding changed after preparation")
        target = next((t for t in snapshot.targets if t.account.account_id == target_id), None)
        if target is None:
            raise ValueError("Target is outside the frozen snapshot")
        with self._payload_file(prepared.payloads[target_id]) as path:
            self.authority.consume(permit, snapshot, "submit", target_id)
            try:
                raw, good = self._call(
                    [
                        "publish",
                        "video",
                        PLATFORM_NAMES[target.account.platform],
                        str(path),
                        "--publish-channel",
                        "local",
                        "--client-id",
                        prepared.client_id,
                        "--json",
                    ],
                    timeout=300,
                )
            except Exception:
                return SubmissionReceipt(
                    target_id=target_id, state="UNKNOWN", reason="submission_outcome_unknown"
                )
        return parse_publish_acknowledgement(
            raw, transport_ok=good, target_id=target_id, client_id=prepared.client_id
        )

    def reconcile(self, attempt):
        if not attempt.provider_task_id or not _ID.fullmatch(attempt.provider_task_id):
            return ReconcileResult(
                target_id=attempt.target_id, state="UNKNOWN", reason="missing_provider_identity"
            )
        self._version()
        _, good = self._call(["query", "details", attempt.provider_task_id, "--json"])
        # Platform receipt schema is unverified. Task success/time alone proves nothing.
        return ReconcileResult(
            target_id=attempt.target_id,
            state="UNKNOWN",
            provider_task_id=attempt.provider_task_id,
            reason="platform_evidence_contract_unverified" if good else "reconciliation_failed",
        )
