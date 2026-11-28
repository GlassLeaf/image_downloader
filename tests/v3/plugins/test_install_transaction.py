"""An interrupted upgrade keeps either the installed plugin or its recovery data."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.plugins import make_minimal_plugin

from image_downloader.exceptions import ConfigurationError
from image_downloader.plugins import management
from image_downloader.plugins.install_area import INSTALL_AREA
from image_downloader.plugins.install_transaction import PluginInstallTransaction
from image_downloader.plugins.lifecycle import PluginDiscovery
from image_downloader.plugins.plugin_catalog import PluginCatalog, write_catalog


@pytest.fixture
def upgrade(tmp_path):
    source = make_minimal_plugin((tmp_path / "source").resolve())
    (source / "version.txt").write_text("old")
    root = (tmp_path / "plugins").resolve()
    management.install_plugin(root, source, mode="bypass-all")
    (source / "version.txt").write_text("new")
    return root, source, root / "site_plugins" / source.name


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt, SystemExit])
def test_failed_publication_restores_old_plugin_and_catalog(upgrade, monkeypatch, failure_type):
    root, source, target = upgrade
    before = (root / "catalog.json").read_bytes()
    original = Path.replace
    failure = (
        OSError(errno.EXDEV, "synthetic cross-device publication failure")
        if failure_type is OSError
        else failure_type("publication failed")
    )

    def replace(path, destination):
        if path.name == "candidate":
            raise failure
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(failure_type) as caught:
        management.install_plugin(root, source, mode="bypass-all")
    assert caught.value is failure
    assert (target / "version.txt").read_text() == "old"
    assert (root / "catalog.json").read_bytes() == before
    assert list((target.parent / INSTALL_AREA).iterdir()) == [target.parent / INSTALL_AREA / "owner.json"]


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt, SystemExit])
def test_exception_after_catalog_replace_preserves_committed_upgrade(upgrade, monkeypatch, failure_type):
    root, source, target = upgrade
    original = management._write_catalog_unlocked
    failure = failure_type("after catalog replacement")

    def write(*args):
        original(*args)
        raise failure

    monkeypatch.setattr(management, "_write_catalog_unlocked", write)
    if failure_type is OSError:
        management.install_plugin(root, source, mode="bypass-all")
    else:
        with pytest.raises(failure_type) as caught:
            management.install_plugin(root, source, mode="bypass-all")
        assert caught.value is failure
    assert (target / "version.txt").read_text() == "new"
    assert PluginCatalog.load(root / "catalog.json").find("com.example.minimal") is not None


def test_failed_restore_retains_both_versions_and_blocks_mutations(upgrade, monkeypatch, capsys):
    root, source, target = upgrade
    original = Path.replace
    failure = OSError("original publication failure")

    def replace(path, destination):
        if path.name == "candidate":
            raise failure
        if path.name == ".previous":
            raise OSError("restore failure")
        return original(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", replace)
        with pytest.raises(OSError) as caught:
            management.install_plugin(root, source, mode="bypass-all")
        assert caught.value is failure
    transaction = next(path for path in (target.parent / INSTALL_AREA).iterdir() if path.is_dir())
    assert (transaction / ".previous/version.txt").read_text() == "old"
    assert (transaction / "candidate/version.txt").read_text() == "new"
    assert (transaction / "catalog.before.json").is_file()
    assert json.loads((transaction / "transaction.json").read_text())["phase"] == "recovery_required"
    for change in (
        lambda: management.install_plugin(root, source, mode="bypass-all"),
        lambda: management.trust_plugin(root, source, mode="bypass-all"),
        lambda: management.revoke_plugin(root, "com.example.minimal"),
        lambda: management.uninstall_plugin(root, "com.example.minimal"),
        lambda: write_catalog(root, PluginCatalog(())),
    ):
        with pytest.raises(ConfigurationError, match="manual recovery"):
            change()
    assert "original publication failure" not in capsys.readouterr().err


def test_committed_cleanup_failure_is_warning_and_not_a_failed_install(upgrade, monkeypatch, capsys):
    root, source, target = upgrade

    def fail_cleanup(*args, **kwargs):
        raise PermissionError("private diagnostic")

    monkeypatch.setattr("image_downloader.plugins.install_transaction.shutil.rmtree", fail_cleanup)
    entry = management.install_plugin(root, source, mode="bypass-all")
    assert entry.id == "com.example.minimal"
    assert (target / "version.txt").read_text() == "new"
    discovery = PluginDiscovery(root, mode="bypass-all").discover()
    assert [candidate.manifest.id for candidate in discovery.candidates] == [entry.id]
    assert not discovery.diagnostics
    management.revoke_plugin(root, entry.id)
    assert "private diagnostic" not in capsys.readouterr().err


def test_stage_shares_even_the_kind_directory_filesystem(upgrade, monkeypatch):
    root, source, target = upgrade
    original = Path.replace

    def device(path):
        path = Path(path)
        return "kind" if path.is_relative_to(target.parent) else "root"

    def replace(path, destination):
        if device(path) != device(destination):
            raise OSError(errno.EXDEV, "different synthetic mount")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", replace)
    management.install_plugin(root, source, mode="bypass-all")
    assert (target / "version.txt").read_text() == "new"


@pytest.mark.parametrize("content", ["unknown", "plugin", "bad_record"])
def test_unknown_reserved_contents_are_rejected_without_removal(tmp_path, content):
    root = (tmp_path / "plugins").resolve()
    source = make_minimal_plugin((tmp_path / "source").resolve())
    area = root / "site_plugins" / INSTALL_AREA
    area.mkdir(parents=True)
    (area / "precious.txt").write_text("keep")
    if content != "unknown":
        (area / "owner.json").write_text(json.dumps({"schema_version": 1, "owner": "image-downloader-plugin-install"}))
        if content == "plugin":
            (area / "manifest.json").write_bytes((source / "manifest.json").read_bytes())
    with pytest.raises(ConfigurationError):
        management.install_plugin(root, source, mode="bypass-all")
    assert (area / "precious.txt").read_text() == "keep"


def test_only_reserved_area_is_excluded_not_all_hidden_plugins(tmp_path):
    root = (tmp_path / "plugins").resolve()
    source = make_minimal_plugin(root)
    source.rename(source.with_name(".hidden-plugin"))
    with management._catalog_lock(root):
        transaction = PluginInstallTransaction(root, root / "site_plugins", "test.pending")
    try:
        result = PluginDiscovery(root, mode="bypass-all").discover()
        assert [item.manifest.id for item in result.candidates] == ["com.example.minimal"]
    finally:
        transaction.close()


def test_process_exit_after_new_plugin_publication_keeps_recovery_data(upgrade):
    root, source, target = upgrade
    script = """
import os, sys
from pathlib import Path
from image_downloader.plugins.management import install_plugin
original = Path.replace
def replace(path, destination):
    result = original(path, destination)
    if path.name == 'candidate':
        os._exit(73)
    return result
Path.replace = replace
install_plugin(Path(sys.argv[1]), Path(sys.argv[2]), mode='bypass-all')
"""
    process = subprocess.run([sys.executable, "-c", script, str(root), str(source)], capture_output=True, timeout=30)
    assert process.returncode == 73, process.stderr.decode()
    transaction = next(path for path in (target.parent / INSTALL_AREA).iterdir() if path.is_dir())
    assert (target / "version.txt").read_text() == "new"
    assert (transaction / ".previous/version.txt").read_text() == "old"
    assert json.loads((transaction / "transaction.json").read_text())["phase"] == "publishing"
    with pytest.raises(ConfigurationError, match="manual recovery"):
        management.install_plugin(root, source, mode="bypass-all")


def test_parallel_preparations_merge_both_catalog_entries(tmp_path):
    root = (tmp_path / "plugins").resolve()
    sources = [
        make_minimal_plugin((tmp_path / str(index)).resolve(), plugin_id=f"test.parallel{index}") for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        entries = list(pool.map(lambda source: management.install_plugin(root, source, mode="bypass-all"), sources))
    assert {entry.id for entry in PluginCatalog.load(root / "catalog.json").entries} == {entry.id for entry in entries}
    result = PluginDiscovery(root, mode="bypass-all").discover()
    assert len(result.candidates) == 2
    assert not result.diagnostics


def test_fatal_cleanup_preserves_committed_plugin(upgrade, monkeypatch):
    root, source, target = upgrade
    failure = KeyboardInterrupt("cleanup interrupted")

    def interrupt(*args, **kwargs):
        raise failure

    monkeypatch.setattr("image_downloader.plugins.install_transaction.shutil.rmtree", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        management.install_plugin(root, source, mode="bypass-all")
    assert caught.value is failure
    assert (target / "version.txt").read_text() == "new"
    assert PluginCatalog.load(root / "catalog.json").find("com.example.minimal") is not None


def test_unknown_transaction_content_is_retained_and_blocks_mutation(upgrade):
    root, source, target = upgrade
    with management._catalog_lock(root):
        transaction = PluginInstallTransaction(root, target.parent, "test.unknown")
    precious = transaction.root / "unknown.txt"
    precious.write_text("keep")
    transaction.close()
    with pytest.raises(ConfigurationError, match="metadata is invalid"):
        management.install_plugin(root, source, mode="bypass-all")
    assert precious.read_text() == "keep"
    assert (target / "version.txt").read_text() == "old"


def test_reparse_install_area_is_rejected_without_cleanup(upgrade, monkeypatch):
    root, source, target = upgrade
    area = target.parent / INSTALL_AREA
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        state = original(path, *args, **kwargs)
        if path == area:
            return SimpleNamespace(st_mode=state.st_mode, st_file_attributes=0x400)
        return state

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ConfigurationError, match="reparse point"):
        management.install_plugin(root, source, mode="bypass-all")
    assert (area / "owner.json").is_file()
    assert (target / "version.txt").read_text() == "old"
