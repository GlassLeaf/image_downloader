"""Credential CLI end-to-end behavior using only artificial master keys."""

import io
import json
import sys

import pytest
from support.vault import MemoryKeys

from image_downloader.cli import build_parser, main
from image_downloader.commands.setup import _config_for
from image_downloader.commands.validation import _option_has_effect, validate_arguments
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.credentials.vault import CredentialVault
from image_downloader.plugins.runtime import PluginRuntime


@pytest.fixture(params=[False, True], ids=["plain", "plugin-options"])
def cli_vault(tmp_path, monkeypatch, request):
    keys = MemoryKeys()
    monkeypatch.setattr(MasterKeyStore, "get", lambda self, v, k: keys.get(v, k))
    monkeypatch.setattr(MasterKeyStore, "create", lambda self, v, k, value: keys.create(v, k, value))
    monkeypatch.setattr(MasterKeyStore, "delete", lambda self, v, k: keys.delete(v, k))
    config = tmp_path / "app.yaml"
    data = tmp_path / "data"
    config.write_text(json.dumps({"storage": {"data_root": str(data)}, "logging": {"console": {"enabled": False}}}))
    for name in ("one", "two"):
        profile = tmp_path / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "app.yaml").write_text("{}", encoding="utf-8")
    options = ["--config", str(config)]
    if request.param:
        options += ["--plugin-root", str(tmp_path / "plugins"), "--plugin-verification-override", "bypass-all"]

        def unexpected(*args, **kwargs):
            pytest.fail("credential and vault operations must not initialize the plugin runtime")

        monkeypatch.setattr(PluginRuntime, "__init__", unexpected)
    return [*options, "--json"], data, keys


def test_cli_profile_crud_and_safe_output(cli_vault, monkeypatch, capsys):
    options, data, keys = cli_vault
    monkeypatch.setattr(sys, "stdin", io.StringIO("fictional-cli-canary"))
    assert main(["credential", "plugin", "set", "a.b", "TOKEN", "--stdin", "--profile", "one", *options]) == 0
    assert main(["credential", "plugin", "list", "--profile", "two", *options]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["execution"]["result"]["entries"] == []
    assert main(["credential", "plugin", "list", "--profile", "one", *options]) == 0
    assert "fictional-cli-canary" not in capsys.readouterr().out
    assert len(keys.values) == 1
    assert main(["credential", "plugin", "delete", "a.b", "TOKEN", "--profile", "one", "--yes", *options]) == 0
    assert CredentialVault(data).profile("one").list_entries("plugin") == []


def test_cli_smtp_and_environment_is_unused(cli_vault, monkeypatch, capsys):
    options, data, _ = cli_vault
    monkeypatch.setenv("IMAGE_DOWNLOADER_SMTP_PASSWORD", "unused-env-canary")
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"username":"user-canary","password":"password-canary"}'))
    assert main(["credential", "smtp", "set", "MAIL", "--stdin", *options]) == 0
    assert main(["credential", "smtp", "list", *options]) == 0
    output = capsys.readouterr()
    assert "user-canary" not in output.out + output.err
    assert "password-canary" not in output.out + output.err
    assert CredentialVault(data).profile("default").get_smtp("MAIL").password == "password-canary"


def test_cli_backup_restore_and_rotation(cli_vault, tmp_path, monkeypatch, capsys):
    options, data, keys = cli_vault
    assert main(["vault", "status", *options]) == 0
    assert main(["vault", "init", *options]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("secret-canary"))
    assert main(["credential", "plugin", "set", "a.b", "TOKEN", "--stdin", *options]) == 0
    backup = tmp_path / "vault.backup"
    monkeypatch.setattr(sys, "stdin", io.StringIO("passphrase\n"))
    assert main(["vault", "backup", str(backup), "--passphrase-stdin", *options]) == 0
    assert main(["vault", "rotate-key", *options]) == 0
    restored = tmp_path / "restored"
    monkeypatch.setattr(sys, "stdin", io.StringIO("passphrase\n"))
    assert main(["vault", "restore", str(backup), "--data-root", str(restored), "--passphrase-stdin", *options]) == 0
    assert CredentialVault(restored).profile("default").get_plugin("a.b", "TOKEN") == "secret-canary"
    assert len(keys.values) == 2
    output = capsys.readouterr()
    assert "secret-canary" not in output.out + output.err
    assert "passphrase" not in output.out + output.err


def test_cli_smtp_delete(cli_vault, monkeypatch, capsys):
    options, data, _ = cli_vault
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"username":"user","password":"password-canary"}'))
    assert main(["credential", "smtp", "set", "MAIL", "--stdin", *options]) == 0
    assert main(["credential", "smtp", "delete", "MAIL", "--yes", *options]) == 0
    assert CredentialVault(data).profile("default").list_entries("smtp") == []
    output = capsys.readouterr()
    assert "password-canary" not in output.out + output.err


_MANAGED_COMMANDS = [
    ["credential", kind, action] + ([] if action == "list" else (["a.b", "TOKEN"] if kind == "plugin" else ["MAIL"]))
    for kind in ("plugin", "smtp")
    for action in ("set", "list", "delete")
] + [
    ["vault", action] + (["backup.enc"] if action in {"backup", "restore"} else [])
    for action in ("init", "status", "backup", "restore", "rotate-key")
]


@pytest.mark.parametrize("words", _MANAGED_COMMANDS)
@pytest.mark.parametrize("position", ["before", "within", "after"])
@pytest.mark.parametrize("mode", [None, "bypass-all", "bypass-catalog", "bypass-signature"])
@pytest.mark.parametrize("with_root", [False, True])
def test_managed_plugin_options_are_prepared(tmp_path, words, position, mode, with_root):
    options = ["--plugin-root", str(tmp_path / "plugins")] if with_root else []
    if mode is not None:
        options += ["--plugin-verification-override", mode]
    split = {"before": 0, "within": 1, "after": len(words)}[position]
    args = build_parser().parse_args([*words[:split], *options, *words[split:]])
    command = validate_arguments(args)
    assert command.arguments.plugin_root == (tmp_path / "plugins" if with_root else None)
    assert command.arguments.plugin_verification_override == mode
    path = " ".join(words[:3] if words[0] == "credential" else words[:2])
    assert not _option_has_effect("plugin-verification-override", args, path)


@pytest.mark.parametrize("words", _MANAGED_COMMANDS)
@pytest.mark.parametrize("invalid", ["mode", "relative-root", "file-root"])
def test_invalid_plugin_options_fail_before_vault_access(tmp_path, monkeypatch, capsys, words, invalid):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid options must not access the vault")

    monkeypatch.setattr(CredentialVault, "from_config", unexpected)
    root_file = tmp_path / "plugins-file"
    root_file.write_text("not a directory", encoding="utf-8")
    option = "--plugin-verification-override" if invalid == "mode" else "--plugin-root"
    value = {"mode": "invalid", "relative-root": "relative/plugins", "file-root": str(root_file)}[invalid]
    assert main([*words, option, value, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["execution"]["error"]
    assert error["details"]["kind"] == "invalid_value"
    assert error["details"]["option"] == option


def test_managed_plugin_root_overrides_config_without_persistence(cli_vault, tmp_path):
    options, _, _ = cli_vault
    config_path = tmp_path / "app.yaml"
    original = config_path.read_bytes()
    root = tmp_path / "selected-plugins"
    args = validate_arguments(build_parser().parse_args(["vault", "status", *options, "--plugin-root", str(root)]))
    config, _, _, _ = _config_for(args.arguments, None)
    assert config.plugins.root == str(root)
    assert config.security.plugin_verification == "strict"
    assert config_path.read_bytes() == original
    assert not root.exists()


def test_status_and_help_do_not_initialize(cli_vault, capsys):
    options, data, keys = cli_vault
    assert main(["vault", "status", *options]) == 0
    assert not data.exists() and not keys.values
    assert main(["credential", "plugin", "set", "a.b", "TOKEN", *options]) == 2
    assert not data.exists() and not keys.values
    capsys.readouterr()


def test_key_loss_has_specific_json_error_and_preserves_data(cli_vault, monkeypatch, capsys):
    options, data, keys = cli_vault
    monkeypatch.setattr(sys, "stdin", io.StringIO("secret-canary"))
    assert main(["credential", "plugin", "set", "a.b", "TOKEN", "--stdin", *options]) == 0
    capsys.readouterr()
    before = (data / "profiles/default/credentials/credentials.enc").read_bytes()
    keys.values.clear()
    assert main(["vault", "init", *options]) == 3
    output = capsys.readouterr()
    assert "vault_key_missing" in output.out
    assert "secret-canary" not in output.out + output.err
    assert (data / "profiles/default/credentials/credentials.enc").read_bytes() == before
    assert not keys.values


def test_text_status_and_list_render_identifiers_only(cli_vault, monkeypatch, capsys):
    options, _, _ = cli_vault
    options = options[:-1]
    assert main(["vault", "status", *options]) == 0
    assert "uninitialized" in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", io.StringIO("secret-canary"))
    assert main(["credential", "plugin", "set", "a.b", "TOKEN", "--stdin", *options]) == 0
    assert main(["credential", "plugin", "list", *options]) == 0
    output = capsys.readouterr()
    assert "TOKEN" in output.out and "a.b" in output.out
    assert "secret-canary" not in output.out + output.err


@pytest.mark.parametrize("json_output", [True, False])
def test_status_config_directory_has_specific_error_before_vault_access(tmp_path, monkeypatch, capsys, json_output):
    config_root = tmp_path / "config root private-canary"
    config_root.mkdir()
    data_root = tmp_path / "working test" / "data"

    def unexpected(*args, **kwargs):
        pytest.fail("invalid configuration must not access the vault or OS store")

    monkeypatch.setattr(CredentialVault, "from_config", unexpected)
    monkeypatch.setattr(MasterKeyStore, "get", unexpected)
    monkeypatch.setattr(MasterKeyStore, "create", unexpected)
    monkeypatch.setattr(MasterKeyStore, "delete", unexpected)
    options = ["--json"] if json_output else []
    assert main(["vault", "status", "--data-root", str(data_root), "--config", str(config_root), *options]) == 2
    captured = capsys.readouterr()
    assert "--config must point to a configuration file, not a directory" in captured.out + captured.err
    assert "private-canary" not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err
    if json_output:
        error = json.loads(captured.out)["execution"]["error"]
        assert error["exception"] == "ConfigurationError"
        assert error["details"] == {"kind": "configuration_not_file", "option": "--config"}
        assert captured.err == ""
    assert not data_root.exists()
    assert list(config_root.iterdir()) == []


def test_manual_status_with_space_containing_file_paths_does_not_create_vault(tmp_path, monkeypatch, capsys):
    config_root = tmp_path / "config root"
    config_root.mkdir()
    config_file = config_root / "app.yaml"
    config_file.write_text("{}\n", encoding="utf-8")
    data_root = tmp_path / "working test" / "data"

    def unexpected(*args, **kwargs):
        pytest.fail("uninitialized status must not access the OS store")

    monkeypatch.setattr(MasterKeyStore, "get", unexpected)
    monkeypatch.setattr(MasterKeyStore, "create", unexpected)
    monkeypatch.setattr(MasterKeyStore, "delete", unexpected)
    assert main(["vault", "status", "--data-root", str(data_root), "--config", str(config_file), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["execution"]["result"]
    assert result == {"state": "uninitialized"}
    assert config_file.read_text(encoding="utf-8") == "{}\n"
    assert not data_root.exists()


@pytest.mark.parametrize("rotation_completed", [False, True])
def test_resume_without_journal_reports_no_rotation_without_changing_vault(cli_vault, capsys, rotation_completed):
    options, data, keys = cli_vault
    assert main(["vault", "init", *options]) == 0
    if rotation_completed:
        assert main(["vault", "rotate-key", *options]) == 0
    capsys.readouterr()
    before_state = CredentialVault(data).status()
    before_metadata = (data / "security/vault.json").read_bytes()
    before_keys = dict(keys.values)
    before_calls = (list(keys.created), list(keys.deleted))

    assert main(["vault", "rotate-key", "--resume", *options]) == 3

    output = capsys.readouterr()
    error = json.loads(output.out)["execution"]["error"]
    assert error["code"] == "vault_no_rotation"
    assert error["message"] == "credential vault has no key rotation to resume"
    assert "incomplete" not in error["message"]
    assert error["exception"] == "VaultError"
    assert not output.err
    assert CredentialVault(data).status() == before_state
    assert before_state["state"] == "ready"
    assert (data / "security/vault.json").read_bytes() == before_metadata
    assert keys.values == before_keys
    assert (keys.created, keys.deleted) == before_calls
    assert not (data / "security/rotation.enc").exists()
