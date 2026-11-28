from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from image_downloader import AppConfig
from image_downloader.application.requests import InspectRequest
from image_downloader.commands import dispatch
from image_downloader.configuration.document_layers import INCOMPLETE_MARKER
from image_downloader.configuration.inventory import validate_tree
from image_downloader.configuration.layers import resolve_application_config
from image_downloader.configuration.migration import apply_migration, plan_migration
from image_downloader.credentials.access import resolve_auth_access
from image_downloader.credentials.overrides import operation_access
from image_downloader.exceptions import ConfigurationError

PLUGIN = "com.example.gallery"


def write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def binding(host="example.com", **values):
    return {"origins": ["https://" + host], "credential_access": "deny", "login": "deny", **values}


def main(root):
    return write(root / "app.yaml", {"config_version": 2})


def auth(root, value):
    return write(root / "auth.yaml", {"plugins": {PLUGIN: value}})


def test_three_families_interleave_host_layers_and_preserve_partial_author_policy(tmp_path):
    path = main(tmp_path)
    write(path, {"config_version": 2, "network": {"request_concurrency": 8}})
    write(tmp_path / "plugin-settings" / (PLUGIN + ".yaml"), {"config": {"retained": 1, "value": 0}})
    auth(
        tmp_path,
        {
            "bindings": {"shared": binding()},
            "connections": {"authentication": {"verify_before_request": True}, "endpoints": {"api": {"auth": "none"}}},
        },
    )
    scopes = [
        tmp_path / "profiles/default",
        tmp_path / "sites/global",
        tmp_path / "profiles/default/sites/global",
        tmp_path / "sites/example.com",
        tmp_path / "profiles/default/sites/example.com",
        tmp_path / "sites/a.example.com",
    ]
    for index, scope in enumerate(scopes, 1):
        write(scope / "app.yaml", {"network": {"request_concurrency": index, "headers": {"User-Agent": str(index)}}})
        write(scope / "plugin-settings" / (PLUGIN + ".yaml"), {"config": {"value": index}})
    auth(scopes[-1], {"connections": {"endpoints": {"api": {"origin": "https://cdn.example.org"}}}})
    config = resolve_application_config(path, site="A.example.com.").config
    assert config.network.request_concurrency == 6
    assert config.network.headers["User-Agent"] == "6"
    setting = config.plugin_settings[PLUGIN]
    assert setting.config == {"retained": 1, "value": 6}
    assert setting.endpoints["api"] == {"auth": "none", "origin": "https://cdn.example.org"}
    assert setting.authentication.model_dump(exclude_unset=True) == {"verify_before_request": True}


@pytest.mark.parametrize(
    "value",
    [
        {"bindings": {"x": binding()}},
        {"defaults": {"session": {"persistence": "ephemeral"}}},
        {"host_groups": {"sso": ["example.com"]}},
    ],
)
def test_site_auth_rejects_permission_fields(tmp_path, value):
    path = main(tmp_path)
    auth(tmp_path / "sites/global", value)
    with pytest.raises(ConfigurationError, match="site layers"):
        resolve_application_config(path)


def test_atomic_binding_replacement_deletion_and_late_session_inheritance(tmp_path):
    path = main(tmp_path)
    auth(
        tmp_path, {"bindings": {"x": binding(credentials={"password": "PRIVATE_REF"}), "remove": binding("other.com")}}
    )
    profile = tmp_path / "profiles/default"
    auth(
        profile,
        {"defaults": {"session": {"persistence": "ephemeral"}}, "bindings": {"x": binding("new.com"), "remove": None}},
    )
    config = resolve_application_config(path).config
    access = config.plugin_settings[PLUGIN].auth_access
    assert set(access.sites) == {"x"} and not access.sites["x"].secrets
    assert resolve_auth_access(PLUGIN, "https://new.com", access).site.mode == "anonymous"
    assert resolve_auth_access(PLUGIN, "https://unknown.com", access).site.mode == "anonymous"


def test_namespaces_are_independent_and_legacy_api_still_shares_groups(tmp_path):
    path = main(tmp_path)
    auth(
        tmp_path,
        {
            "host_groups": {"sso": ["example.com"]},
            "bindings": {"x": binding("${host_groups.sso}")},
            "connections": {"host_groups": {"sso": ["api.other.com"]}},
        },
    )
    config = resolve_application_config(path).config
    setting = config.plugin_settings[PLUGIN]
    assert (
        resolve_auth_access(PLUGIN, "https://example.com", setting.auth_access, host_groups=setting.host_groups).site_id
        == "x"
    )
    auth(
        tmp_path,
        {"bindings": {"x": binding("${host_groups.sso}")}, "connections": {"host_groups": {"sso": ["api.other.com"]}}},
    )
    with pytest.raises(ConfigurationError, match="not defined"):
        resolve_application_config(path)
    old = AppConfig.model_validate(
        {
            "plugin_settings": {
                PLUGIN: {
                    "host_groups": {"sso": ["example.com"]},
                    "auth_access": {"sites": {"x": {"origins": "https://${host_groups.sso}"}}},
                }
            }
        }
    ).plugin_settings[PLUGIN]
    assert (
        resolve_auth_access(PLUGIN, "https://example.com", old.auth_access, host_groups=old.host_groups).site_id == "x"
    )


def test_binding_replacement_then_selection_then_patch_and_unregistered_default():
    base = {"sites": {"x": {"origins": ["https://example.com"], "secrets": {"password": "PRIVATE_REF"}}}}
    result = operation_access(
        PLUGIN,
        "https://other.com",
        base,
        legacy=None,
        host_groups={},
        replacements={PLUGIN: {"x": binding("other.com", login="allow")}},
        patches={PLUGIN: {"session": {"persistence": "ephemeral"}}},
    )
    assert result.site_id == "x" and result.site.login_policy == "allow"
    assert result.site.mode == "anonymous" and not result.site.secrets
    fallback = operation_access(
        PLUGIN,
        "https://unknown.com",
        base,
        legacy=None,
        host_groups={},
        replacements=None,
        patches={PLUGIN: {"credential_access": "allow"}},
    )
    assert fallback.site.login_policy == "deny" and fallback.site.provides_credentials
    assert base["sites"]["x"]["secrets"] == {"password": "PRIVATE_REF"}
    with pytest.raises(ConfigurationError, match="cannot be combined"):
        InspectRequest(
            url="https://example.com",
            plugin_auth_access_overrides={PLUGIN: {}},
            plugin_auth_access_patches={PLUGIN: {}},
        )
    with pytest.raises(ConfigurationError, match="selected site plugin"):
        operation_access(
            PLUGIN,
            "https://example.com",
            base,
            legacy=None,
            replacements=None,
            patches={"another.plugin": {}},
            host_groups={},
        )


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"config_version": True},
        {"config_version": "2"},
        {"config_version": 2, "unknown": True},
        {"config_version": 2, "plugin_settings": {}},
        {"config_version": 2, "network": {"auth_refresh_attempts": 2}},
    ],
)
def test_old_or_misplaced_unknown_settings_never_rewrite(tmp_path, value):
    path = write(tmp_path / "app.yaml", value)
    before = path.read_bytes()
    with pytest.raises(ConfigurationError):
        resolve_application_config(path, rewrite_user_layers=True)
    assert path.read_bytes() == before


def test_partial_layers_validate_values_but_defer_inherited_network_consistency(tmp_path):
    path = write(
        tmp_path / "app.yaml",
        {"config_version": 2, "network": {"pool_max_connections": 3, "pool_max_idle_connections": 3}},
    )
    write(tmp_path / "profiles/default/app.yaml", {"network": {"pool_max_connections": 4}})
    assert resolve_application_config(path).config.network.pool_max_connections == 4
    write(path, {"config_version": 2, "network": {"request_concurrency": 0}})
    write(tmp_path / "profiles/default/app.yaml", {"network": {"request_concurrency": 4}})
    with pytest.raises(ConfigurationError):
        resolve_application_config(path)


def test_validate_all_profiles_and_sites_no_plugin_execution_or_mutation(tmp_path):
    path = main(tmp_path)
    write(tmp_path / "profiles/work/app.yaml", {})
    auth(tmp_path / "profiles/work/sites/example.com", {"bindings": {"bad": binding()}})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
    with pytest.raises(ConfigurationError):
        validate_tree(path)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")} == before


def test_migrate_dry_run_and_apply_preserve_explicit_values_templates_and_source(tmp_path):
    source = write(
        tmp_path / "old/app.yaml",
        {
            "network": {"request_concurrency": 8, "auth_refresh_attempts": 2},
            "notification": {"email": {"credential_reference": "SMTP_PRIVATE"}},
            "plugin_settings": {
                PLUGIN: {
                    "enabled": True,
                    "config": {"value": 1},
                    "host_groups": {"sso": ["example.com"]},
                    "auth_access": {
                        "sites": {
                            "x": {"origins": "https://${host_groups.sso}", "secrets": {"password": "PRIVATE_REF"}}
                        }
                    },
                    "endpoints": {"api": {"origin": "https://${config.api_host}", "auth": "none"}},
                }
            },
        },
    )
    write(source.parent / "sites/example.com.yaml", {"network": {"headers": {"User-Agent": "special"}}})
    original = {p: p.read_bytes() for p in source.parent.rglob("*.yaml")}
    destination = tmp_path / "new"
    plan = plan_migration(source, destination)
    assert not destination.exists()
    assert "PRIVATE" not in json.dumps(plan.report())
    apply_migration(plan)
    assert {p: p.read_bytes() for p in source.parent.rglob("*.yaml")} == original
    assert not (destination / INCOMPLETE_MARKER).exists()
    assert yaml.safe_load((destination / "app.yaml").read_text())["network"]["request_concurrency"] == 8
    converted = resolve_application_config(destination / "app.yaml", site="example.com").config
    assert converted.network.headers["User-Agent"] == "special"
    assert converted.network.auth_refresh_attempts == 2
    assert converted.plugin_settings[PLUGIN].endpoints["api"]["origin"] == "https://${config.api_host}"


def test_migration_refuses_host_grants_existing_dest_and_source_changes(tmp_path):
    source = write(tmp_path / "old/app.yaml", {})
    site = write(
        source.parent / "sites/example.com.yaml", {"plugin_settings": {PLUGIN: {"auth_access": {"mode": "anonymous"}}}}
    )
    with pytest.raises(ConfigurationError, match="host-dependent"):
        plan_migration(source, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    site.unlink()
    plan = plan_migration(source, tmp_path / "new")
    source.write_text("network: {max_attempts: 2}\n")
    with pytest.raises(ConfigurationError, match="source changed"):
        apply_migration(plan)
    assert not (tmp_path / "new").exists()
    with pytest.raises(ConfigurationError, match="disjoint"):
        plan_migration(source, source.parent / "new")
    (tmp_path / "new").mkdir()
    with pytest.raises(ConfigurationError, match="must not exist"):
        plan_migration(source, tmp_path / "new")


def test_incomplete_output_refused_and_diagnostic_redacts_proxy_headers_references(tmp_path, capsys):
    path = write(
        tmp_path / "app.yaml",
        {
            "config_version": 2,
            "network": {"proxy": "http://name:PRIVATE_PROXY@localhost:8080", "headers": {"X-Value": "PRIVATE_HEADER"}},
        },
    )
    write(tmp_path / "auth.yaml", {"smtp": {"credential_reference": "PRIVATE_SMTP"}})
    auth_value = {"bindings": {"x": binding(credentials={"password": "PRIVATE_REF"})}}
    write(tmp_path / "auth.yaml", {"plugins": {PLUGIN: auth_value}, "smtp": {"credential_reference": "PRIVATE_SMTP"}})
    assert (
        dispatch.main(
            [
                "config",
                "explain",
                "--config",
                str(path),
                "--json",
                "--data-root",
                str(tmp_path / "data"),
                "--plugin-root",
                str(tmp_path / "plugins"),
            ]
        )
        == 0
    )
    assert "PRIVATE_" not in capsys.readouterr().out
    (tmp_path / INCOMPLETE_MARKER).write_text("incomplete")
    with pytest.raises(ConfigurationError, match="incomplete"):
        resolve_application_config(path)


def test_migration_rebuilds_global_profile_permissions_and_keeps_equal_explicit_values(tmp_path):
    source = write(
        tmp_path / "old/app.yaml",
        {
            "plugin_settings": {
                PLUGIN: {
                    "auth_access": {
                        "sites": {
                            "x": {
                                "origins": "https://example.com",
                                "secrets": {"password": "PRIVATE_REF"},
                                "login": "allow",
                            }
                        }
                    }
                }
            }
        },
    )
    write(
        source.parent / "sites/global.yaml",
        {"plugin_settings": {PLUGIN: {"auth_access": {"sites": {"x": {"login": "deny"}}}}}},
    )
    write(
        source.parent / "profiles/work/app.yaml",
        {
            "network": {"request_concurrency": 8},
            "plugin_settings": {PLUGIN: {"auth_access": {"sites": {"x": {"login": "allow"}}}}},
        },
    )
    write(
        source.parent / "profiles/work/sites/example.com.yaml",
        {"plugin_settings": {"com.example.other": {"config": {"custom": True}}}},
    )
    destination = tmp_path / "new"
    plan = plan_migration(source, destination)
    profile_auth = plan.documents[destination / "profiles/work/auth.yaml"]
    assert profile_auth["plugins"][PLUGIN]["bindings"]["x"]["login"] == "deny"
    assert plan.documents[destination / "profiles/work/app.yaml"]["network"]["request_concurrency"] == 8
    apply_migration(plan)
    assert validate_tree(destination / "app.yaml")["valid"]


def test_migration_failure_keeps_marker_and_never_resumes_or_overwrites(tmp_path, monkeypatch):
    from image_downloader.configuration import migration

    source = write(tmp_path / "old/app.yaml", {})
    destination = tmp_path / "new"
    plan = plan_migration(source, destination)
    original = migration.yaml.safe_dump

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(migration.yaml, "safe_dump", interrupted)
    with pytest.raises(KeyboardInterrupt):
        apply_migration(plan)
    assert (destination / INCOMPLETE_MARKER).exists()
    with pytest.raises(ConfigurationError, match="incomplete"):
        resolve_application_config(destination / "app.yaml")
    with pytest.raises(ConfigurationError, match="must not exist"):
        apply_migration(plan)


@pytest.mark.parametrize("component", ["source", "destination"])
def test_migration_rejects_symlink_boundaries(tmp_path, component):
    source = write(tmp_path / "old/app.yaml", {})
    link = tmp_path / "linked"
    try:
        link.symlink_to(source.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links unavailable")
    with pytest.raises(ConfigurationError, match="link or reparse"):
        plan_migration(
            link / "app.yaml" if component == "source" else source,
            link / "new" if component == "destination" else tmp_path / "new",
        )


def test_runtime_partial_limits_use_inherited_values_and_ipv6_inventory_is_canonical(tmp_path):
    path = write(tmp_path / "app.yaml", {"config_version": 2, "network": {"request_concurrency": 20}})
    config = resolve_application_config(path, runtime_override={"network": {"origin_request_concurrency": 10}}).config
    assert config.network.origin_request_concurrency == 10
    write(tmp_path / "sites/ipv6-1/app.yaml", {"unknown": True})
    with pytest.raises(ConfigurationError, match="normalized host name"):
        validate_tree(path)


def test_empty_session_mapping_inherits_and_empty_patch_does_not_reset_persistence(tmp_path):
    path = main(tmp_path)
    auth(tmp_path, {"defaults": {"session": {"persistence": "ephemeral"}}, "bindings": {"x": binding(session={})}})
    access = resolve_application_config(path).config.plugin_settings[PLUGIN].auth_access
    assert resolve_auth_access(PLUGIN, "https://example.com", access).site.mode == "anonymous"
    selected = operation_access(
        PLUGIN,
        "https://example.com",
        access.model_dump(exclude_unset=True),
        legacy=None,
        replacements=None,
        patches={PLUGIN: {"session": {}}},
        host_groups={},
    )
    assert selected.site.mode == "anonymous"


def test_source_change_after_output_started_keeps_destination_incomplete(tmp_path, monkeypatch):
    from image_downloader.configuration import migration

    source = write(tmp_path / "old/app.yaml", {})
    destination = tmp_path / "new"
    plan = plan_migration(source, destination)
    original = migration._verify_sources
    checks = 0

    def concurrent_change(current):
        nonlocal checks
        checks += 1
        if checks == 2:
            assert (destination / INCOMPLETE_MARKER).exists()
            source.write_text("network: {max_attempts: 2}\n")
        original(current)

    monkeypatch.setattr(migration, "_verify_sources", concurrent_change)
    with pytest.raises(ConfigurationError, match="source changed"):
        apply_migration(plan)
    assert (destination / INCOMPLETE_MARKER).exists()
    with pytest.raises(ConfigurationError, match="incomplete"):
        resolve_application_config(destination / "app.yaml")


def test_migration_rejects_reparse_point_without_following_it(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    source = write(tmp_path / "old/app.yaml", {})
    original = Path.lstat

    def reparse(path):
        state = original(path)
        return (
            SimpleNamespace(st_mode=state.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            if path == source.parent
            else state
        )

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ConfigurationError, match="link or reparse"):
        plan_migration(source, tmp_path / "new")


def test_migration_does_not_turn_invalid_legacy_smtp_auth_into_anonymous_delivery(tmp_path):
    values = {
        "notification": {
            "enabled": True,
            "methods": ["email"],
            "email": {
                "smtp_host": "smtp.example.com",
                "from": "sender@example.com",
                "to": ["recipient@example.com"],
                "username": "PRIVATE_USER",
            },
        }
    }
    source = write(tmp_path / "old/app.yaml", values)
    with pytest.raises(ConfigurationError, match="active legacy SMTP username"):
        plan_migration(source, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    values["notification"]["enabled"] = False
    write(source, values)
    plan = plan_migration(source, tmp_path / "new")
    assert "username" not in plan.documents[tmp_path / "new/app.yaml"]["notification"]["email"]
    assert "PRIVATE_USER" not in json.dumps(plan.report())


def test_migration_preserves_empty_plugin_entries_without_adding_processor_auth(tmp_path):
    from support.plugins import make_plugin

    from image_downloader.plugins.runtime import PluginRuntime

    identifier = "com.example.processor"
    root = tmp_path / "plugins"
    make_plugin(root, plugin_id=identifier, kind="image_processor_plugin")
    source = write(
        tmp_path / "old/app.yaml",
        {
            "plugins": {"root": str(root)},
            "plugin_settings": {identifier: {}},
            "image_processors": {"chain": [identifier]},
        },
    )
    destination = tmp_path / "new"
    plan = plan_migration(source, destination)
    assert plan.documents[destination / "plugin-settings" / (identifier + ".yaml")] == {}
    assert identifier not in plan.documents[destination / "auth.yaml"].get("plugins", {})
    apply_migration(plan)
    config = resolve_application_config(destination / "app.yaml").config
    runtime = PluginRuntime(config, root, mode="bypass-all")
    try:
        runtime.validate_settings()
    finally:
        runtime.close()


def test_migration_validate_and_explain_cli_preserve_envelopes_and_report_completeness(tmp_path, capsys):
    source = write(tmp_path / "old/app.yaml", {"network": {"headers": {"X-Private": "PRIVATE_CANARY"}}})
    destination = tmp_path / "new"
    command = ["config", "migrate", "--config", str(source), "--destination", str(destination), "--json"]
    assert dispatch.main(command) == 0
    output = capsys.readouterr().out
    assert not destination.exists() and "PRIVATE_CANARY" not in output
    assert not json.loads(output)["execution"]["result"]["writes_applied"]
    assert dispatch.main([*command, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["execution"]["result"]["writes_applied"]
    assert dispatch.main(["config", "validate", "--config", str(destination / "app.yaml"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["execution"]["result"]["valid"]
    assert (
        dispatch.main(
            [
                "config",
                "explain",
                "--config",
                str(destination / "app.yaml"),
                "--data-root",
                str(tmp_path / "data"),
                "--plugin-root",
                str(tmp_path / "plugins"),
            ]
        )
        == 0
    )
    assert "user layers only" in capsys.readouterr().out


def test_smtp_only_and_empty_auth_maps_do_not_erase_other_value_sources(tmp_path):
    path = main(tmp_path)
    private = write(tmp_path / "plugin-settings" / (PLUGIN + ".yaml"), {"config": {"value": 1}})
    common_auth = auth(
        tmp_path,
        {
            "defaults": {"session": {"persistence": "ephemeral"}},
            "host_groups": {"identity": ["example.com"]},
            "bindings": {"x": binding("${host_groups.identity}")},
            "connections": {"host_groups": {"services": ["api.example.com"]}},
        },
    )
    write(
        tmp_path / "profiles/default/auth.yaml",
        {
            "smtp": {"credential_reference": None},
            "plugins": {PLUGIN: {"defaults": {"session": {}}, "host_groups": {}, "connections": {"host_groups": {}}}},
        },
    )
    resolved = resolve_application_config(path)
    prefix = "plugin_settings." + PLUGIN
    assert resolved.origins[prefix + ".config.value"] == str(private)
    assert resolved.origins[prefix + ".auth_access.host_groups.identity"] == str(common_auth)
    assert resolved.origins[prefix + ".host_groups.services"] == str(common_auth)
    assert resolved.origins[prefix + ".auth_access.mode"] == str(common_auth)


def test_profile_named_sites_is_not_misclassified_as_a_host_layer_during_migration(tmp_path):
    source = write(tmp_path / "old/app.yaml", {"profile": {"default": "sites"}})
    write(
        source.parent / "profiles/sites/app.yaml",
        {"security": {"plugin_verification": "off"}, "network": {"max_attempts": 5}},
    )
    write(source.parent / "profiles/sites/sites/example.com.yaml", {"network": {"request_timeout_seconds": 60}})
    destination = tmp_path / "new"
    plan = plan_migration(source, destination)
    apply_migration(plan)
    config = resolve_application_config(destination / "app.yaml", site="example.com").config
    assert config.profile.default == "sites" and config.security.plugin_verification == "off"
    assert config.network.max_attempts == 5 and config.network.request_timeout_seconds == 60
