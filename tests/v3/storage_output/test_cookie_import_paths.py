"""Cookie input paths are validated before any decryption error conversion."""

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.vault import cookie_store

from image_downloader.exceptions import AuthenticationError, ConfigurationError, StorageError, StorageSafetyError
from image_downloader.storage.cookies import _deserialize


@pytest.fixture
def exported(tmp_path):
    source = cookie_store(tmp_path / "source")
    source.save(_deserialize(json.dumps([{"name": "imported", "value": "artificial", "domain": "example.test"}])))
    path = tmp_path / "input" / "cookies.enc"
    source.export_file(path, "test-passphrase")
    return path


@pytest.mark.parametrize("kind", ["absolute", "relative", "nested"])
def test_cookie_import_resolves_paths_and_merges(exported, tmp_path, monkeypatch, kind):
    target = cookie_store(tmp_path / "target")
    target.save(_deserialize(json.dumps([{"name": "existing", "value": "keep", "domain": "other.test"}])))
    if kind == "relative":
        monkeypatch.chdir(exported.parent)
        path = Path(exported.name)
    elif kind == "nested":
        monkeypatch.chdir(tmp_path)
        path = Path("input") / exported.name
    else:
        path = exported
    target.import_file(path, "test-passphrase")
    assert {cookie.name for cookie in target.load()} == {"existing", "imported"}


@pytest.mark.parametrize("kind", ["missing", "parent", "corrupt", "password", "hardlink", "link"])
def test_cookie_import_classifies_errors_and_preserves_target(exported, tmp_path, monkeypatch, kind):
    target = cookie_store(tmp_path / "target")
    target.save(_deserialize("[]"))
    before = target.path.read_bytes()
    path, password = exported, "test-passphrase"
    error = AuthenticationError
    if kind == "missing":
        path, error = tmp_path / "missing", StorageError
    elif kind == "parent":
        monkeypatch.chdir(exported.parent)
        path, error = Path("..") / "input" / exported.name, ConfigurationError
    elif kind == "corrupt":
        exported.write_bytes(b"corrupt")
    elif kind == "password":
        password = "incorrect"
    elif kind == "hardlink":
        (tmp_path / "alias").hardlink_to(exported)
        error = StorageSafetyError
    elif kind == "link":
        path = tmp_path / "link.enc"
        try:
            path.symlink_to(exported)
        except OSError:
            pytest.skip("symbolic links unavailable")
        error = ConfigurationError
    with pytest.raises(error):
        target.import_file(path, password)
    assert target.path.read_bytes() == before


def test_cookie_import_rejects_reparse_point_before_reading(exported, tmp_path, monkeypatch):
    target = cookie_store(tmp_path / "target")
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == exported:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ConfigurationError, match="reparse point"):
        target.import_file(exported, "test-passphrase")


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("traversal_first", [False, True])
def test_missing_component_cannot_hide_parent_traversal(exported, tmp_path, monkeypatch, relative, traversal_first):
    target = cookie_store(tmp_path / "target")
    target.save(_deserialize("[]"))
    before = target.path.read_bytes()
    suffix = Path("..") / "missing" / exported.name if traversal_first else Path("missing") / ".." / exported.name
    path = exported.parent / suffix
    if relative:
        monkeypatch.chdir(exported.parent)
        path = suffix
    with pytest.raises(ConfigurationError, match="relative path components"):
        target.import_file(path, "test-passphrase")
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("missing", [False, True])
def test_cookie_import_cli_relative_path_and_storage_status(exported, tmp_path, monkeypatch, capsys, missing):
    import image_downloader.commands.cookie as command
    from image_downloader.cli import main

    target = cookie_store(tmp_path / "target")
    monkeypatch.setattr(
        command.CredentialVault, "from_config", lambda config: SimpleNamespace(profile=lambda _: target.store)
    )
    monkeypatch.setattr(command.getpass, "getpass", lambda _: "test-passphrase")
    monkeypatch.setattr(command, "_persist_initial_user_config", lambda _: None)
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"storage": {"data_root": str(tmp_path)}, "profile": {"default": "target"}}))
    monkeypatch.chdir(tmp_path)
    code = main(
        ["cookie", "import", "missing.enc" if missing else "input/cookies.enc", "--config", str(config), "--json"]
    )
    output = capsys.readouterr()
    assert code == (1 if missing else 0), output
    if missing:
        assert "storage_error" in output.out + output.err
        assert "authentication_error" not in output.out + output.err
    else:
        assert [cookie.name for cookie in target.load()] == ["imported"]
