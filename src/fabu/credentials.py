"""Current-user DPAPI secrets with protected current-user/SYSTEM-only temporary files."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import ntsecuritycon
import pywintypes
import win32api
import win32con
import win32crypt
import win32security
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from fabu.assets import assert_no_reparse


class Credentials(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    api_key: SecretStr
    local_client_id: str = Field(min_length=1, max_length=256, repr=False)

    @model_validator(mode="after")
    def nonempty(self):
        key = self.api_key.get_secret_value()
        if not key.strip() or not self.local_client_id.strip() or len(key) > 4096:
            raise ValueError("Both API key and local client ID are required")
        if any(ord(c) < 32 for c in key + self.local_client_id):
            raise ValueError("Credentials contain unsupported control characters")
        return self


def current_user_sid():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()


def secure_directory(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("Private storage must be an absolute directory")
    assert_no_reparse(path)
    path.mkdir(parents=True, exist_ok=True)
    acl = win32security.ACL()
    flags = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
    for sid in [current_user_sid(), win32security.ConvertStringSidToSid("S-1-5-18")]:
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION_DS, flags, ntsecuritycon.FILE_ALL_ACCESS, sid
        )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


class CredentialStore:
    def __init__(self, root: Path):
        secure_directory(root)
        self.root = root
        self.path = root / "credentials.dpapi"
        self.user_sid = win32security.ConvertSidToStringSid(current_user_sid())

    def configured(self) -> bool:
        try:
            self.load()
            return True
        except (ValueError, OSError):
            return False

    def save(self, credentials: Credentials) -> None:
        secure_directory(self.root)
        assert_no_reparse(self.path)
        plaintext = json.dumps(
            {
                "api_key": credentials.api_key.get_secret_value(),
                "local_client_id": credentials.local_client_id,
            }
        ).encode("utf-8")
        encrypted = win32crypt.CryptProtectData(
            plaintext, "Fabu user credentials", None, None, None, 1
        )
        temporary = self.root / f"encrypted-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> Credentials:
        assert_no_reparse(self.path)
        try:
            if not self.path.is_file() or self.path.stat().st_size > 65536:
                raise ValueError("Credentials are not configured")
            _, data = win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 1)
            return Credentials.model_validate_json(data)
        except (pywintypes.error, ValueError) as exc:
            raise ValueError("Credentials could not be decrypted by this Windows user") from exc

    @contextmanager
    def temporary_cli_config(self):
        credentials = self.load()
        folder = self.root / f"session-{uuid4()}"
        secure_directory(folder)
        path = folder / "auth.json"
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "apiKey": credentials.api_key.get_secret_value(),
                        "localPublishClientId": credentials.local_client_id,
                    },
                    stream,
                )
            yield path
        finally:
            assert_no_reparse(folder)
            path.unlink(missing_ok=True)
            folder.rmdir()

    def cleanup_stale_sessions(self) -> None:
        # Startup caller MUST already hold the global worker lock; don't erase an active session.
        assert_no_reparse(self.root)
        for folder in self.root.glob("session-*"):
            try:
                UUID(folder.name.removeprefix("session-"))
            except ValueError:
                continue
            assert_no_reparse(folder)
            if folder.resolve().parent != self.root.resolve() or not folder.is_dir():
                raise ValueError("Unsafe credential-session path")
            children = list(folder.iterdir())
            if any(p.name != "auth.json" or not p.is_file() for p in children):
                raise ValueError("Unexpected private-session contents; manual inspection required")
            for path in children:
                assert_no_reparse(path)
                path.unlink()
            folder.rmdir()
