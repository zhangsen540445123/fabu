import base64
import json
import sys
from pathlib import Path

import pytest


def extension(tmp_path):
    root = tmp_path / "extension-source"
    root.mkdir()
    manifest = {
        "manifest_version": 3,
        "name": "Fabu YouTube",
        "version": "0.1.0",
        "key": base64.b64encode(b"synthetic-public-key" * 20).decode(),
        "permissions": ["cookies", "nativeMessaging", "storage", "alarms"],
        "optional_host_permissions": ["https://*.youtube.com/*"],
        "background": {"service_worker": "background.js", "type": "module"},
        "action": {"default_popup": "popup.html"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "background.js").write_text("// synthetic extension")
    (root / "popup.html").write_text("<html></html>")
    (root / "worker.test.mjs").write_text("not a deployed asset")
    return root


def test_prepare_packages_without_registration_or_browser_changes(tmp_path):
    from fabu.browser_auth.setup import prepare_installation

    source = extension(tmp_path)
    result = prepare_installation(source, tmp_path / "state", Path(sys.executable))
    target = Path(result["extension_directory"])
    assert (target / "manifest.json").is_file()
    assert not (target / "worker.test.mjs").exists()
    manifest = json.loads(Path(result["host_manifest"]).read_text())
    assert manifest["name"] == "com.fabu.youtube_session"
    assert manifest["allowed_origins"] == [f"chrome-extension://{result['extension_id']}/"]
    assert len(result["extension_id"]) == 32 and set(result["extension_id"]) <= set(
        "abcdefghijklmnop"
    )
    batch = Path(manifest["path"]).read_text()
    assert str(Path(sys.executable)) in batch and "-I -u -X utf8" in batch
    assert " -m fabu.browser_auth.native_host " in batch
    assert "echo" in batch and "DisableDelayedExpansion" in batch
    assert result["browser_install_required"] is True
    assert not (tmp_path / "state" / "browser-auth" / "connected.json").exists()
    again = prepare_installation(source, tmp_path / "state", Path(sys.executable))
    assert result == again


@pytest.mark.parametrize(
    "change",
    [
        {"permissions": ["cookies", "debugger"]},
        {"host_permissions": ["<all_urls>"]},
        {"optional_host_permissions": ["https://*.google.com/*"]},
        {"key": "bad"},
        {"background": {"service_worker": "../outside.js"}},
    ],
)
def test_installer_refuses_broad_or_invalid_manifest(tmp_path, change):
    from fabu.browser_auth.setup import prepare_installation

    source = extension(tmp_path)
    path = source / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(change)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        prepare_installation(source, tmp_path / "state", Path(sys.executable))
    assert not (tmp_path / "state").exists()


def test_installer_rejects_shell_metacharacters_in_native_paths(tmp_path):
    from fabu.browser_auth.setup import prepare_installation

    source = extension(tmp_path)
    with pytest.raises(ValueError):
        prepare_installation(source, tmp_path / "%INJECT%", Path(sys.executable))
    assert not (tmp_path / "%INJECT%").exists()


def test_native_registry_registration_is_scoped_and_non_overwriting(tmp_path, monkeypatch):
    import winreg

    from fabu.browser_auth.setup import register_native_manifest

    manifest = tmp_path / "host.json"
    manifest.write_text("{}")
    calls = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def missing(*args):
        calls.append(("read", args))
        raise FileNotFoundError()

    monkeypatch.setattr(winreg, "OpenKey", missing)
    monkeypatch.setattr(
        winreg, "CreateKeyEx", lambda *args: calls.append(("create", args)) or Key()
    )
    monkeypatch.setattr(winreg, "SetValueEx", lambda *args: calls.append(("set", args)))
    register_native_manifest(manifest)
    created = [v for kind, v in calls if kind == "create"]
    assert len(created) == 1 and created[0][0] == winreg.HKEY_CURRENT_USER
    assert created[0][1] == r"Software\Google\Chrome\NativeMessagingHosts\com.fabu.youtube_session"
    calls.clear()
    monkeypatch.setattr(winreg, "OpenKey", lambda *args: Key())
    monkeypatch.setattr(
        winreg, "QueryValueEx", lambda *args: (str(tmp_path / "other.json"), winreg.REG_SZ)
    )
    with pytest.raises(ValueError, match="already registered"):
        register_native_manifest(manifest)
    assert not calls


def test_generated_windows_batch_launches_real_native_protocol(tmp_path):
    import io
    import os
    import subprocess

    from fabu.browser_auth.protocol import read_message
    from fabu.browser_auth.setup import prepare_installation

    result = prepare_installation(
        extension(tmp_path), tmp_path / "state with spaces", Path(sys.executable)
    )
    host = json.loads(Path(result["host_manifest"]).read_text())
    command = (
        f'"{os.environ["COMSPEC"]}" /d /s /c ""{host["path"]}" "{host["allowed_origins"][0]}""'
    )
    process = subprocess.run(
        command,
        input=b"",
        capture_output=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=tmp_path,
    )
    assert process.returncode == 0, process.stderr.decode("utf-8", "replace")
    assert process.stderr == b""
    assert read_message(io.BytesIO(process.stdout)) == {
        "type": "status",
        "version": 1,
        "status": "connected",
    }
