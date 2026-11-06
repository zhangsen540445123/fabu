import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from pydantic import ValidationError


def command(tmp_path, script, *args, timeout=5, output_limit=4_194_304):
    from fabu.tools import CommandSpec

    return CommandSpec(
        executable=Path(sys.executable),
        args=["-c", script, *args],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=timeout,
        max_output_bytes=output_limit,
    )


def test_arguments_remain_literal(tmp_path):
    from fabu.tools import run_command

    text = "剃须 [1] & echo x ' 😴 ; $(not a command)"
    result = run_command(
        command(tmp_path, "import json,sys; print(json.dumps(sys.argv[1:]))", text),
        threading.Event(),
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == [text]
    assert result.stderr == b""
    assert not result.timed_out


def test_timeout_is_not_success(tmp_path):
    from fabu.tools import run_command

    result = run_command(
        command(tmp_path, "import time; time.sleep(30)", timeout=0.2), threading.Event()
    )
    assert result.timed_out is True
    assert result.exit_code != 0


def test_nonzero_exit_preserved(tmp_path):
    from fabu.tools import run_command

    result = run_command(
        command(tmp_path, "import sys; sys.stderr.write('bad'); sys.exit(7)"), threading.Event()
    )
    assert result.exit_code == 7
    assert result.stderr == b"bad"


def test_pre_canceled_does_not_start_process(tmp_path):
    from fabu.tools import run_command

    cancel = threading.Event()
    cancel.set()
    result = run_command(command(tmp_path, "open('should-not-exist','w').write('x')"), cancel)
    assert result.canceled
    assert not (tmp_path / "should-not-exist").exists()


def test_cancel_kills_owned_descendants_not_unrelated_process(tmp_path):
    from fabu.tools import run_command

    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    cancel = threading.Event()
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
    )
    result_box = []
    worker = threading.Thread(
        target=lambda: result_box.append(
            run_command(command(tmp_path, script, str(pid_file)), cancel)
        )
    )
    try:
        worker.start()
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.03)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        cancel.set()
        worker.join(5)
        assert not worker.is_alive()
        assert result_box[0].canceled
        deadline = time.monotonic() + 2
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not psutil.pid_exists(child_pid)
        assert unrelated.poll() is None
    finally:
        cancel.set()
        worker.join(6)
        unrelated.terminate()
        unrelated.wait(5)


def test_output_capture_is_bounded(tmp_path):
    from fabu.tools import run_command

    result = run_command(
        command(tmp_path, "import sys; sys.stdout.write('x'*200000)", output_limit=1024),
        threading.Event(),
    )
    assert result.output_limit_exceeded
    assert len(result.stdout) <= 1024
    assert result.exit_code != 0


@pytest.mark.parametrize("executable", ["python", "C:/missing/fake.exe"])
def test_invalid_executable_rejected(tmp_path, executable):
    from fabu.tools import CommandSpec

    with pytest.raises((ValueError, ValidationError)):
        CommandSpec(executable=executable, args=[], cwd=tmp_path, env={}, timeout_s=2)


def test_batch_shell_entry_rejected(tmp_path):
    from fabu.tools import CommandSpec

    batch = tmp_path / "unsafe.cmd"
    batch.write_text("echo test")
    with pytest.raises(ValueError, match="executable"):
        CommandSpec(executable=batch, args=[], cwd=tmp_path, env={}, timeout_s=2)


def test_redaction_covers_stderr_and_urls():
    from fabu.redaction import redact_event

    secret = "synthetic-secret-123"
    event = {
        "event": "tool_failed",
        "phase": "upload",
        "job_id": "123",
        "message": f"Bearer {secret} https://example.invalid/upload?apiKey={secret}",
        "stdout": secret,
        "stderr": f"api-key: {secret}",
        "api_key": secret,
        "clientId": "private-client",
        "payload": {"key": secret},
    }
    cleaned = redact_event(event, [secret])
    text = json.dumps(cleaned)
    assert secret not in text
    assert "private-client" not in text
    assert "apiKey=" not in text
    assert "stdout" not in cleaned and "stderr" not in cleaned and "payload" not in cleaned
    assert cleaned["event"] == "tool_failed"


def test_unknown_key_patterns_and_signed_urls_redacted():
    from fabu.redaction import redact_event

    out = redact_event(
        {
            "message": "api_key=not-known; Bearer another-secret "
            "https://host.test/file?X-Amz-Signature=signed-secret"
        },
        [],
    )
    assert "not-known" not in out["message"]
    assert "another-secret" not in out["message"]
    assert "signed-secret" not in out["message"]
