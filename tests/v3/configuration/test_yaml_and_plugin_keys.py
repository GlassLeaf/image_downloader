from __future__ import annotations

import pytest

from image_downloader.configuration.layers import resolve_application_config
from image_downloader.configuration.yaml_input import DuplicateYamlKeyError, safe_load_unique
from image_downloader.devtools.output import read_document
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ConfigurationError


@pytest.mark.parametrize(
    "source",
    [
        "download: {}\ndownload: {}\n",
        "download:\n  chapter_concurrency: 1\n  chapter_concurrency: 2\n",
        "plugin_settings:\n  a.b: {}\n  a.b: {}\n",
        "plugin_settings:\n  a.b:\n    host_groups:\n      sso: [example.com]\n      sso: [example.org]\n",
        "plugin_settings:\n  a.b:\n    config:\n      PASSWORD_CANARY: one\n      PASSWORD_CANARY: two\n",
    ],
)
def test_duplicate_keys_fail_without_values_or_file_rewrites(tmp_path, source):
    path = tmp_path / "app.yaml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        resolve_application_config(path, rewrite_user_layers=True)
    diagnostic = diagnostic_for(captured.value)
    assert diagnostic.details["kind"] == "configuration_duplicate_key"
    assert diagnostic.details["line"] > 1 and diagnostic.details["column"] > 0
    assert "CANARY" not in str(captured.value)
    assert path.read_text(encoding="utf-8") == source
    with pytest.raises(ConfigurationError) as development:
        read_document(path)
    assert diagnostic_for(development.value).details["kind"] == "configuration_duplicate_key"


def test_anchors_merge_and_explicit_overrides_are_not_duplicate_definitions():
    parsed = safe_load_unique("base: &base\n  sso: [example.com]\ncopy:\n  <<: *base\n  sso: [example.org]\n")
    assert parsed["base"]["sso"] == ["example.com"]
    assert parsed["copy"]["sso"] == ["example.org"]
    parsed = safe_load_unique("a: &a {x: 1}\nb: &b {x: 2}\nc: {<<: [*a, *b]}\n")
    assert parsed["c"]["x"] == 1
    with pytest.raises(DuplicateYamlKeyError):
        safe_load_unique("base: &base {x: 1, x: 2}\ncopy: {<<: *base}\n")


@pytest.mark.parametrize(
    "field",
    [
        "    host_gropus: {}\n",
        "    allowed_hosts: [example.com]\n",
        "    auth_access:\n      logni: deny\n",
        (
            "    auth_access:\n      sites:\n        named:\n"
            "          origins: https://example.com\n          credential_acess: deny\n"
        ),
    ],
)
def test_unknown_plugin_core_keys_and_legacy_are_errors_before_normalization(tmp_path, field):
    source = "plugin_settings:\n  a.b:\n" + field
    path = tmp_path / "app.yaml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        resolve_application_config(path, rewrite_user_layers=True)
    details = diagnostic_for(captured.value).details
    assert details["kind"] in {"configuration_unknown_field", "host_groups_legacy"}
    assert details["plugin_id"] == "a.b"
    assert path.read_text(encoding="utf-8") == source


def test_plugin_defined_config_and_other_existing_cleanup_rules_remain(tmp_path):
    path = tmp_path / "app.yaml"
    path.write_text(
        "download:\n  unrelated_unknown: true\nplugin_settings:\n  a.b:\n    config:\n      custom_name: value\n",
        encoding="utf-8",
    )
    resolved = resolve_application_config(path, rewrite_user_layers=True)
    assert resolved.config.plugin_settings["a.b"].config == {"custom_name": "value"}
    assert "unrelated_unknown" not in path.read_text(encoding="utf-8")


def test_old_reference_is_rejected_without_rewriting_user_configuration(tmp_path):
    path = tmp_path / "app.yaml"
    source = (
        "plugin_settings:\n  a.b:\n    auth_access:\n      sites:\n        shared:\n          origins: 'https://"
        + "$"
        + "{allowed_hosts}'\n"
    )
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        resolve_application_config(path, rewrite_user_layers=True)
    assert diagnostic_for(captured.value).details["kind"] == "host_groups_legacy"
    assert path.read_text(encoding="utf-8") == source


def test_development_manifest_author_yaml_uses_the_same_duplicate_detection(tmp_path):
    from support.plugins import make_plugin

    from image_downloader.devtools.loading import resolve_target

    directory = make_plugin(tmp_path / "plugins")
    (directory / "entry.yaml").write_text("config: {}\nconfig: {PRIVATE_CANARY: discarded}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        resolve_target(directory)
    diagnostic = diagnostic_for(captured.value)
    assert diagnostic.details["kind"] == "configuration_duplicate_key"
    assert diagnostic.details["line"] == 2
    assert "PRIVATE_CANARY" not in str(diagnostic)
