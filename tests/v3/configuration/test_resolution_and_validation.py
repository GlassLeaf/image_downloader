from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import image_downloader.commands.config as cli_config
import image_downloader.commands.download as cli_download
from image_downloader.cli import EXIT_SUCCESS, build_parser, run
from image_downloader.config import AppConfig, resolve_application_config
from image_downloader.configuration import layers
from image_downloader.exceptions import ConfigurationError


def test_resolver_records_effective_value_origin_in_layer_order(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("network: {request_concurrency: 4}\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "default" / "app.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("network: {max_attempts: 5}\n", encoding="utf-8")
    site = tmp_path / "sites" / "example.test.yaml"
    site.parent.mkdir()
    site.write_text("network: {request_timeout_seconds: 9}\n", encoding="utf-8")

    resolved = resolve_application_config(
        main,
        site="example.test",
        runtime_override={"network": {"max_attempts": 6}},
        source="explicit",
    )

    assert resolved.config.network.request_concurrency == 4
    assert resolved.config.network.request_timeout_seconds == 9
    assert resolved.config.network.max_attempts == 6
    assert resolved.origins["network.request_concurrency"] == str(main)
    assert resolved.origins["network.request_timeout_seconds"] == str(site)
    assert resolved.origins["network.max_attempts"] == "runtime override"
    assert [layer.status for layer in resolved.layers[:4]] == ["applied", "applied", "applied", "applied"]


def test_explicit_package_baseline_is_not_merged_twice() -> None:
    baseline = Path(layers.__file__).parents[1] / "app.yaml"

    resolved = resolve_application_config(baseline, source="explicit")

    assert resolved.layers[1].role == "baseline"
    assert resolved.layers[2].role == "app"
    assert resolved.layers[2].status == "not_applicable"


def test_invalid_lower_layer_is_not_hidden_by_a_later_override(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("network: {max_attempts: -1}\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "default" / "app.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("network: {max_attempts: 3}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match=r"app layer .*network\.max_attempts"):
        resolve_application_config(main)


def test_removed_setting_names_are_ignored_read_only_and_removed_when_rewriting(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("# Retain this comment.\nnetwork: {max_retries: 3}\n", encoding="utf-8")

    assert resolve_application_config(main).config.network.max_attempts == 3
    assert "max_retries" in main.read_text(encoding="utf-8")

    assert resolve_application_config(main, rewrite_user_layers=True).config.network.max_attempts == 3
    rewritten = main.read_text(encoding="utf-8")
    assert "max_retries" not in rewritten
    assert "Retain this comment." in rewritten


@pytest.mark.parametrize("path", tuple(sorted(layers._REMOVED_KEYS)))
def test_every_known_removed_key_is_deleted_without_conversion(tmp_path: Path, path: tuple[str, ...]) -> None:
    document: dict[str, object] = {}
    current = document
    for component in path[:-1]:
        nested: dict[str, object] = {}
        current[component] = nested
        current = nested
    current[path[-1]] = "legacy-value"
    main = tmp_path / "app.yaml"
    main.write_text(yaml.safe_dump(document), encoding="utf-8")

    resolve_application_config(main, rewrite_user_layers=True)

    rewritten = yaml.safe_load(main.read_text(encoding="utf-8"))
    assert isinstance(rewritten, dict)
    current_value: object = rewritten
    for component in path[:-1]:
        assert isinstance(current_value, dict)
        current_value = current_value[component]
    assert isinstance(current_value, dict)
    assert path[-1] not in current_value


def test_unknown_static_keys_are_removed_from_every_user_layer_but_free_form_mapping_is_retained(
    tmp_path: Path,
) -> None:
    main = tmp_path / "app.yaml"
    main.write_text(
        "unknown_root: remove\nnetwork:\n  unknown_network: remove\n  headers: {X-Plugin: keep}\n"
        "plugin_settings:\n  com.example.gallery:\n    config: {plugin_private: keep}\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profiles" / "default" / "app.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("download: {unknown_download: remove}\n", encoding="utf-8")
    site = tmp_path / "sites" / "example.test.yaml"
    site.parent.mkdir()
    site.write_text("network: {max_retries: 2, unknown_site: remove}\n", encoding="utf-8")

    resolved = resolve_application_config(main, site="example.test", rewrite_user_layers=True)

    assert resolved.config.network.max_attempts == 3
    assert "unknown_root" not in main.read_text(encoding="utf-8")
    assert "unknown_network" not in main.read_text(encoding="utf-8")
    assert "X-Plugin: keep" in main.read_text(encoding="utf-8")
    assert "plugin_private: keep" in main.read_text(encoding="utf-8")
    assert "unknown_download" not in profile.read_text(encoding="utf-8")
    assert "max_retries" not in site.read_text(encoding="utf-8")
    assert "unknown_site" not in site.read_text(encoding="utf-8")


def test_layer_constraints_and_cleanup_write_failures_still_stop_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("unknown: remove\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "default" / "app.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("storage: {data_root: C:\\\\invalid}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match=r"bootstrap settings"):
        resolve_application_config(main, rewrite_user_layers=True)
    assert "unknown" in main.read_text(encoding="utf-8")

    profile.unlink()
    monkeypatch.setattr(layers, "_atomic_replace", lambda *_args: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(ConfigurationError, match=r"could not normalize configuration"):
        resolve_application_config(main, rewrite_user_layers=True)


def test_cleanup_atomic_replace_rejects_a_content_change_without_overwriting(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("unknown: old\n", encoding="utf-8")
    expected = main.read_bytes()
    main.write_text("unknown: newer\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match=r"configuration changed while normalizing"):
        layers._atomic_replace(main, expected, b"{}\n")

    assert main.read_text(encoding="utf-8") == "unknown: newer\n"


@pytest.mark.parametrize(
    "value",
    (
        {"network": {"request_concurrency": True}},
        {"network": {"request_concurrency": 1.0}},
        {"network": {"request_timeout_seconds": float("inf")}},
        {"network": {"retry_max_delay_seconds": float("nan")}},
        {"network": {"pool_max_connections": 4, "pool_max_idle_connections": 5}},
        {"storage": {"data_root": ""}},
    ),
)
def test_ambiguous_or_incoherent_values_are_rejected(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(value)


def test_config_explain_includes_layers_effective_values_and_origins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main = tmp_path / "app.yaml"
    original = "network: {request_concurrency: 3, stale: ignored}\n"
    main.write_text(original, encoding="utf-8")
    args = build_parser().parse_args(["config", "explain", "--config", str(main), "--json"])

    assert asyncio.run(run(args)) == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "explicit"
    assert payload["main_config_kind"] == "explicit"
    assert payload["effective"]["network"]["request_concurrency"] == 3
    assert payload["effective"]["network"]["auth_refresh_attempts"] == 1
    assert payload["origins"]["network.request_concurrency"] == str(main)
    assert payload["layers"][0]["role"] == "schema_default"
    assert main.read_text(encoding="utf-8") == original


def test_config_path_is_read_only_and_reports_the_fixed_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = tmp_path / "data"
    plugin_root = data_root / "plugins"
    monkeypatch.setattr(cli_config, "default_user_config_path", lambda: user_config)
    monkeypatch.setattr(cli_config, "default_data_root", lambda: data_root)
    monkeypatch.setattr(cli_config, "default_plugin_root", lambda: plugin_root)
    args = build_parser().parse_args(["config", "path", "--json"])

    assert asyncio.run(run(args)) == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_config"] == str(user_config)
    assert payload["default_data_root"] == str(data_root)
    assert payload["default_plugin_root"] == str(plugin_root)
    assert payload["user_config_exists"] is False
    assert not user_config.parent.exists()
    assert not data_root.exists()


def test_state_changing_command_rewrites_user_layer_before_composing_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("network: {max_retries: 4, misspelled: remove}\n", encoding="utf-8")
    composed = False

    class Service:
        async def run(self, *_args: object, **_kwargs: object) -> object:
            return type("Result", (), {"saved_files": (), "skipped_files": (), "failures": ()})()

        async def close(self) -> None:
            return None

    class Composer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal composed
            assert "max_retries" not in main.read_text(encoding="utf-8")
            assert "misspelled" not in main.read_text(encoding="utf-8")
            composed = True

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
    args = build_parser().parse_args(["download", "https://example.test/item", "--config", str(main)])

    assert asyncio.run(run(args)) == EXIT_SUCCESS
    assert composed


def test_cleanup_failure_stops_state_changing_command_before_service_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("unknown: remove\n", encoding="utf-8")
    composed = False

    class Composer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal composed
            composed = True

    monkeypatch.setattr(layers, "_atomic_replace", lambda *_args: (_ for _ in ()).throw(OSError("denied")))
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
    args = build_parser().parse_args(["download", "https://example.test/item", "--config", str(main)])

    with pytest.raises(ConfigurationError, match=r"could not normalize configuration"):
        asyncio.run(run(args))
    assert not composed
    assert "unknown" in main.read_text(encoding="utf-8")
