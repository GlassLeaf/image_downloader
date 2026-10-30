"""Artificial vaults only. Fixed keys also work across spawned test processes."""

from pathlib import Path

from image_downloader.credentials.vault import CredentialVault
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore


class FixedKeys:
    def get(self, vault_id, key_id):
        return b"t" * 32

    def create(self, vault_id, key_id, key):
        raise AssertionError("fixed artificial keys already exist")

    def delete(self, vault_id, key_id):
        raise AssertionError("fixed artificial keys cannot rotate")


class MemoryKeys:
    def __init__(self):
        self.values = {}
        self.created = []
        self.deleted = []

    def get(self, vault_id, key_id):
        return self.values.get((vault_id, key_id))

    def create(self, vault_id, key_id, key):
        assert (vault_id, key_id) not in self.values
        self.values[vault_id, key_id] = key
        self.created.append((vault_id, key_id))

    def delete(self, vault_id, key_id):
        self.values.pop((vault_id, key_id), None)
        self.deleted.append((vault_id, key_id))


def cookie_store(root, *, lock_timeout_seconds=30):
    root = Path(root).resolve()
    if root.name == "cookie" and root.parent.parent.name == "profiles":
        data_root, profile = root.parents[2], root.parent.name
    else:
        data_root, profile = root.parent, root.name
    vault = CredentialVault(data_root, keys=FixedKeys(), lock_timeout_seconds=lock_timeout_seconds)
    store = vault.profile(profile)
    filesystem = FileSystem(data_root / "profiles" / profile / "cookie")
    return CookieStore(filesystem, store=store, lock_timeout_seconds=lock_timeout_seconds)
