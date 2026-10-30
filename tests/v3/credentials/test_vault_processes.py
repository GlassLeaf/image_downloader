"""Spawned processes use a shared artificial keyring; no OS secrets are touched."""

from __future__ import annotations

import json
import multiprocessing
import os

import pytest

from image_downloader.credentials import CredentialVault
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore, _cookie


class SharedKeys:
    def __init__(self, values):
        self.values = values

    def get(self, vault, key):
        return self.values.get((vault, key))

    def create(self, vault, key, value):
        assert (vault, key) not in self.values
        self.values[vault, key] = value

    def delete(self, vault, key):
        self.values.pop((vault, key), None)


def _write_profile(root, values, start, profile, reference):
    vault = CredentialVault(root, keys=SharedKeys(values))
    start.wait(20)
    vault.profile(profile).set_plugin("a.b", reference, "canary-" + reference)
    store = CookieStore(FileSystem(root / "profiles" / profile / "cookie"), store=vault.profile(profile))
    jar = store.load()
    before = store.snapshot(jar)
    jar.set_cookie(_cookie(reference, "cookie-canary", domain="example.test"))
    store.persist_delta(before, jar)


def _stale_writer(root, values, ready, rotated):
    vault = CredentialVault(root, keys=SharedKeys(values))
    store = vault.profile("a")
    assert store.get_plugin("a.b", "TOKEN") == "before"
    ready.set()
    assert rotated.wait(30)
    store.set_plugin("a.b", "TOKEN", "after")


def _crash(root, values, operation, boundary, backup):
    vault = CredentialVault(root, keys=SharedKeys(values))
    write = vault._write_json
    create = vault.keys.create

    def crashing_write(path, value, *args):
        write(path, value, *args)
        if path.name == "vault.json":
            hit = value["state"] == boundary
        elif path.name == "rotation.enc":
            hit = vault._rotation(vault._meta())["phase"] == boundary
        else:
            hit = boundary == "file"
        if hit:
            os._exit(71)

    def crashing_create(*args):
        create(*args)
        if boundary == "key":
            os._exit(71)

    vault._write_json = crashing_write
    vault.keys.create = crashing_create
    if operation == "initialize":
        vault.initialize()
    elif operation == "restore":
        vault.restore(backup, "passphrase")
    else:
        vault.rotate_key()


def _join(process, expected=0):
    process.join(40)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("artificial vault worker did not finish")
    assert process.exitcode == expected


@pytest.mark.parametrize("profiles", [("a", "b"), ("a", "a")])
def test_concurrent_initialization_credentials_and_cookie_deltas(tmp_path, profiles):
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        values = manager.dict()
        gate = context.Event()
        root = tmp_path / "data"
        processes = [
            context.Process(target=_write_profile, args=(root, values, gate, profile, reference))
            for profile, reference in zip(profiles, ("FIRST", "SECOND"), strict=True)
        ]
        for process in processes:
            process.start()
        gate.set()
        for process in processes:
            _join(process)
        vault = CredentialVault(root, keys=SharedKeys(values))
        assert len(values) == 1
        for profile, reference in zip(profiles, ("FIRST", "SECOND"), strict=True):
            assert vault.profile(profile).get_plugin("a.b", reference) == "canary-" + reference
            store = CookieStore(FileSystem(root / "profiles" / profile / "cookie"), store=vault.profile(profile))
            expected = {"FIRST", "SECOND"} if profiles[0] == profiles[1] else {reference}
            assert {cookie.name for cookie in store.load()} == expected


def test_running_process_refreshes_master_after_rotation(tmp_path):
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        values = manager.dict()
        root = tmp_path / "data"
        vault = CredentialVault(root, keys=SharedKeys(values))
        vault.profile("a").set_plugin("a.b", "TOKEN", "before")
        ready, rotated = context.Event(), context.Event()
        process = context.Process(target=_stale_writer, args=(root, values, ready, rotated))
        process.start()
        assert ready.wait(30)
        vault.rotate_key()
        rotated.set()
        _join(process)
        path = root / "profiles/a/credentials/credentials.enc"
        assert json.loads(path.read_bytes())["key_id"] == vault.status()["key_id"]
        assert vault.profile("a").get_plugin("a.b", "TOKEN") == "after"
        assert len(values) == 1


@pytest.mark.parametrize(
    "operation,boundary",
    [
        ("initialize", "initializing"),
        ("initialize", "key"),
        ("initialize", "ready"),
        ("restore", "restoring"),
        ("restore", "key"),
        ("restore", "file"),
        ("restore", "ready"),
        ("rotate", "prepared"),
        ("rotate", "key"),
        ("rotate", "rewrapping"),
        ("rotate", "file"),
        ("rotate", "committed"),
    ],
)
def test_forced_exit_at_durable_boundaries_can_resume(tmp_path, operation, boundary):
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        values = manager.dict()
        root = tmp_path / "data"
        backup = tmp_path / "backup.enc"
        vault = CredentialVault(root, keys=SharedKeys(values))
        if operation != "initialize":
            vault.profile("a").set_plugin("a.b", "TOKEN", "canary")
        if operation == "restore":
            vault.backup(backup, "passphrase")
            root = tmp_path / "restored"
            vault = CredentialVault(root, keys=SharedKeys(values))
        process = context.Process(target=_crash, args=(root, values, operation, boundary, backup))
        process.start()
        _join(process, 71)
        created = len(values)
        if operation == "initialize":
            vault.initialize()
        elif operation == "restore":
            if boundary == "ready":
                assert vault.status()["state"] == "ready"
            else:
                vault.restore(backup, "passphrase")
        else:
            vault.rotate_key(resume=True)
        assert vault.status()["state"] == "ready"
        if operation != "initialize":
            assert vault.profile("a").get_plugin("a.b", "TOKEN") == "canary"
        if operation == "rotate":
            assert len(values) == 1
        elif boundary != "initializing" and boundary != "restoring":
            assert len(values) == created
