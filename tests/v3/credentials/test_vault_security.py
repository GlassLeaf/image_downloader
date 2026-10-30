"""Malformed containers, limits, backend policy and authenticated notifications."""

import asyncio
import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path

import pytest
from support.vault import MemoryKeys

from image_downloader import AuthenticationError, ConfigurationError, SecretNotFoundError
from image_downloader.configuration.models import Email
from image_downloader.credentials import CredentialVault
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.credentials.vault_crypto import FILE_LIMIT, encode
from image_downloader.observability import notification_delivery as delivery
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore, _cookie
from image_downloader.storage.transactions import finish_transaction


@pytest.fixture
def vault(tmp_path):
    return CredentialVault(tmp_path / "data", keys=MemoryKeys())


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", True),
        ("format", 2.0),
        ("format", 100),
        ("key_id", "wrong"),
        ("profile", "other"),
        ("purpose", "cookies"),
        ("vault_id", "0" * 32),
        ("wrapped_key", []),
        ("data", {"nonce": [], "ciphertext": "canary"}),
    ],
)
def test_invalid_envelope_stops_crud_without_changes(vault, field, value):
    store = vault.profile("a")
    store.set_plugin("a.b", "TOKEN", "canary")
    path = vault.filesystem.path(vault.relative("a", "credentials"))
    container = json.loads(path.read_bytes())
    container[field] = value
    path.write_bytes(encode(container))
    before = path.read_bytes()
    for action in (
        lambda: store.get_plugin("a.b", "TOKEN"),
        lambda: store.set_plugin("a.b", "TOKEN", "replacement"),
        lambda: store.delete("plugin", "TOKEN", "a.b"),
    ):
        with pytest.raises(AuthenticationError):
            action()
        assert path.read_bytes() == before


def test_oversized_sparse_file_is_rejected_before_read(vault, monkeypatch):
    store = vault.profile("a")
    store.set_plugin("a.b", "TOKEN", "canary")
    path = vault.filesystem.path(vault.relative("a", "credentials"))
    with path.open("wb") as stream:
        stream.truncate(FILE_LIMIT + 1)
    read = vault.filesystem.read_bytes_bounded

    def bounded(relative, limit):
        if relative == vault.relative("a", "credentials"):
            pytest.fail("oversized input read")
        return read(relative, limit)

    monkeypatch.setattr(vault.filesystem, "read_bytes_bounded", bounded)
    with pytest.raises(AuthenticationError, match="size limit"):
        store.get_plugin("a.b", "TOKEN")
    assert path.stat().st_size == FILE_LIMIT + 1


def test_backend_only_uses_immutable_local_master_items(monkeypatch):
    import keyring

    values, calls = {}, []

    class Backend:
        def get_password(self, service, account):
            calls.append(("get", service, account))
            return values.get((service, account))

        def set_password(self, service, account, value):
            calls.append(("set", service, account))
            values[service, account] = value

        def delete_password(self, service, account):
            calls.append(("delete", service, account))
            values.pop((service, account))

    Backend.__module__ = "keyring.backends.Windows"
    Backend.__name__ = "WinVaultKeyring"
    backend = Backend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    store = MasterKeyStore()
    store.create("vault", "key", b"x" * 32)
    assert backend.persist == "local machine"
    with pytest.raises(AuthenticationError):
        store.create("vault", "key", b"y" * 32)
    assert store.get("vault", "key") == b"x" * 32
    store.delete("vault", "key")
    store.delete("vault", "key")
    assert all(service == "image-downloader.master.vault.key" and account == "MASTER" for _, service, account in calls)
    assert len([item for item in calls if item[0] == "set"]) == 1


def test_cookie_backup_isolated_profiles_and_no_legacy_reads(vault, tmp_path, capsys):
    for name in ("a", "b"):
        store = CookieStore(FileSystem(vault.filesystem.root / "profiles" / name / "cookie"), store=vault.profile(name))
        store.filesystem.ensure_directory()
        legacy = store.filesystem.root / "cookies.enc"
        legacy.write_bytes(b"legacy-must-stay")
        assert list(store.load()) == []
        jar = store.load()
        jar.set_cookie(_cookie(name, "cookie-canary", domain="example.test"))
        store.save(jar)
        assert legacy.read_bytes() == b"legacy-must-stay"
        vault.profile(name).set_smtp("MAIL", "user-" + name, "pass-" + name)
    assert capsys.readouterr().err.count("legacy cookies are unused") == 2
    backup = tmp_path / "backup.enc"
    vault.backup(backup, "passphrase")
    target = CredentialVault(tmp_path / "restored", keys=vault.keys)
    target.restore(backup, "passphrase")
    for name in ("a", "b"):
        store = CookieStore(
            FileSystem(target.filesystem.root / "profiles" / name / "cookie"), store=target.profile(name)
        )
        assert [cookie.name for cookie in store.load()] == [name]
        assert target.profile(name).get_smtp("MAIL").username == "user-" + name
        assert not store.filesystem.exists(Path("cookies.enc"))


def test_failed_key_retirement_can_resume_and_blocks_next_rotation(vault, monkeypatch):
    store = vault.profile("a")
    store.set_plugin("a.b", "TOKEN", "canary")
    delete = vault.keys.delete

    def denied(*args):
        raise PermissionError("private-keyring-canary")

    monkeypatch.setattr(vault.keys, "delete", denied)
    with pytest.raises(AuthenticationError):
        vault.rotate_key()
    assert vault.status()["state"] == "retirement_pending"
    store.set_plugin("a.b", "TOKEN", "updated")
    with pytest.raises(AuthenticationError, match="--resume"):
        vault.rotate_key()
    monkeypatch.setattr(vault.keys, "delete", delete)
    vault.rotate_key(resume=True)
    assert store.get_plugin("a.b", "TOKEN") == "updated"
    assert len(vault.keys.values) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_smtp_requires_profile_reference_and_never_environment(vault, monkeypatch, legacy):
    monkeypatch.setenv("IMAGE_DOWNLOADER_SMTP_PASSWORD", "unused-env-canary")
    sent = []
    monkeypatch.setattr(delivery, "_send_mail", lambda settings, *args: sent.append(settings))
    settings = {"smtp_host": "example.test", "from": "a@example.test", "to": ["b@example.test"]}
    if legacy:
        settings["username"] = "old-user"
        sender = delivery.EmailNotificationSender(Email.model_validate(settings), vault.profile("a"))
        with pytest.raises(ConfigurationError, match="credential_reference"):
            asyncio.run(sender.send("title", "message"))
    else:
        settings["credential_reference"] = "MAIL"
        sender = delivery.EmailNotificationSender(Email.model_validate(settings), vault.profile("a"))
        with pytest.raises(SecretNotFoundError):
            asyncio.run(sender.send("title", "message"))
        assert sent == []
        vault.profile("a").set_smtp("MAIL", "user-canary", "pass-canary")
        assert asyncio.run(sender.send("title", "message"))
        assert sent[0]["username"] == "user-canary" and sent[0]["password"] == "pass-canary"
        with pytest.raises(SecretNotFoundError):
            vault.profile("b").get_smtp("MAIL")


def test_broken_os_access_does_not_replace_existing_files(vault, monkeypatch):
    store = vault.profile("a")
    store.set_plugin("a.b", "TOKEN", "canary")
    before = {p: p.read_bytes() for p in vault.filesystem.root.rglob("*.enc")}

    def denied(*args):
        raise PermissionError("os-secret-canary")

    monkeypatch.setattr(vault.keys, "get", denied)
    with pytest.raises(AuthenticationError, match="cannot be accessed"):
        store.set_plugin("a.b", "TOKEN", "replacement")
    assert all(path.read_bytes() == data for path, data in before.items())


def test_corrupt_backup_and_nonempty_destination_do_not_publish_keys(vault, tmp_path):
    vault.profile("a").set_plugin("a.b", "TOKEN", "canary")
    backup = tmp_path / "backup.enc"
    vault.backup(backup, "passphrase")
    target = CredentialVault(tmp_path / "target", keys=vault.keys)
    target.filesystem.ensure_directory()
    (target.filesystem.root / "unrelated.txt").write_text("untouched")
    before = len(vault.keys.values)
    with pytest.raises(AuthenticationError, match="empty destination"):
        target.restore(backup, "passphrase")
    assert (target.filesystem.root / "unrelated.txt").read_text() == "untouched"
    assert len(vault.keys.values) == before
    container = json.loads(backup.read_bytes())
    container["data"]["ciphertext"] = "AAAA"
    backup.write_bytes(encode(container))
    empty = CredentialVault(tmp_path / "empty", keys=vault.keys)
    with pytest.raises(AuthenticationError):
        empty.restore(backup, "passphrase")
    assert not empty.filesystem.root.exists()


def test_private_directory_rejects_symlink_before_chmod(tmp_path):
    from image_downloader.credentials.vault_files import private_directory

    real, link = tmp_path / "real", tmp_path / "link"
    real.mkdir()
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ConfigurationError):
        private_directory(FileSystem(link))


def test_secret_limit_boundary_and_fresh_per_write_keys(vault):
    from image_downloader.credentials.vault_crypto import SECRET_LIMIT

    store = vault.profile("a")
    value = "x" * SECRET_LIMIT
    store.set_plugin("a.b", "TOKEN", value)
    path = vault.filesystem.path(vault.relative("a", "credentials"))
    first = json.loads(path.read_bytes())
    store.set_plugin("a.b", "TOKEN", value)
    second = json.loads(path.read_bytes())
    assert first["wrapped_key"] != second["wrapped_key"]
    assert first["data"] != second["data"]
    assert store.get_plugin("a.b", "TOKEN") == value
    with pytest.raises(AuthenticationError, match="size limit"):
        store.set_plugin("a.b", "TOKEN", value + "x")
    assert path.read_bytes() == encode(second)


@pytest.mark.parametrize("change", [{"format": True}, {"kdf": "scrypt-arbitrary"}, {"n": 2**60}])
def test_backup_rejects_untrusted_parameters_before_kdf(vault, tmp_path, monkeypatch, change):
    import image_downloader.credentials.vault as implementation

    source = tmp_path / "untrusted.backup"
    source.write_bytes(
        encode(
            {
                "format": 1,
                "kind": "image-downloader-vault-backup",
                "kdf": "scrypt-17-8-1",
                "salt": "bad",
                "data": {},
                **change,
            }
        )
    )
    monkeypatch.setattr(implementation, "backup_key", lambda *args: pytest.fail("untrusted KDF invoked"))
    with pytest.raises(AuthenticationError):
        vault.restore(source, "passphrase")
    assert not vault.filesystem.root.exists()


def test_cancelled_atomic_update_finishes_and_releases_locks(vault, monkeypatch):
    started, release = threading.Event(), threading.Event()
    write = vault._write_json

    def blocked(path, value, *args):
        if path.name == "credentials.enc":
            started.set()
            assert release.wait(10)
        write(path, value, *args)

    monkeypatch.setattr(vault, "_write_json", blocked)

    async def scenario():
        task = asyncio.create_task(finish_transaction(lambda: vault.profile("a").set_plugin("a.b", "TOKEN", "canary")))
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert vault.profile("a").get_plugin("a.b", "TOKEN") == "canary"
    vault.rotate_key()
    assert vault.profile("a").get_plugin("a.b", "TOKEN") == "canary"


@pytest.mark.parametrize("mutation", ["format", "scope", "ciphertext", "payload"])
def test_complete_cookie_replacement_also_authenticates_existing_data(vault, mutation):
    store = CookieStore(FileSystem(vault.filesystem.root / "profiles/a/cookie"), store=vault.profile("a"))
    store.save(CookieJar())
    if mutation == "payload":
        with vault.locked(write=True):
            vault.write_locked("a", "cookies", b"[{}]")
    else:
        value = json.loads(store.path.read_bytes())
        if mutation == "format":
            value["format"] = 100
        elif mutation == "scope":
            value["profile"] = "other"
        else:
            value["data"]["ciphertext"] = "AAAA"
        store.path.write_bytes(encode(value))
    before = store.path.read_bytes()
    with pytest.raises(AuthenticationError):
        store.save(CookieJar())
    assert store.path.read_bytes() == before
    count = len(vault.keys.created)
    with pytest.raises(AuthenticationError):
        vault.rotate_key()
    assert len(vault.keys.created) == count


@pytest.mark.parametrize(
    "target", ["security/rotation.enc", "profiles/a/credentials/credentials.enc", "profiles/a/cookie/cookies.v2.enc"]
)
def test_backup_cannot_publish_at_a_reserved_vault_path(vault, target):
    vault.initialize()
    path = vault.filesystem.root / target
    path.parent.mkdir(parents=True, exist_ok=True)
    before = vault.status()
    with pytest.raises(AuthenticationError):
        vault.backup(path, "passphrase")
    assert not path.exists()
    assert vault.status() == before


def test_cookie_read_does_not_restrict_permissions_or_rewrite_data(vault, monkeypatch):
    import image_downloader.credentials.vault as vault_module
    import image_downloader.storage.cookies as cookie_module

    store = CookieStore(FileSystem(vault.filesystem.root / "profiles/a/cookie"), store=vault.profile("a"))
    store.save(CookieJar())
    before = store.path.read_bytes()

    def unexpected(*args, **kwargs):
        pytest.fail("read-only operation attempted a directory or metadata write")

    monkeypatch.setattr(vault_module, "private_directory", unexpected)
    monkeypatch.setattr(cookie_module, "private_directory", unexpected)
    monkeypatch.setattr(vault, "_write_json", unexpected)
    assert list(store.load()) == []
    assert vault.status()["state"] == "ready"
    assert vault.profile("a").list_entries("plugin") == []
    assert store.path.read_bytes() == before
