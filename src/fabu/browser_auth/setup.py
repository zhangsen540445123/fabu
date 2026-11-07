"""Prepare a user-local native host; never install/alter Chrome profiles automatically."""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import winreg
from pathlib import Path

from fabu.assets import assert_no_reparse
from fabu.browser_auth.bridge import BrowserSessionClient, _atomic, _json, _read
from fabu.browser_auth.protocol import decode_message
from fabu.credentials import secure_directory

HOST = "com.fabu.youtube_session"
_REGISTRY_KEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST}"
_PERMISSIONS = {"cookies", "nativeMessaging", "storage", "alarms"}
_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _native_path(path):
    if not path.is_absolute() or any(c in str(path) for c in '\r\n\0"%&|<>^!'):
        raise ValueError("Native host paths must be absolute and contain no shell metacharacters")
    assert_no_reparse(path)


def prepare_installation(source: Path, state_dir: Path, python_path: Path):
    for path in (source, state_dir, python_path):
        _native_path(path)
    if not python_path.is_file() or python_path.suffix.lower() != ".exe":
        raise ValueError("Select the installed Fabu Python executable")
    manifest = decode_message(_read(source / "manifest.json", 65536))
    if (
        manifest.get("manifest_version") != 3
        or set(manifest.get("permissions", [])) != _PERMISSIONS
        or manifest.get("host_permissions")
        or manifest.get("content_scripts")
        or manifest.get("optional_host_permissions") != ["https://*.youtube.com/*"]
    ):
        raise ValueError("Extension must be restricted to YouTube and native messaging")
    try:
        key = base64.b64decode(manifest["key"], validate=True)
        if not 64 <= len(key) <= 4096:
            raise ValueError()
        extension_id = "".join(
            chr(ord("a") + int(char, 16)) for char in hashlib.sha256(key).hexdigest()[:32]
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("Extension must provide a valid stable public key") from None
    for filename in (
        manifest.get("background", {}).get("service_worker"),
        manifest.get("action", {}).get("default_popup"),
    ):
        if (
            not isinstance(filename, str)
            or not _FILENAME.fullmatch(filename)
            or not (source / filename).is_file()
        ):
            raise ValueError("Extension entry points must be packaged local files")
    package = {}
    for path in source.iterdir():
        if path.is_file() and path.suffix in {
            ".js",
            ".mjs",
            ".json",
            ".html",
            ".css",
            ".png",
            ".svg",
        }:
            if ".test." in path.name or path.name.startswith("package"):
                continue
            if not _FILENAME.fullmatch(path.name):
                raise ValueError("Invalid extension package filename")
            package[path.name] = _read(path, 1048576)
    install = state_dir / "browser-helper"
    extension_dir = install / "chrome-extension"
    if source == extension_dir:
        raise ValueError("Source package must differ from installed extension")
    secure_directory(install)
    secure_directory(extension_dir)
    secure_directory(state_dir / "browser-auth")
    for name, data in package.items():
        _atomic(extension_dir / name, data)
    origin = f"chrome-extension://{extension_id}/"
    config_path = install / "native-config.json"
    _atomic(
        config_path,
        _json({"version": 1, "root": str(state_dir / "browser-auth"), "allowed_origin": origin}),
    )
    batch_path = install / "fabu-youtube-host.bat"
    # Chrome's official Windows sample uses a .bat native host. All paths above reject
    # shell metacharacters; -I ignores ambient PYTHONPATH/PYTHONHOME/user site packages.
    batch = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\nchcp 65001 >nul\r\n"
        f'"{python_path}" -I -u -X utf8 -m fabu.browser_auth.native_host '
        f'--config "{config_path}" %*\r\n'
    )
    _atomic(batch_path, batch.encode("utf-8"))
    host_path = install / f"{HOST}.json"
    _atomic(
        host_path,
        _json(
            {
                "name": HOST,
                "description": "Fabu YouTube local session bridge",
                "path": str(batch_path),
                "type": "stdio",
                "allowed_origins": [origin],
            }
        ),
    )
    return {
        "extension_directory": str(extension_dir),
        "host_manifest": str(host_path),
        "extension_id": extension_id,
        "browser_install_required": True,
    }


def register_native_manifest(manifest_path: Path):
    _native_path(manifest_path)
    if not manifest_path.is_file():
        raise ValueError("Native host manifest is missing")
    expected = os.path.normcase(os.path.abspath(manifest_path))
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REGISTRY_KEY, 0, winreg.KEY_READ | view
            ) as key:
                value, kind = winreg.QueryValueEx(key, "")
        except FileNotFoundError:
            continue
        if kind != winreg.REG_SZ or os.path.normcase(os.path.abspath(value)) != expected:
            raise ValueError("A different Fabu native host is already registered; not overwritten")
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _REGISTRY_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))


def main():
    parser = argparse.ArgumentParser(description="Prepare Fabu YouTube Chrome helper")
    parser.add_argument("action", choices=["prepare", "install", "status"])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--extension-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "status":
            result = BrowserSessionClient(args.state_dir / "browser-auth").status()
        else:
            if args.extension_dir is None:
                raise ValueError("Select the reviewed Fabu Chrome extension source directory")
            result = prepare_installation(args.extension_dir, args.state_dir, Path(sys.executable))
            if args.action == "install":
                register_native_manifest(Path(result["host_manifest"]))
                result["native_host_registered"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError):
        print(json.dumps({"error": "browser_helper_setup_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
