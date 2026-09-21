from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import image_downloader.plugins.management as plugin_management
from image_downloader.cli import EXIT_SUCCESS, build_parser, doctor
from image_downloader.config import AppConfig, load_application_config, site_file_name
from image_downloader.exceptions import ConfigurationError, PluginError
from image_downloader.ports import ImageProcessor as ImageProcessorProtocol
from image_downloader.ports import SitePlugin
from image_downloader.runtime import RuntimeComposer
from image_downloader.security import (
    PluginCatalog,
    PluginClassLoader,
    PluginDiscovery,
    PluginManifestVerifier,
    PluginRegistry,
    PluginRuntime,
    PluginSelector,
    canonical_jcs,
    install_plugin,
    read_manifest,
    trust_plugin,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def make_plugin(
    root: Path,
    *,
    plugin_id: str = "com.example.gallery",
    kind: str = "site_plugin",
    priority: int = 1,
    configured_match: bool = False,
    runnable: bool = False,
) -> Path:
    folder = "site_plugins" if kind == "site_plugin" else "image_processor_plugins"
    directory = root / folder / plugin_id.rsplit(".", 1)[-1]
    directory.mkdir(parents=True)
    source_name = "entry.source"
    source = ("import logging\nfrom image_downloader.models import DownloadManifest\n\n" if runnable else "") + (
        "class Entry:\n"
        "    def validate_config(self, config, app_settings):\n"
        "        if 'author' not in config: raise ValueError('config')\n"
        "        return None\n"
    )
    if kind == "site_plugin":
        inspect_source = (
            "    async def inspect(self, url, context):\n"
            "        logging.getLogger(__name__).warning('plugin-operation-marker')\n"
            "        return DownloadManifest('logged', ())\n"
            if runnable
            else "    async def inspect(self, url, context): raise AssertionError('not used')\n"
        )
        source += (
            "    def matches(self, url): return 'example.test' in url\n"
            + inspect_source
            + "    async def create_image_request(self, image, context): raise AssertionError('not used')\n"
            "    async def recover_image_request(self, image, failed, response, context): return None\n"
            "    def auth_flow(self, context): return None\n"
            "    async def transform_image(self, artifact, context): return artifact\n"
        )
        if configured_match:
            source += (
                "    def matches_with_config(self, url, config, app_settings):\n"
                "        return str(config.get('alternate_host', '')) in url\n"
            )
    else:
        source += "    async def transform(self, artifact, context): return artifact\n"
    author_name = "entry.yaml"
    files = {source_name: source.encode(), author_name: b"config:\n  author: true\n  nested:\n    one: 1\n"}
    for name, content in files.items():
        (directory / name).write_bytes(content)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    tree = {name: _digest(content) for name, content in files.items()}
    manifest = {
        "schema_version": 1,
        "id": plugin_id,
        "publisher": "com.example",
        "version": "1.0",
        "api_version": "3",
        "kind": kind,
        "capabilities": ["reserved"],
        "match_priority": priority,
        "entry": {"file": source_name, "class": "Entry"},
        "config_file": author_name,
        "public_key": base64.b64encode(public).decode(),
        "key_id": _digest(public),
        "file_tree": tree,
        "file_tree_sha256": _digest(canonical_jcs(tree)),
    }
    wrapper = {"manifest": manifest, "signature": base64.b64encode(private.sign(canonical_jcs(manifest))).decode()}
    (directory / "manifest.json").write_text(json.dumps(wrapper), encoding="utf-8")
    return directory


def test_idr_025_discovery_and_verification_modes_are_independent(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)

    discovery = PluginDiscovery(root).discover()

    assert [candidate.manifest.id for candidate in discovery.candidates] == ["com.example.gallery"]
    strict = PluginManifestVerifier(root, mode="strict").verify(discovery)
    warn = PluginManifestVerifier(root, mode="warn").verify(discovery)
    off = PluginManifestVerifier(root, mode="off").verify(discovery)
    assert strict.records == ()
    assert any(not item.loaded and "catalog is unavailable" in item.detail for item in strict.diagnostics)
    assert [record.id for record in warn.records] == ["com.example.gallery"]
    assert any(item.warning and item.name == "com.example.gallery" for item in warn.diagnostics)
    assert [record.id for record in off.records] == ["com.example.gallery"]
    assert not any(item.warning for item in off.diagnostics)


def test_idr_025_registry_is_a_read_only_record_snapshot(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    discovery = PluginDiscovery(root).discover()
    verified = PluginManifestVerifier(root, mode="off").verify(discovery)
    registry = PluginRegistry(verified.records, catalog=verified.catalog)
    record = registry.get("com.example.gallery")

    assert record is not None
    assert registry.records[record.id] is record
    assert not hasattr(registry, "resolve")
    with pytest.raises(TypeError):
        registry.records["com.example.changed"] = record  # type: ignore[index]


def test_idr_025_loader_and_selector_preserve_priority_and_fallback(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root, priority=1)
    make_plugin(root, plugin_id="com.example.preferred", priority=10)
    runtime = PluginRuntime(AppConfig(), root, mode="off")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    runtime.register_builtin(GenericHtmlPlugin)
    runtime.prepare()

    assert isinstance(runtime.loader, PluginClassLoader)
    assert isinstance(runtime.selector, PluginSelector)
    preferred, _ = runtime.resolve("https://example.test/item", fallback_enabled=True)
    fallback, _ = runtime.resolve("https://unmatched.test/item", fallback_enabled=True)
    assert preferred.id == "com.example.preferred"
    assert fallback.id == "core.generic-html"


def test_idr_040_049_plugin_records_are_immutable_runtime_descriptions(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    runtime = PluginRuntime(AppConfig(), root, mode="off")
    record = runtime.records["com.example.gallery"]

    assert not hasattr(record, "plugin_class")
    assert not hasattr(record, "module_namespace")
    with pytest.raises(FrozenInstanceError):
        record.builtin = True  # type: ignore[misc]
    runtime.close()


def test_idr_040_049_loader_caches_classes_and_isolates_runtime_namespaces(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    first = PluginRuntime(AppConfig(), root, mode="off")
    second = PluginRuntime(AppConfig(), root, mode="off")
    first_record = first.records["com.example.gallery"]
    second_record = second.records["com.example.gallery"]
    entry = root / "site_plugins" / "gallery" / "entry.source"
    entry.write_text("from . import helper\n" + entry.read_text(encoding="utf-8"), encoding="utf-8")
    (entry.parent / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

    first_class = first.loader.load_class(first_record)
    assert first.loader.load_class(first_record) is first_class
    second.loader.load_class(second_record)
    first_namespace = first.module_namespace(first_record)
    second_namespace = second.module_namespace(second_record)
    assert first_namespace is not None and second_namespace is not None
    assert first_namespace != second_namespace
    assert first_namespace in sys.modules and second_namespace in sys.modules
    assert f"{first_namespace}.helper" in sys.modules
    assert f"{second_namespace}.helper" in sys.modules

    first.close()
    assert not any(name == first_namespace or name.startswith(f"{first_namespace}.") for name in sys.modules)
    assert second_namespace in sys.modules
    second.close()
    assert not any(name == second_namespace or name.startswith(f"{second_namespace}.") for name in sys.modules)


def test_idr_040_049_failed_import_rolls_back_all_new_module_entries(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root)
    runtime = PluginRuntime(AppConfig(), root, mode="off")
    record = runtime.records["com.example.gallery"]
    (directory / "entry.source").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
    before = set(sys.modules)

    with pytest.raises(PluginError, match="plugin entry import failed"):
        runtime.loader.load_class(record)

    added = set(sys.modules) - before
    assert not any(name.startswith("_image_downloader_plugins") for name in added)
    assert runtime.module_namespace(record) is None
    runtime.close()


def test_idr_040_049_service_close_releases_its_plugin_modules(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "off"},
        }
    )
    service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()
    record = service.registry.records["com.example.gallery"]
    namespace = service.registry.module_namespace(record)
    assert namespace is not None and namespace in sys.modules

    asyncio.run(service.close())

    assert not any(name == namespace or name.startswith(f"{namespace}.") for name in sys.modules)
    with pytest.raises(PluginError, match="loader is closed"):
        service.registry.site_instance(record)


def test_typed_plugin_instance_boundaries_accept_only_the_declared_kind(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    make_plugin(root, plugin_id="com.example.processor", kind="image_processor_plugin")
    registry = PluginRuntime(AppConfig(), root, mode="off")
    site_record = registry.records["com.example.gallery"]
    processor_record = registry.records["com.example.processor"]

    assert isinstance(registry.site_instance(site_record), SitePlugin)
    assert isinstance(registry.processor_instance(processor_record), ImageProcessorProtocol)
    with pytest.raises(PluginError, match="not a site plugin"):
        registry.site_instance(processor_record)
    with pytest.raises(PluginError, match="not an image processor"):
        registry.processor_instance(site_record)


def test_typed_plugin_instance_boundaries_reject_incomplete_contracts(tmp_path: Path) -> None:
    class IncompleteSitePlugin:
        def validate_config(self, config: object, app_settings: object) -> None:
            return None

    class IncompleteImageProcessor:
        def validate_config(self, config: object, app_settings: object) -> None:
            return None

    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    make_plugin(root, plugin_id="com.example.processor", kind="image_processor_plugin")
    registry = PluginRuntime(AppConfig(), root, mode="off")
    site_record = registry.records["com.example.gallery"]
    processor_record = registry.records["com.example.processor"]
    registry.loader.register_class(site_record, IncompleteSitePlugin)
    registry.loader.register_class(processor_record, IncompleteImageProcessor)

    with pytest.raises(PluginError, match="does not implement"):
        registry.site_instance(site_record)
    with pytest.raises(PluginError, match="does not implement"):
        registry.processor_instance(processor_record)


def test_config_tree_profile_is_fixed_and_psl_layers(tmp_path: Path) -> None:
    (tmp_path / "app.yaml").write_text(
        "profile: {default: alpha}\nnetwork: {request_timeout_seconds: 1}\n", encoding="utf-8"
    )
    for relative, timeout in (
        ("profiles/alpha/app.yaml", 2),
        ("sites/global.yaml", 3),
        ("profiles/alpha/sites/global.yaml", 4),
        ("sites/example.co.uk.yaml", 5),
        ("profiles/alpha/sites/example.co.uk.yaml", 6),
        ("sites/book.example.co.uk.yaml", 7),
        ("profiles/alpha/sites/book.example.co.uk.yaml", 8),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"network: {{request_timeout_seconds: {timeout}}}\n", encoding="utf-8")
    value = load_application_config(
        tmp_path / "app.yaml", site="book.example.co.uk", runtime_override={"network": {"max_attempts": 4}}
    )
    assert value.profile.default == "alpha"
    assert value.network.request_timeout_seconds == 8
    assert value.network.max_attempts == 4
    assert site_file_name("2001:db8::1") == "ipv6-20010db8000000000000000000000001.yaml"

    (tmp_path / "profiles" / "alpha" / "app.yaml").write_text("profile: {default: beta}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="profile is only allowed"):
        load_application_config(tmp_path / "app.yaml")


def test_signed_directory_plugin_is_pinned_and_merges_private_config(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root)
    trust_plugin(root, directory.resolve())
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "strict"},
            "plugin_settings": {"com.example.gallery": {"config": {"persisted": True, "nested": {"two": 2}}}},
        }
    )
    registry = PluginRuntime(config, root, mode="strict")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    record, _ = registry.resolve("https://example.test/a", fallback_enabled=False)
    module_namespace = registry.module_namespace(record)
    assert module_namespace is not None
    assert module_namespace.startswith("_image_downloader_plugins.com_example_gallery_")
    effective = registry.effective_config(record, {"com.example.gallery": {"nested": {"three": 3}}})
    assert effective == {"author": True, "persisted": True, "nested": {"one": 1, "two": 2, "three": 3}}

    service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()
    try:
        selected, _, context, _ = service._operation(
            "https://example.test/a",
            {"com.example.gallery": {"inline": 1}},
        )
        assert service._python_log_namespaces(selected, include_processors=True) == (
            service.registry.module_namespace(selected),
        )
        assert context.config["author"] is True and context.config["inline"] == 1
        assert "headers" not in context.app_settings["network"]
        with pytest.raises(TypeError):
            context.manifest["id"] = "changed"  # type: ignore[index]
    finally:
        import asyncio

        asyncio.run(service.close())


def test_configured_match_can_enable_an_alternate_host_and_reports_selection(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root, configured_match=True)
    trust_plugin(root, directory)
    config = AppConfig.model_validate(
        {
            "plugin_settings": {"com.example.gallery": {"config": {"alternate_host": "alternate.test"}}},
            "security": {"plugin_verification": "strict"},
        }
    )
    registry = PluginRuntime(config, root, mode="strict")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    record, _ = registry.resolve("https://alternate.test/item", fallback_enabled=False)

    assert record.id == "com.example.gallery"
    assert registry.selection_diagnostics == ({"id": "com.example.gallery", "matcher": "configured", "matched": True},)


def test_doctor_uses_configured_matching_for_a_bare_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root, configured_match=True)
    trust_plugin(root, directory)
    config = tmp_path / "app.yaml"
    config.write_text(
        "\n".join(
            (
                "storage:",
                f"  data_root: {str((tmp_path / 'data').resolve())}",
                "plugins:",
                f"  root: {root}",
                "plugin_settings:",
                "  com.example.gallery:",
                "    config:",
                "      alternate_host: alternate.test",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    args = build_parser().parse_args(["doctor", "--config", str(config), "--host", "alternate.test", "--json"])

    assert asyncio.run(doctor(args)) == EXIT_SUCCESS
    report = json.loads(capsys.readouterr().out)
    assert report["configuration"]["selection"] == [
        {"id": "com.example.gallery", "matcher": "configured", "matched": True}
    ]


def test_equal_match_and_catalog_priorities_remain_an_error(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    first = make_plugin(root)
    second = make_plugin(root, plugin_id="com.example.second")
    trust_plugin(root, first)
    trust_plugin(root, second)
    registry = PluginRuntime(
        AppConfig.model_validate({"security": {"plugin_verification": "strict"}}),
        root,
        mode="strict",
    )
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    with pytest.raises(PluginError, match="priority conflict"):
        registry.resolve("https://example.test/item", fallback_enabled=False)


def test_runtime_override_for_a_non_winner_is_rejected_after_selection(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    first = make_plugin(root)
    second = make_plugin(root, plugin_id="com.example.second", configured_match=True)
    trust_plugin(root, first)
    trust_plugin(root, second)
    config = AppConfig.model_validate({"security": {"plugin_verification": "strict"}})
    registry = PluginRuntime(config, root, mode="strict")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    overrides = {"com.example.second": {"alternate_host": "other.test"}}
    winner, _ = registry.resolve("https://example.test/item", fallback_enabled=False, overrides=overrides)
    assert winner.id == "com.example.gallery"
    with pytest.raises(ConfigurationError, match="unselected plugin"):
        registry.validate_operation_overrides(winner, overrides)


def test_isolated_output_uses_canonical_host_then_plugin_id(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root)
    trust_plugin(root, directory)
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "output": {"isolate_by_plugin": True},
            "security": {"plugin_verification": "strict"},
        }
    )
    service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()
    try:
        record = service.registry.records["com.example.gallery"]
        assert service._output_filesystem(record, "https://Example.Test/item").root == (
            tmp_path / "data" / "profiles" / "default" / "downloads" / "example.test" / "com.example.gallery"
        )
        assert service._output_filesystem(record, "https://[2001:db8::1]/item").root.name == "com.example.gallery"
        assert service._output_filesystem(record, "https://[2001:db8::1]/item").root.parent.name == (
            "ipv6-20010db8000000000000000000000001"
        )
    finally:
        asyncio.run(service.close())


def test_explicit_nondefault_profile_requires_an_overlay(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    main.write_text("profile: {default: default}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="configuration file not found"):
        load_application_config(main, profile="work")


def test_strict_untrusted_and_fallback_selection(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root)
    config = AppConfig.model_validate({"security": {"plugin_verification": "strict"}})
    registry = PluginRuntime(config, root, mode="strict")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    assert any(not item.loaded for item in registry.diagnostics)
    with pytest.raises(PluginError, match="fallback is disabled"):
        registry.resolve("https://unmatched.test/a", fallback_enabled=False)


def test_manifest_rejects_unknown_fields_and_descriptor_is_not_api(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root)
    raw = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    raw["manifest"]["unexpected"] = True
    (directory / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PluginError, match="schema"):
        read_manifest(directory.resolve())
    import image_downloader

    assert not hasattr(image_downloader, "PluginDescriptor")


def test_install_stages_then_creates_catalog_and_target(tmp_path: Path) -> None:
    source = make_plugin((tmp_path / "source").resolve())
    root = (tmp_path / "installed" / "plugins").resolve()
    entry = install_plugin(root, source.resolve())
    assert entry.id == "com.example.gallery"
    assert (root / "site_plugins" / source.name / "manifest.json").is_file()
    assert PluginCatalog.load(root / "catalog.json").find(entry.id) == entry


@pytest.mark.parametrize("operation", ("trust", "install"))
def test_trust_and_install_share_signed_source_validation(tmp_path: Path, operation: str) -> None:
    source = make_plugin((tmp_path / "source").resolve())
    wrapper = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    signature = bytearray(base64.b64decode(wrapper["signature"]))
    signature[0] ^= 1
    wrapper["signature"] = base64.b64encode(signature).decode("ascii")
    (source / "manifest.json").write_text(json.dumps(wrapper), encoding="utf-8")
    root = (tmp_path / "plugins").resolve()

    with pytest.raises(PluginError, match="plugin signature is invalid"):
        if operation == "trust":
            trust_plugin(root, source)
        else:
            install_plugin(root, source)


@pytest.mark.parametrize("operation", ("trust", "install"))
def test_trust_and_install_share_file_tree_validation(tmp_path: Path, operation: str) -> None:
    source = make_plugin((tmp_path / "source").resolve())
    (source / "entry.source").write_text("tampered", encoding="utf-8")
    root = (tmp_path / "plugins").resolve()

    with pytest.raises(PluginError, match="plugin file tree hash does not match"):
        if operation == "trust":
            trust_plugin(root, source)
        else:
            install_plugin(root, source)


@pytest.mark.parametrize("operation", ("trust", "install"))
def test_trust_and_install_share_catalog_transition_policy(tmp_path: Path, operation: str) -> None:
    first = make_plugin((tmp_path / "first-source").resolve())
    second = make_plugin((tmp_path / "second-source").resolve())
    root = (tmp_path / "plugins").resolve()

    if operation == "trust":
        trust_plugin(root, first)
    else:
        install_plugin(root, first)

    with pytest.raises(ConfigurationError, match="content cannot change without a version change"):
        if operation == "trust":
            trust_plugin(root, second)
        else:
            install_plugin(root, second)


def test_install_updates_existing_plugin_id_when_source_directory_name_changes(tmp_path: Path) -> None:
    source = make_plugin((tmp_path / "source").resolve())
    renamed_source = (tmp_path / "renamed-source").resolve()
    shutil.copytree(source, renamed_source)
    root = (tmp_path / "installed" / "plugins").resolve()

    install_plugin(root, source.resolve())
    install_plugin(root, renamed_source)

    installed = [path for path in (root / "site_plugins").iterdir() if path.is_dir()]
    assert installed == [root / "site_plugins" / source.name]
    assert read_manifest(installed[0]).id == "com.example.gallery"


def test_install_rolls_back_new_target_when_catalog_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_plugin((tmp_path / "source").resolve())
    root = (tmp_path / "installed" / "plugins").resolve()

    def fail_catalog(*_args: object, **_kwargs: object) -> None:
        raise OSError("catalog unavailable")

    monkeypatch.setattr(plugin_management, "_write_catalog_unlocked", fail_catalog)
    with pytest.raises(OSError, match="catalog unavailable"):
        install_plugin(root, source.resolve())
    assert not (root / "site_plugins" / source.name).exists()


def test_doctor_reports_runtime_and_plugin_provenance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config" / "app.yaml"
    config.parent.mkdir()
    config_content = "\n".join(
        (
            "storage:",
            f"  data_root: {(tmp_path / 'data').resolve()}",
            "plugins:",
            f"  root: {(tmp_path / 'configured-plugins').resolve()}",
            "network:",
            "  headers:",
            "    Authorization: Bearer test-token",
            "plugin_settings:",
            "  com.example.gallery:",
            "    config:",
            "      mode: test",
            "    secrets:",
            "      api_key: EXAMPLE_API_KEY",
            "",
        )
    )
    config.write_text(
        config_content,
        encoding="utf-8",
    )
    root = (tmp_path / "plugins").resolve()
    directory = make_plugin(root)
    trust_plugin(root, directory.resolve())
    args = build_parser().parse_args(["doctor", "--config", str(config), "--plugin-root", str(root), "--json"])

    assert asyncio.run(doctor(args)) == EXIT_SUCCESS
    report = json.loads(capsys.readouterr().out)

    assert report["application"]["version"] == "0.0.0.1b0"
    assert Path(report["application"]["root_directory"]).is_absolute()
    assert Path(report["library"]["root_directory"]).name == "image_downloader"
    assert report["plugin_root"] == str(root)
    assert report["configuration"]["effective"]["network"]["headers"]["Authorization"] == "<redacted>"
    assert (
        report["configuration"]["effective"]["plugin_settings"]["com.example.gallery"]["secrets"]["api_key"]
        == "<redacted>"
    )
    plugin = next(item for item in report["loaded_plugins"] if item["id"] == "com.example.gallery")
    assert plugin["publisher"] == "com.example"
    assert plugin["version"] == "1.0"
    assert plugin["verification"] == "trusted"
    assert plugin["effective_config"]["mode"] == "test"
    assert any(item["id"] == "core.generic-html" for item in report["loaded_plugins"])


def test_doctor_human_output_includes_runtime_details(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "app.yaml"
    config_content = "\n".join(
        (
            "storage:",
            f"  data_root: {(tmp_path / 'data').resolve()}",
            "plugins:",
            f"  root: {(tmp_path / 'configured-plugins').resolve()}",
            "",
        )
    )
    config.write_text(
        config_content,
        encoding="utf-8",
    )
    cookie = tmp_path / "data" / "profiles" / "default" / "cookie"
    cookie.mkdir(parents=True)
    (cookie / "cookies.enc").write_text("not a valid encrypted cookie file", encoding="utf-8")
    root = (tmp_path / "plugins").resolve()
    args = build_parser().parse_args(["doctor", "--config", str(config), "--plugin-root", str(root)])

    assert asyncio.run(doctor(args)) == EXIT_SUCCESS
    output = capsys.readouterr().out
    assert "application:" in output
    assert "library:" in output
    assert f"plugin root: {root}" in output
    assert "configuration (sensitive values redacted):" in output
    assert "loaded plugins:" in output
    assert "publisher=core version=3" in output


def test_selected_dynamic_plugin_logging_is_captured_during_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = (tmp_path / "plugins").resolve()
        directory = make_plugin(root, runnable=True)
        trust_plugin(root, directory)
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str((tmp_path / "data").resolve())},
                "plugins": {"root": str(root)},
                "security": {"plugin_verification": "strict"},
                "download": {"allow_empty_chapter_manifest": True},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()
        try:
            await service.run("https://example.test/a")
        finally:
            await service.close()

        detail = (service.logs.root / "debug.log").read_text(encoding="utf-8")
        assert "plugin-operation-marker" in detail

    asyncio.run(scenario())


def test_download_log_capture_includes_enabled_processor_namespace(tmp_path: Path) -> None:
    root = (tmp_path / "plugins").resolve()
    site_directory = make_plugin(root)
    processor_directory = make_plugin(root, plugin_id="com.example.resize", kind="image_processor_plugin")
    trust_plugin(root, site_directory)
    trust_plugin(root, processor_directory)
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "strict"},
            "image_processors": {"chain": ["com.example.resize"]},
        }
    )
    service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()
    try:
        selected, _, _, _ = service._operation("https://example.test/a")
        namespaces = service._python_log_namespaces(selected, include_processors=True)
        processor = service.registry.records["com.example.resize"]
        assert namespaces == (
            service.registry.module_namespace(selected),
            service.registry.module_namespace(processor),
        )
        assert service._python_log_namespaces(selected, include_processors=False) == (
            service.registry.module_namespace(selected),
        )
    finally:
        asyncio.run(service.close())
