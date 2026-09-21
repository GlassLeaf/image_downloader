"""Plugin selection and per-operation runtime lifecycle."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from ..configuration.layers import deep_merge
from ..configuration.models import AppConfig, PluginSettings
from ..exceptions import ConfigurationError, PluginError
from ..immutable import freeze_json
from ..ports import ImageProcessor, SitePlugin
from ..storage.path_safety import existing_directory
from .lifecycle import (
    PluginClassLoader,
    PluginDiagnostic,
    PluginDiscovery,
    PluginManifestVerifier,
    PluginRecord,
    PluginRegistry,
)
from .plugin_invoker import PluginInvoker
from .plugin_manifest import (
    PluginCatalog,
    PluginConfigOverrides,
    PluginManifest,
    _sha256,
    canonical_jcs,
)


def _validate_plugin_config(
    plugin_id: str,
    plugin: SitePlugin | ImageProcessor,
    config: Mapping[str, object],
    app_settings: Mapping[str, object],
) -> None:
    PluginInvoker(plugin_id).validate_config(plugin, config, app_settings)


def _builtin_manifest() -> PluginManifest:
    value = {
        "schema_version": 1,
        "id": "core.generic-html",
        "publisher": "core",
        "version": "3",
        "api_version": "3",
        "kind": "site_plugin",
        "capabilities": [],
        "match_priority": -(2**31),
        "entry": {"file": "builtin", "class": "GenericHtmlPlugin"},
        "config_file": "builtin.yaml",
        "public_key": base64.b64encode(bytes(32)).decode("ascii"),
        "key_id": _sha256(bytes(32)),
        "file_tree": {"builtin.yaml": _sha256(b"config: {}\n")},
        "file_tree_sha256": _sha256(canonical_jcs({"builtin.yaml": _sha256(b"config: {}\n")})),
    }
    return PluginManifest(
        cast(Mapping[str, Any], freeze_json(value)),
        "",
        _sha256(canonical_jcs(value)),
        Path(__file__).resolve().parents[1],
    )


class PluginSelector:
    """Select one enabled site plugin using manifest and catalog priorities."""

    def __init__(self, loader: PluginClassLoader) -> None:
        self.loader = loader

    def select(
        self,
        records: Mapping[str, PluginRecord],
        url: str,
        *,
        fallback_enabled: bool,
        overrides: PluginConfigOverrides | None,
        enabled: Callable[[PluginRecord], bool],
        effective_config: Callable[[PluginRecord, PluginConfigOverrides | None], Mapping[str, Any]],
        app_settings: Mapping[str, Any],
    ) -> tuple[PluginRecord, SitePlugin, tuple[Mapping[str, Any], ...]]:
        matches: list[tuple[PluginRecord, SitePlugin]] = []
        diagnostics: list[Mapping[str, Any]] = []
        for record in records.values():
            if record.kind != "site_plugin" or record.builtin or not enabled(record):
                continue
            instance = self.loader.site_instance(record)
            matched, matcher = PluginInvoker(record.id).matches(
                instance,
                url,
                config=effective_config(record, overrides),
                app_settings=app_settings,
            )
            diagnostics.append({"id": record.id, "matcher": matcher, "matched": matched})
            if matched:
                matches.append((record, instance))
        if matches:
            record, instance = self._winner(matches)
        elif fallback_enabled:
            record = self._fallback(records)
            instance = self.loader.site_instance(record)
            matched, _ = PluginInvoker(record.id).matches(instance, url, config={}, app_settings=app_settings)
            if not matched:
                raise PluginError("no plugin can handle URL")
            diagnostics.append({"id": record.id, "matcher": "builtin", "matched": True})
        else:
            raise PluginError("no enabled site plugin matched and generic fallback is disabled")
        return record, instance, tuple(diagnostics)

    @staticmethod
    def _winner(matches: list[tuple[PluginRecord, SitePlugin]]) -> tuple[PluginRecord, SitePlugin]:
        priority = max(int(record.manifest.value["match_priority"]) for record, _ in matches)
        highest = [item for item in matches if item[0].manifest.value["match_priority"] == priority]
        catalog_priority = max(record.catalog.selection_priority if record.catalog else 0 for record, _ in highest)
        winners = [
            item
            for item in highest
            if (item[0].catalog.selection_priority if item[0].catalog else 0) == catalog_priority
        ]
        if len(winners) != 1:
            raise PluginError("plugin priority conflict for URL")
        return winners[0]

    @staticmethod
    def _fallback(records: Mapping[str, PluginRecord]) -> PluginRecord:
        fallback = records.get("core.generic-html")
        if fallback is None:
            raise PluginError("generic fallback is unavailable")
        return fallback


class PluginRuntime:
    """Coordinate plugin configuration, loading and selection around a record snapshot."""

    def __init__(self, config: AppConfig, plugin_root: Path, *, mode: str) -> None:
        self.config, self.plugin_root, self.mode = config, existing_directory(plugin_root, "plugin root"), mode
        discovery = PluginDiscovery(self.plugin_root).discover()
        verification = PluginManifestVerifier(self.plugin_root, mode=mode).verify(discovery)
        self.registry = PluginRegistry(verification.records, catalog=verification.catalog)
        self.diagnostics = [*discovery.diagnostics, *verification.diagnostics]
        self.selection_diagnostics: tuple[Mapping[str, Any], ...] = ()
        self.loader = PluginClassLoader()
        self.selector = PluginSelector(self.loader)

    @property
    def records(self) -> Mapping[str, PluginRecord]:
        return self.registry.records

    @property
    def catalog(self) -> PluginCatalog | None:
        return self.registry.catalog

    def register_builtin(self, plugin_class: type[object]) -> None:
        manifest = _builtin_manifest()
        record = PluginRecord(
            manifest,
            cast(Mapping[str, Any], freeze_json({})),
            None,
            True,
        )
        self.loader.register_class(record, plugin_class)
        self.registry = PluginRegistry({**self.records, record.id: record}, catalog=self.catalog)
        self.diagnostics.append(PluginDiagnostic(manifest.id, "builtin", True))

    def _setting(self, plugin_id: str) -> PluginSettings | None:
        return self.config.plugin_settings.get(plugin_id)

    def validate_settings(self) -> None:
        invalid = set(self.config.plugin_settings) - set(self.records)
        if invalid:
            raise ConfigurationError(f"plugin_settings contains unknown plugin ID: {sorted(invalid)[0]}")
        if "core.generic-html" in self.config.plugin_settings:
            raise ConfigurationError("core.generic-html does not accept plugin_settings")
        for record in self.records.values():
            settings = self._setting(record.id)
            if settings and settings.secrets and record.kind != "site_plugin":
                raise ConfigurationError(f"image processor {record.id} cannot declare secrets")
        for plugin_id in self.config.image_processors.chain:
            configured_record = self.records.get(plugin_id)
            if configured_record is None or configured_record.kind != "image_processor_plugin":
                raise ConfigurationError(f"configured image processor is unavailable: {plugin_id}")

    def enabled(self, record: PluginRecord) -> bool:
        setting = self._setting(record.id)
        return setting is None or setting.enabled

    def effective_config(
        self,
        record: PluginRecord,
        overrides: PluginConfigOverrides | None = None,
    ) -> Mapping[str, Any]:
        setting = self._setting(record.id)
        result = deep_merge(record.author_defaults, setting.config if setting else {})
        if overrides and record.id in overrides:
            incoming = overrides[record.id]
            if not isinstance(incoming, Mapping):
                raise ConfigurationError(f"runtime plugin override for {record.id} must be a mapping")
            result = deep_merge(result, incoming)
        return cast(Mapping[str, Any], freeze_json(result))

    def validate_operation_overrides(
        self,
        site_record: PluginRecord,
        overrides: PluginConfigOverrides | None,
    ) -> None:
        if not overrides:
            return
        allowed = {site_record.id}
        allowed.update(
            plugin_id
            for plugin_id in self.config.image_processors.chain
            if (record := self.records.get(plugin_id)) is not None and self.enabled(record)
        )
        unexpected = set(overrides) - allowed
        if unexpected:
            raise ConfigurationError(f"runtime plugin config targets an unselected plugin: {sorted(unexpected)[0]}")
        if any(not isinstance(value, Mapping) for value in overrides.values()):
            raise ConfigurationError("runtime plugin override must be a mapping")

    def validate_candidate_overrides(self, overrides: PluginConfigOverrides | None) -> None:
        """Validate override shape before configuration-aware site matching.

        A configured match may make a plugin eligible for a host that its standard
        ``matches`` hook would reject, so candidate validation cannot wait until
        after a winner has been selected.
        """
        if not overrides:
            return
        allowed = {
            record.id
            for record in self.records.values()
            if (record.kind == "site_plugin" and not record.builtin and self.enabled(record))
        }
        allowed.update(
            plugin_id
            for plugin_id in self.config.image_processors.chain
            if (record := self.records.get(plugin_id)) is not None and self.enabled(record)
        )
        unexpected = set(overrides) - allowed
        if unexpected:
            raise ConfigurationError(f"runtime plugin config targets an unavailable plugin: {sorted(unexpected)[0]}")
        if any(not isinstance(value, Mapping) for value in overrides.values()):
            raise ConfigurationError("runtime plugin override must be a mapping")

    def site_instance(self, record: PluginRecord) -> SitePlugin:
        return self.loader.site_instance(record)

    def processor_instance(self, record: PluginRecord) -> ImageProcessor:
        return self.loader.processor_instance(record)

    def module_namespace(self, record: PluginRecord) -> str | None:
        return self.loader.module_namespace(record)

    def close(self) -> None:
        """Release all dynamically imported modules owned by this runtime."""
        self.loader.close()

    def _typed_instance(self, record: PluginRecord) -> SitePlugin | ImageProcessor:
        return self.loader.typed_instance(record)

    def prepare(self) -> None:
        self.validate_settings()
        for record in list(self.records.values()):
            if record.builtin:
                continue
            should_import = (record.kind == "site_plugin" and self.enabled(record)) or (
                record.kind == "image_processor_plugin"
                and record.id in self.config.image_processors.chain
                and self.enabled(record)
            )
            if not should_import:
                if record.kind == "image_processor_plugin" and record.id in self.config.image_processors.chain:
                    self.diagnostics.append(
                        PluginDiagnostic(
                            record.id, str(record.manifest.directory), True, "disabled processor skipped", True
                        )
                    )
                continue
            try:
                self._typed_instance(record)
            except Exception as exc:
                self.loader.unload(record)
                remaining = {name: item for name, item in self.records.items() if name != record.id}
                self.registry = PluginRegistry(remaining, catalog=self.catalog)
                self.diagnostics.append(
                    PluginDiagnostic(record.id, str(record.manifest.directory), False, f"{type(exc).__name__}: {exc}")
                )

    def doctor_validate(self, overrides: PluginConfigOverrides | None = None) -> None:
        """Import and validate every static plugin, including disabled units."""
        self.validate_settings()
        for record in list(self.records.values()):
            try:
                instance = self._typed_instance(record)
                _validate_plugin_config(
                    record.id,
                    instance,
                    self.effective_config(record, overrides),
                    safe_app_settings(self.config),
                )
            except Exception as exc:
                self.diagnostics.append(
                    PluginDiagnostic(record.id, str(record.manifest.directory), False, f"{type(exc).__name__}: {exc}")
                )

    def validate_all(self, overrides: PluginConfigOverrides | None = None) -> None:
        self.validate_settings()
        for record in self.records.values():
            instance = self._typed_instance(record)
            _validate_plugin_config(
                record.id,
                instance,
                self.effective_config(record, overrides),
                safe_app_settings(self.config),
            )

    def resolve(
        self,
        url: str,
        *,
        fallback_enabled: bool,
        overrides: PluginConfigOverrides | None = None,
    ) -> tuple[PluginRecord, SitePlugin]:
        self.validate_candidate_overrides(overrides)
        app_settings = safe_app_settings(self.config)
        record, instance, diagnostics = self.selector.select(
            self.records,
            url,
            fallback_enabled=fallback_enabled,
            overrides=overrides,
            enabled=self.enabled,
            effective_config=self.effective_config,
            app_settings=app_settings,
        )
        self.selection_diagnostics = diagnostics
        _validate_plugin_config(record.id, instance, self.effective_config(record, overrides), app_settings)
        return record, instance

    def processor(
        self,
        plugin_id: str,
        overrides: PluginConfigOverrides | None = None,
    ) -> tuple[PluginRecord, ImageProcessor] | None:
        record = self.records.get(plugin_id)
        if record is None or record.kind != "image_processor_plugin":
            raise ConfigurationError(f"configured image processor is unavailable: {plugin_id}")
        if not self.enabled(record):
            return None
        instance = self.processor_instance(record)
        _validate_plugin_config(
            record.id,
            instance,
            self.effective_config(record, overrides),
            safe_app_settings(self.config),
        )
        return record, instance


def safe_app_settings(config: AppConfig) -> Mapping[str, Any]:
    """The immutable app view exposed to v3 plugins, with credentials removed."""
    network = config.network.model_dump(warnings=False)
    network.pop("headers", None)
    network.pop("proxy", None)
    return cast(
        Mapping[str, Any],
        freeze_json(
            {
                "output": config.output.model_dump(warnings=False),
                "media": config.media.model_dump(warnings=False),
                "download": config.download.model_dump(warnings=False),
                "network": network,
            }
        ),
    )
