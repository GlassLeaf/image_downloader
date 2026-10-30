"""Master keys, scoped persistence, non-destructive failure and recovery."""

import json

import pytest
from support.vault import MemoryKeys

from image_downloader.credentials import CredentialVault
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.credentials.plugin_secrets import RuntimeSecrets
from image_downloader.credentials.vault_crypto import FILE_LIMIT, SECRET_LIMIT
from image_downloader.exceptions import SecretNotFoundError, VaultError


@pytest.fixture
def vault(tmp_path):
    return CredentialVault(tmp_path / "data", keys=MemoryKeys())


def test_empty_reads_do_not_initialize_or_access_os(vault):
    assert vault.status() == {"state": "uninitialized"}
    assert vault.profile("a").list_entries("plugin") == []
    with pytest.raises(SecretNotFoundError):
        vault.profile("a").get_plugin("a.b", "TOKEN")
    assert not vault.filesystem.root.exists()
    assert not vault.keys.created


def test_profile_crud_large_secrets_and_no_plaintext(vault):
    a, b = vault.profile("a"), vault.profile("b")
    value = "fictional-long-secret-" * 300
    a.set_plugin("a.b", "TOKEN", value)
    b.set_plugin("a.b", "TOKEN", "other-canary")
    a.set_smtp("MAIL", "user-canary", "password-canary")
    assert a.get_plugin("a.b", "TOKEN") == value
    assert b.get_plugin("a.b", "TOKEN") == "other-canary"
    assert a.get_smtp("MAIL").username == "user-canary"
    assert "canary" not in repr(a.get_smtp("MAIL"))
    assert a.list_entries("plugin") == [{"kind": "plugin", "plugin_id": "a.b", "reference": "TOKEN"}]
    assert len(vault.keys.values) == 1
    for path in vault.filesystem.root.rglob("*"):
        if path.is_file():
            assert b"canary" not in path.read_bytes()
    assert a.delete("plugin", "TOKEN", "a.b")
    assert not a.delete("plugin", "TOKEN", "a.b")
    assert b.get_plugin("a.b", "TOKEN") == "other-canary"


def test_environment_and_old_keyring_are_not_read(vault, monkeypatch):
    monkeypatch.setenv("IMAGE_DOWNLOADER_PLUGIN_612E62_544F4B454E", "unused-env-canary")
    store = vault.profile("a")
    provider = RuntimeSecrets("a.b", {"token": "TOKEN"}, store=store)
    with pytest.raises(SecretNotFoundError):
        provider.get("token")
    store.set_plugin("a.b", "TOKEN", "file-canary")
    assert provider.get("token") == "file-canary"
    store.set_plugin("a.b", "TOKEN", "changed-canary")
    assert provider.get("token") == "changed-canary"


def test_key_loss_does_not_generate_or_overwrite(vault):
    store = vault.profile("a")
    store.set_plugin("a.b", "TOKEN", "canary")
    before = {p: p.read_bytes() for p in vault.filesystem.root.rglob("*.enc")}
    vault.keys.values.clear()
    count = len(vault.keys.created)
    for operation in (
        lambda: store.get_plugin("a.b", "TOKEN"),
        lambda: store.set_plugin("a.b", "TOKEN", "new"),
        vault.initialize,
    ):
        with pytest.raises(VaultError) as error:
            operation()
        assert error.value.code == "vault_key_missing"
    assert len(vault.keys.created) == count
    assert all(p.read_bytes() == data for p, data in before.items())


def test_profile_swap_and_tampering_rejected(vault):
    for profile in ("a", "b"):
        vault.profile(profile).set_plugin("a.b", "TOKEN", profile)
    a = vault.filesystem.path(vault.relative("a", "credentials"))
    b = vault.filesystem.path(vault.relative("b", "credentials"))
    b.write_bytes(a.read_bytes())
    with pytest.raises(VaultError, match="different scope"):
        vault.profile("b").get_plugin("a.b", "TOKEN")
    payload = json.loads(a.read_bytes())
    payload["data"]["ciphertext"] = "AAAA"
    a.write_text(json.dumps(payload))
    with pytest.raises(VaultError, match="authenticated"):
        vault.profile("a").get_plugin("a.b", "TOKEN")


def test_backup_restore_new_identity_and_all_profiles(vault, tmp_path):
    for profile in ("a", "b"):
        vault.profile(profile).set_plugin("a.b", "TOKEN", "canary-" + profile)
    backup = tmp_path / "backup.enc"
    vault.backup(backup, "passphrase")
    assert b"canary" not in backup.read_bytes()
    restored = CredentialVault(tmp_path / "restored", keys=vault.keys)
    restored.restore(backup, "passphrase")
    assert restored.status()["vault_id"] != vault.status()["vault_id"]
    assert restored.status()["key_id"] != vault.status()["key_id"]
    for profile in ("a", "b"):
        assert restored.profile(profile).get_plugin("a.b", "TOKEN") == "canary-" + profile
    with pytest.raises(VaultError, match="empty destination"):
        restored.restore(backup, "passphrase")
    untouched = CredentialVault(tmp_path / "untouched", keys=vault.keys)
    with pytest.raises(VaultError):
        untouched.restore(backup, "wrong")
    assert not untouched.filesystem.root.exists()


def test_rotation_rewraps_without_reencrypting_data(vault):
    store = vault.profile("a")
    store.set_plugin("a.b", "TOKEN", "canary")
    path = vault.filesystem.path(vault.relative("a", "credentials"))
    before = json.loads(path.read_bytes())
    old_id = vault.status()["key_id"]
    vault.rotate_key()
    after = json.loads(path.read_bytes())
    assert before["data"] == after["data"]
    assert before["wrapped_key"] != after["wrapped_key"]
    assert old_id != vault.status()["key_id"]
    assert len(vault.keys.values) == 1
    store.set_plugin("a.b", "TOKEN", "new-canary")
    assert json.loads(path.read_bytes())["key_id"] == vault.status()["key_id"]
    assert store.get_plugin("a.b", "TOKEN") == "new-canary"


@pytest.mark.parametrize("boundary", ["prepared", "rewrapping", "file", "committed", "meta", "delete"])
def test_rotation_every_durable_boundary_can_resume(vault, monkeypatch, boundary):
    for profile in ("a", "b"):
        vault.profile(profile).set_plugin("a.b", "TOKEN", "canary-" + profile)
    old_write = vault._write_json
    old_delete = vault.keys.delete
    tripped = False

    def write(path, value, *args):
        nonlocal tripped
        old_write(path, value, *args)
        if path.name == "rotation.enc":
            phase = vault._rotation(vault._meta())["phase"]
            hit = phase == boundary
        else:
            hit = (boundary == "file" and path.suffix == ".enc") or (boundary == "meta" and path.name == "vault.json")
        if hit and not tripped:
            tripped = True
            raise OSError("artificial interruption")

    def delete(*args):
        nonlocal tripped
        old_delete(*args)
        if boundary == "delete" and not tripped:
            tripped = True
            raise OSError("artificial interruption")

    monkeypatch.setattr(vault, "_write_json", write)
    monkeypatch.setattr(vault.keys, "delete", delete)
    with pytest.raises(VaultError):
        vault.rotate_key()
    assert tripped
    monkeypatch.setattr(vault, "_write_json", old_write)
    monkeypatch.setattr(vault.keys, "delete", old_delete)
    vault.rotate_key(resume=True)
    assert len(vault.keys.values) == 1
    for profile in ("a", "b"):
        assert vault.profile(profile).get_plugin("a.b", "TOKEN") == "canary-" + profile


def test_restore_interruption_can_resume(vault, tmp_path, monkeypatch):
    vault.profile("a").set_plugin("a.b", "TOKEN", "canary")
    backup = tmp_path / "backup.enc"
    vault.backup(backup, "passphrase")
    target = CredentialVault(tmp_path / "target", keys=vault.keys)
    put = target._put

    def interrupted(*args):
        put(*args)
        raise OSError("artificial interruption")

    monkeypatch.setattr(target, "_put", interrupted)
    with pytest.raises(VaultError):
        target.restore(backup, "passphrase")
    with pytest.raises(VaultError, match="incomplete"):
        target.profile("a").get_plugin("a.b", "TOKEN")
    monkeypatch.setattr(target, "_put", put)
    target.restore(backup, "passphrase")
    assert target.profile("a").get_plugin("a.b", "TOKEN") == "canary"


def test_size_limits_reject_before_initializing(vault):
    with pytest.raises(VaultError) as error:
        vault.profile("a").set_plugin("a.b", "TOKEN", "x" * (SECRET_LIMIT + 1))
    assert error.value.code == "vault_size"
    assert not vault.filesystem.root.exists()
    assert FILE_LIMIT > SECRET_LIMIT


def test_insecure_or_chained_backend_is_rejected(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: object(), raising=False)
    with pytest.raises(VaultError) as error:
        MasterKeyStore().get("dummy", "dummy")
    assert error.value.code == "vault_backend"
