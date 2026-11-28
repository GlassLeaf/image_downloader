from __future__ import annotations

import httpx
import pytest
import yaml
from pydantic import ValidationError

from image_downloader import PluginAuthAccess
from image_downloader.config import PluginSettings
from image_downloader.configuration import legacy_layers
from image_downloader.configuration.auth_origins import OriginMatcher
from image_downloader.configuration.endpoint_origins import expand_origins
from image_downloader.configuration.layers import apply_overrides
from image_downloader.credentials.access import compile_auth_access, resolve_auth_access
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ConfigurationError

HOSTS = "$" + "{host_groups.default}"


def policy(origins, **site):
    return PluginAuthAccess.model_validate({"sites": {"shared": {"origins": origins, **site}}})


def test_groups_normalize_deduplicate_and_detach_both_mapping_and_arrays():
    hosts = ["EXAMPLE.COM.", "example.com", "bücher.de"]
    groups = {"default": hosts, "future": []}
    setting = PluginSettings(host_groups=groups)
    hosts.append("evil.test")
    groups["other"] = ["evil.test"]
    assert setting.host_groups == {"default": ("example.com", "xn--bcher-kva.de"), "future": ()}
    with pytest.raises(TypeError):
        setting.host_groups["default"] = ()
    with pytest.raises(ValidationError):
        setting.host_groups = {}


@pytest.mark.parametrize(
    "value", [[], "hosts", {"Default": []}, {"a.b": []}, {"_private": []}, {"a" * 65: []}, {1: []}]
)
def test_groups_reject_invalid_mapping_and_names(value):
    with pytest.raises(ValidationError):
        PluginSettings(host_groups=value)


@pytest.mark.parametrize(
    "hosts",
    [
        "example.com",
        [1],
        [""],
        ["https://example.com"],
        ["example.com:443"],
        ["127.0.0.1"],
        ["::1"],
        ["*.example.com"],
        ["example.com/path"],
        [" example.com"],
        ["a@b.com"],
        ["$" + "{host_groups.other}"],
    ],
)
def test_groups_reject_invalid_host_arrays_even_if_unused(hosts):
    with pytest.raises(ValidationError):
        PluginSettings(host_groups={"unused": hosts})


@pytest.mark.parametrize("value", [{"allowed_hosts": []}, {"allowed_hosts": ["example.com"]}])
def test_legacy_setting_has_no_compatibility_path(value):
    with pytest.raises(ValidationError, match="was removed"):
        PluginSettings.model_validate(value)


@pytest.mark.parametrize(
    "origin",
    [
        "https://" + "$" + "{allowed_hosts}",
        "https://" + "$" + "{host_groups}",
        "https://" + "$" + "{host_groups.Bad}",
        "https://" + "$" + "{host_groups.a.b}",
        "https://" + "$" + "{config.hosts}",
        "https://" + "$" + "{operation.host}",
        "https://" + "$" + "{env.HOSTS}",
        "https://" + HOSTS + "." + HOSTS,
    ],
)
def test_auth_templates_reject_legacy_malformed_other_namespaces_and_multiple_arrays(origin):
    with pytest.raises(ValidationError):
        policy(origin)


def test_multiple_groups_are_selected_explicitly_and_group_names_are_not_auth_identities():
    raw = policy(["https://" + HOSTS, "https://*." + HOSTS], secrets={"username": "USER"})
    groups = {"default": ("example.com", "example.co.jp"), "unused": ("other.com",)}
    compiled = compile_auth_access("a.b", raw, groups)
    assert raw.sites["shared"].origins[0] == "https://" + HOSTS
    assert len(compiled.sites["shared"].origins) == 4
    first = resolve_auth_access("a.b", "https://example.com", raw, host_groups=groups)
    renamed = policy(
        ["https://" + "$" + "{host_groups.renamed}", "https://*." + "$" + "{host_groups.renamed}"],
        secrets={"username": "USER"},
    )
    second = resolve_auth_access(
        "a.b", "https://viewer.example.co.jp", renamed, host_groups={"renamed": ("example.co.jp", "example.com")}
    )
    assert first.key == second.key and first.session_key == second.session_key
    changed = resolve_auth_access("a.b", "https://example.com", raw, host_groups={"default": ("example.com",)})
    assert changed.key != first.key and changed.session_key != first.session_key
    direct = compile_auth_access("a.b", policy("https://literal.other.com"), groups)
    assert direct.sites["shared"].origins == ("https://literal.other.com:443",)


@pytest.mark.parametrize("groups,kind", [({}, "undefined"), ({"default": ()}, "empty")])
def test_missing_and_empty_references_are_safe_configuration_errors(groups, kind):
    with pytest.raises(ConfigurationError) as captured:
        compile_auth_access("com.example.gallery", policy("https://" + HOSTS), groups)
    details = diagnostic_for(captured.value).details
    assert details["kind"] == "host_groups_" + kind
    assert details["field"] == "plugin_settings.com.example.gallery.host_groups.default"
    assert details["plugin_id"] == "com.example.gallery"


def test_endpoint_references_use_only_user_groups_and_preserve_config_operation_variables():
    config = {"host_groups": {"default": ["attacker.com"]}, "prefix": "viewer"}
    with pytest.raises(ValueError, match="not defined"):
        expand_origins("https://" + HOSTS, config, None)
    assert expand_origins(
        "https://" + "$" + "{config.prefix}." + HOSTS,
        config,
        None,
        host_groups={"default": ("example.com", "example.co.jp")},
    ) == ("https://viewer.example.com:443", "https://viewer.example.co.jp:443")
    assert expand_origins("https://" + "$" + "{operation.host}", {}, "https://example.com/path") == (
        "https://example.com:443",
    )
    with pytest.raises(ValueError, match="one array"):
        expand_origins(
            "https://" + "$" + "{config.array}." + HOSTS,
            {"array": ["viewer"]},
            None,
            host_groups={"default": ("example.com",)},
        )


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://viewer.example.com", True),
        ("https://viewer.example.com:443", True),
        ("https://example.com", False),
        ("https://a.viewer.example.com", False),
        ("http://viewer.example.com", False),
        ("https://viewer.example.com:8443", False),
        ("https://viewer.example.com.evil.com", False),
        ("https://evil-example.com", False),
    ],
)
def test_wildcards_retain_scheme_port_apex_and_depth_boundaries(url, allowed):
    assert OriginMatcher.compile(("https://*.example.com:443",)).matches(url) is allowed


@pytest.mark.parametrize(
    "origin",
    [
        "https://*.co.jp",
        "https://*.github.io",
        "http://*.example.com",
        "https://**.example.com",
        "https://a*.example.com",
        "https://*.127.0.0.1",
        "https://*.example.com/path",
    ],
)
def test_unsafe_wildcards_remain_invalid(origin):
    with pytest.raises(ValidationError):
        policy(origin)


def test_wildcard_validation_fails_closed_without_psl(monkeypatch):
    monkeypatch.setattr("image_downloader.configuration.endpoint_origins._suffix_list", lambda: None)
    with pytest.raises(ValidationError, match="registrable base"):
        policy("https://*.example.com")
    assert policy("https://example.com").sites["shared"].origins == ("https://example.com:443",)


def test_overlap_is_checked_on_effective_expanded_sites_not_on_group_definitions():
    raw = PluginAuthAccess.model_validate(
        {"sites": {"first": {"origins": "https://*." + HOSTS}, "second": {"origins": "https://viewer.example.com"}}}
    )
    with pytest.raises(ConfigurationError) as captured:
        compile_auth_access("a.b", raw, {"default": ("example.com",)})
    assert diagnostic_for(captured.value).details["kind"] == "auth_origins_overlap"
    compile_auth_access("a.b", policy(["https://*.example.com", "https://viewer.example.com"]))
    compile_auth_access(
        "a.b",
        PluginAuthAccess.model_validate(
            {
                "sites": {
                    "first": {"origins": "https://*.example.com"},
                    "second": {"origins": "https://*.sub.example.com"},
                }
            }
        ),
    )


def test_legacy_layers_merge_groups_replace_arrays_and_validate_partial_auth_after_merge(tmp_path):
    app = tmp_path / "app.yaml"
    app.write_text(
        yaml.safe_dump(
            {
                "plugin_settings": {
                    "a.b": {
                        "host_groups": {"default": ["base.example.com"], "retained": ["other.com"]},
                        "auth_access": {"sites": {"shared": {"origins": "https://" + HOSTS, "login": "allow"}}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    overlay = tmp_path / "profiles/default/app.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        yaml.safe_dump(
            {
                "plugin_settings": {
                    "a.b": {
                        "host_groups": {"default": ["overlay.example.com"]},
                        "auth_access": {"sites": {"shared": {"login": "deny"}}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = legacy_layers.resolve_application_config(app)
    setting = resolved.config.plugin_settings["a.b"]
    assert setting.host_groups == {"default": ("overlay.example.com",), "retained": ("other.com",)}
    assert setting.auth_access.sites["shared"].login_policy == "deny"
    assert "profiles" in resolved.origins["plugin_settings.a.b.host_groups.default"]
    changed = apply_overrides(resolved.config, {"plugin_settings": {"a.b": {"host_groups": {}}}})
    assert changed.plugin_settings["a.b"].host_groups == setting.host_groups
    with pytest.raises(ConfigurationError, match="must not be empty"):
        apply_overrides(resolved.config, {"plugin_settings": {"a.b": {"host_groups": {"default": []}}}})


def test_legacy_undefined_group_stops_effective_configuration_and_user_file_is_unchanged(tmp_path):
    app = tmp_path / "app.yaml"
    source = yaml.safe_dump(
        {"plugin_settings": {"a.b": {"auth_access": {"sites": {"shared": {"origins": "https://" + HOSTS}}}}}}
    )
    app.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not defined"):
        legacy_layers.resolve_application_config(app, rewrite_user_layers=True)
    assert app.read_text(encoding="utf-8") == source


@pytest.mark.parametrize("field", ["allowed_hosts", "host_groups", "auth_access"])
def test_author_yaml_cannot_define_user_hosts_or_grants(tmp_path, field):
    from image_downloader.exceptions import PluginError
    from image_downloader.plugins.plugin_manifest import PluginManifest, author_settings

    (tmp_path / "plugin.yaml").write_text(yaml.safe_dump({"config": {}, field: {}}), encoding="utf-8")
    with pytest.raises(PluginError, match="author YAML"):
        author_settings(PluginManifest({"config_file": "plugin.yaml"}, None, "unused", tmp_path))


@pytest.mark.parametrize(
    "domain,host_only,expected",
    [
        ("example.com", True, False),
        ("viewer.example.com", True, True),
        ("a.viewer.example.com", True, False),
        (".example.com", False, True),
        ("viewer.example.com", False, True),
        ("a.viewer.example.com", False, False),
        ("other.com", False, False),
    ],
)
def test_cookie_snapshot_scope_respects_wildcard_depth_and_host_only(domain, host_only, expected):
    cookies = httpx.Cookies()
    cookies.set("session", "value", domain=domain, path="/private")
    cookie = next(iter(cookies.jar))
    cookie.domain_specified = not host_only
    matcher = OriginMatcher.compile(("https://*.example.com:443",))
    assert matcher.accepts_cookie(cookie) is expected
    assert cookie.path == "/private"


def test_cookie_snapshot_excludes_expired_and_incompatible_secure_cookies():
    cookies = httpx.Cookies()
    cookies.set("session", "value", domain="viewer.example.com")
    cookie = next(iter(cookies.jar))
    cookie.secure = True
    secure = OriginMatcher.compile(("https://*.example.com:443",))
    plain = OriginMatcher.compile(("http://viewer.example.com:80",))
    assert secure.accepts_cookie(cookie)
    assert not plain.accepts_cookie(cookie)
    cookie.expires = 0
    assert not secure.accepts_cookie(cookie)


def test_legacy_empty_group_mapping_overlay_preserves_value_provenance(tmp_path):
    app = tmp_path / "app.yaml"
    app.write_text(
        yaml.safe_dump({"plugin_settings": {"a.b": {"host_groups": {"sso": ["example.com"]}}}}), encoding="utf-8"
    )
    overlay = tmp_path / "profiles/default/app.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(yaml.safe_dump({"plugin_settings": {"a.b": {"host_groups": {}}}}), encoding="utf-8")
    resolved = legacy_layers.resolve_application_config(app)
    assert resolved.config.plugin_settings["a.b"].host_groups["sso"] == ("example.com",)
    assert resolved.origins["plugin_settings.a.b.host_groups.sso"] == str(app)


def test_legacy_merged_undefined_reference_reports_origin_location_without_site_name(tmp_path):
    app = tmp_path / "app.yaml"
    source = (
        "plugin_settings:\n  a.b:\n    auth_access:\n      sites:\n"
        '        PRIVATE_SITE_CANARY:\n          origins: "https://' + HOSTS + '"\n'
    )
    app.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        legacy_layers.resolve_application_config(app, rewrite_user_layers=True)
    diagnostic = diagnostic_for(captured.value)
    assert diagnostic.details["source"] == "app:app.yaml"
    assert diagnostic.details["line"] == 6 and diagnostic.details["column"] > 0
    assert "PRIVATE_SITE_CANARY" not in str(diagnostic)
    assert app.read_text(encoding="utf-8") == source


def test_legacy_referenced_empty_group_reports_the_overriding_layer_location(tmp_path):
    app = tmp_path / "app.yaml"
    app.write_text(
        yaml.safe_dump(
            {
                "plugin_settings": {
                    "a.b": {
                        "host_groups": {"default": ["example.com"]},
                        "auth_access": {"sites": {"shared": {"origins": "https://" + HOSTS}}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    overlay = tmp_path / "profiles/default/app.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("plugin_settings:\n  a.b:\n    host_groups:\n      default: []\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        legacy_layers.resolve_application_config(app)
    details = diagnostic_for(captured.value).details
    assert details["kind"] == "host_groups_empty"
    assert details["source"] == "profile_app:app.yaml"
    assert details["line"] == 4 and details["column"] > 0


def test_legacy_invalid_endpoint_group_expansion_fails_before_cleanup_and_masks_rejected_port(tmp_path):
    app = tmp_path / "app.yaml"
    source = yaml.safe_dump(
        {
            "download": {"unrelated_unknown": True},
            "plugin_settings": {
                "a.b": {
                    "host_groups": {"default": ["example.com"]},
                    "endpoints": {"viewer": {"origin": "https://" + HOSTS + ":PRIVATE_PORT_CANARY", "auth": "none"}},
                }
            },
        }
    )
    app.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        legacy_layers.resolve_application_config(app, rewrite_user_layers=True)
    diagnostic = diagnostic_for(captured.value)
    assert diagnostic.details["kind"] == "host_groups_expansion"
    assert diagnostic.details["plugin_id"] == "a.b"
    assert diagnostic.details["source"] == "app:app.yaml"
    assert diagnostic.details["line"] > 0
    assert "PRIVATE_PORT_CANARY" not in str(diagnostic)
    assert app.read_text(encoding="utf-8") == source
