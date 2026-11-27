from __future__ import annotations

import json

import pytest
import yaml

from image_downloader import AppConfig
from image_downloader.commands import dispatch
from image_downloader.commands.errors import _error_payload
from image_downloader.commands.reporting import _doctor_redact
from image_downloader.configuration.layers import validate_config
from image_downloader.exceptions import ConfigurationError

HOSTS = "$" + "{host_groups.default}"


def config():
    return {
        "plugin_settings": {
            "com.example.gallery": {
                "host_groups": {"default": ["example.com"]},
                "auth_access": {
                    "sites": {
                        "shared": {
                            "origins": ["https://" + HOSTS, "https://*." + HOSTS],
                            "secrets": {"password": "PRIVATE_REFERENCE_CANARY"},
                        }
                    }
                },
            }
        }
    }


def test_typed_policy_reports_templates_expansion_source_and_masks_secret_references():
    source = "profile:work.yaml"
    safe = _doctor_redact(
        AppConfig.model_validate(config()), origins={"plugin_settings.com.example.gallery.host_groups.default": source}
    )
    access = safe["plugin_settings"]["com.example.gallery"]["auth_access"]
    assert access["sites"]["shared"]["origins"] == ["https://" + HOSTS, "https://*." + HOSTS]
    assert access["sites"]["shared"]["resolved_origins"] == ["https://example.com:443", "https://*.example.com:443"]
    assert access["variables"]["host_groups"]["default"]["source"] == source
    assert access["variables"]["host_groups"]["default"]["values"] == ["example.com"]
    assert "PRIVATE_REFERENCE_CANARY" not in json.dumps(safe)
    assert "PRIVATE_REFERENCE_CANARY" not in json.dumps(
        _doctor_redact({"auth_access": {"password": "PRIVATE_REFERENCE_CANARY"}})
    )


def test_config_explain_preserves_user_template_and_reports_actual_layer_source(tmp_path, capsys):
    path = tmp_path / "app.yaml"
    values = config()
    values["storage"] = {"data_root": str(tmp_path / "data")}
    values["plugins"] = {"root": str(tmp_path / "plugins")}
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    assert dispatch.main(["--config", str(path), "--json", "config", "explain"]) == 0
    result = json.loads(capsys.readouterr().out)
    access = result["execution"]["result"]["effective"]["plugin_settings"]["com.example.gallery"]["auth_access"]
    assert access["variables"]["host_groups"]["default"]["source"] == str(path)
    assert access["sites"]["shared"]["resolved_origins"] == ["https://example.com:443", "https://*.example.com:443"]
    assert "PRIVATE_REFERENCE_CANARY" not in json.dumps(result)


@pytest.mark.parametrize(
    "origin,kind",
    [
        ("https://" + "$" + "{config.SECRET_REFERENCE_CANARY}", "auth_origins_reference"),
        ("http://*.example.com", "auth_origins_wildcard"),
        ("https://example.com/path", "auth_origins_invalid"),
    ],
)
def test_origin_validation_diagnostics_publish_only_safe_reason_plugin_and_field(origin, kind):
    values = config()
    values["plugin_settings"]["com.example.gallery"]["auth_access"]["sites"]["shared"]["origins"] = origin
    with pytest.raises(ConfigurationError) as captured:
        validate_config(values)
    payload = _error_payload(captured.value, operation="config")
    assert payload["details"]["kind"] == kind
    assert payload["details"]["plugin_id"] == "com.example.gallery"
    assert payload["details"]["field"].endswith("auth_access.sites.<site>.origins")
    assert "CANARY" not in json.dumps(payload)


@pytest.mark.parametrize("hosts", ["SECRET_HOST_CANARY", ["https://SECRET_HOST_CANARY"], [42]])
def test_host_validation_diagnostics_identify_the_setting_without_rejected_values(hosts):
    values = config()
    values["plugin_settings"]["com.example.gallery"]["host_groups"] = {"default": hosts}
    with pytest.raises(ConfigurationError) as captured:
        validate_config(values)
    payload = _error_payload(captured.value, operation="config")
    assert payload["details"]["plugin_id"] == "com.example.gallery"
    assert payload["details"]["field"] == "plugin_settings.com.example.gallery.host_groups.default"
    assert "CANARY" not in json.dumps(payload)


@pytest.mark.parametrize("broken", [False, True])
def test_doctor_counts_author_endpoint_references_and_warns_only_when_complete(tmp_path, capsys, broken):
    from support.plugins import make_plugin

    root = tmp_path / "plugins"
    directory = make_plugin(root)
    author = {
        "config": {"author": True},
        "endpoints": {"viewer": {"origin": "https://viewer." + "$" + "{host_groups.assets}", "auth": "none"}},
    }
    (directory / "entry.yaml").write_text(yaml.safe_dump(author), encoding="utf-8")
    if broken:
        source = directory / "entry.source"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "if 'author' not in config: raise ValueError('config')", "raise RuntimeError('validation failed')"
            ),
            encoding="utf-8",
        )
    values = config()
    values.update(
        storage={"data_root": str(tmp_path / "data")},
        plugins={"root": str(root)},
        security={"plugin_verification": "off"},
    )
    values["plugin_settings"]["com.example.gallery"]["host_groups"].update(assets=["example.org"], future=[])
    path = tmp_path / "app.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    exit_code = dispatch.main(["--config", str(path), "--json", "doctor"])
    result = json.loads(capsys.readouterr().out)["execution"]["result"]
    warnings = result["configuration"]["warnings"]
    variables = result["configuration"]["effective"]["plugin_settings"]["com.example.gallery"]["auth_access"][
        "variables"
    ]["host_groups"]
    assert variables["assets"]["usage_complete"] is not broken
    if broken:
        assert exit_code != 0 and not warnings
    else:
        assert exit_code == 0 and result["healthy"]
        assert variables["assets"]["referenced_by"] == ["endpoints.viewer.origin"]
        assert [warning["field"] for warning in warnings] == ["plugin_settings.com.example.gallery.host_groups.future"]
        detail = next(plugin for plugin in result["loaded_plugins"] if plugin["id"] == "com.example.gallery")
        assert detail["endpoints"]["viewer"]["resolved_origins"] == ["https://viewer.example.org:443"]
        from image_downloader.commands.reporting import _print_doctor_report

        _print_doctor_report(result)
        text = capsys.readouterr().out
        assert "https://viewer.example.org:443" in text
        assert "PRIVATE_REFERENCE_CANARY" not in text
    assert "PRIVATE_REFERENCE_CANARY" not in json.dumps(result)
