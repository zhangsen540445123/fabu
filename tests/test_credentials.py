import json
from pathlib import Path

import pytest


def test_secret_roundtrip_no_plaintext_persistence(tmp_path):
    from fabu.credentials import Credentials, CredentialStore

    secret = "synthetic-key-not-a-real-credential"
    vault = CredentialStore(tmp_path / "credentials")
    assert not vault.configured()
    vault.save(Credentials(api_key=secret, local_client_id="synthetic-client"))
    assert vault.configured()
    assert vault.load().api_key.get_secret_value() == secret
    assert secret not in repr(vault.load())
    assert all(secret.encode() not in p.read_bytes() for p in vault.root.rglob("*") if p.is_file())
    with vault.temporary_cli_config() as path:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"apiKey": secret, "localPublishClientId": "synthetic-client"}
        import win32security

        sd = win32security.GetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        acl = sd.GetSecurityDescriptorDacl()
        sids = {
            win32security.ConvertSidToStringSid(acl.GetAce(i)[2]) for i in range(acl.GetAceCount())
        }
        assert sids == {vault.user_sid, "S-1-5-18"}
    assert not path.exists()
    assert all(secret.encode() not in p.read_bytes() for p in vault.root.rglob("*") if p.is_file())


def test_temporary_config_is_cleaned_on_failure_and_stale_cleanup_is_scoped(tmp_path):
    from fabu.credentials import Credentials, CredentialStore

    vault = CredentialStore(tmp_path / "credentials")
    vault.save(Credentials(api_key="test-key", local_client_id="test-client"))
    with pytest.raises(RuntimeError):
        with vault.temporary_cli_config() as path:
            raise RuntimeError("simulated process failure")
    assert not path.exists()
    sentinel = vault.root / "user-note.txt"
    sentinel.write_text("keep")
    stale = vault.root / "session-00000000-0000-0000-0000-000000000001"
    stale.mkdir()
    (stale / "auth.json").write_text("stale synthetic")
    vault.cleanup_stale_sessions()
    assert not stale.exists() and sentinel.read_text() == "keep"


def test_corrupt_ciphertext_and_blank_credentials_fail_closed(tmp_path):
    from fabu.credentials import Credentials, CredentialStore

    for key, client in [("", "c"), ("x", ""), ("   ", "c")]:
        with pytest.raises(ValueError):
            Credentials(api_key=key, local_client_id=client)
    vault = CredentialStore(tmp_path / "credentials")
    vault.save(Credentials(api_key="test-key", local_client_id="test-client"))
    vault.path.write_bytes(b"corrupted encrypted data")
    with pytest.raises(ValueError):
        vault.load()
    with pytest.raises(ValueError):
        CredentialStore(Path("relative"))
