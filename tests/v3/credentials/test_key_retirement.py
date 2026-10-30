"""Exercise master-store retirement, not a replacement of its delete method."""

import pytest

from image_downloader.credentials import CredentialVault
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.exceptions import VaultError


@pytest.fixture
def backend(monkeypatch):
    import keyring

    class WinVaultKeyring:
        def __init__(self):
            self.values = {}
            self.deleted = []
            self.ignore_delete = False

        def get_password(self, service, username):
            return self.values.get((service, username))

        def set_password(self, service, username, value):
            self.values[service, username] = value

        def delete_password(self, service, username):
            self.deleted.append((service, username))
            if not self.ignore_delete:
                self.values.pop((service, username), None)

    WinVaultKeyring.__module__ = "keyring.backends.Windows"
    backend = WinVaultKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    return backend


def _service(state):
    return MasterKeyStore.service(state["vault_id"], state["key_id"]), "MASTER"


def test_rotation_deletes_only_old_target_and_preserves_restored_vault(tmp_path, backend):
    original = CredentialVault(tmp_path / "original")
    original.profile("default").set_plugin("a.b", "TOKEN", "fictional-token")
    old = original.status()
    backup = tmp_path / "vault.backup"
    original.backup(backup, "fictional-passphrase")
    restored = CredentialVault(tmp_path / "restored")
    restored.restore(backup, "fictional-passphrase")
    restored_state = restored.status()
    assert restored_state["vault_id"] != old["vault_id"]
    assert len(backend.values) == 2

    original.rotate_key()

    current = original.status()
    assert current["vault_id"] == old["vault_id"]
    assert current["key_id"] != old["key_id"]
    assert _service(old) in backend.deleted
    assert _service(old) not in backend.values
    assert set(backend.values) == {_service(current), _service(restored_state)}
    assert original.profile("default").get_plugin("a.b", "TOKEN") == "fictional-token"
    assert restored.profile("default").get_plugin("a.b", "TOKEN") == "fictional-token"


def test_ignored_backend_delete_is_not_reported_as_success_and_can_resume(tmp_path, backend):
    vault = CredentialVault(tmp_path / "data")
    vault.profile("default").set_plugin("a.b", "TOKEN", "fictional-token")
    old = vault.status()
    backend.ignore_delete = True

    with pytest.raises(VaultError) as caught:
        vault.rotate_key()

    assert caught.value.code == "vault_access"
    assert vault.status()["state"] == "retirement_pending"
    assert _service(old) in backend.values
    assert len(backend.values) == 2
    assert vault.filesystem.path("security/rotation.enc").is_file()
    assert vault.profile("default").get_plugin("a.b", "TOKEN") == "fictional-token"
    backend.ignore_delete = False

    vault.rotate_key(resume=True)

    assert vault.status()["state"] == "ready"
    assert _service(old) not in backend.values
    assert set(backend.values) == {_service(vault.status())}
    assert not vault.filesystem.path("security/rotation.enc").exists()
