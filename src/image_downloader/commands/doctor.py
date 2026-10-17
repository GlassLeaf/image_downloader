"""doctor CLI command implementation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..configuration.hosts import normalize_host
from ..configuration.paths import resolve_paths
from ..exceptions import ConfigurationError, PluginError
from ..plugins.plugin_manifest import effective_verification_mode
from ..plugins.runtime import PluginRuntime
from .constants import EXIT_PLUGIN, EXIT_SUCCESS
from .errors import _render_error
from .prepared import DoctorArguments, PreparedCommand
from .reporting import _application_version, _doctor_plugin_details, _doctor_redact, _print_doctor_report
from .setup import (
    _app_override,
    _fallback,
    _plugin_root,
    _resolved_config_for,
)
from .validation import validate_arguments


class DoctorCommandHandler:
    async def handle(self, command: PreparedCommand) -> int:
        return _doctor(command, raise_errors=True)


async def doctor(args: argparse.Namespace, *, raise_errors: bool = False) -> int:
    try:
        return _doctor(validate_arguments(args), raise_errors=raise_errors)
    except (ConfigurationError, PluginError) as exc:
        if raise_errors:
            raise
        return _render_error(exc, json_output=args.json_output, operation="doctor")


def _doctor(command: PreparedCommand, *, raise_errors: bool = False) -> int:
    args = command.arguments
    assert isinstance(args, DoctorArguments)
    registry: PluginRuntime | None = None
    try:
        target = args.host
        site: str | None = None
        selection_url: str | None = None
        if target:
            parsed = urlparse(target)
            if parsed.scheme:
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ConfigurationError("doctor --host must be a bare host or absolute HTTP(S) URL")
                site, selection_url = parsed.hostname, target
            else:
                site, _ = normalize_host(target)
                selection_url = f"https://{site}/"
        resolved = _resolved_config_for(args, site)
        config = resolved.config
        config_root = resolved.config_root
        paths = resolve_paths(config)
        registry = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
            plugin_verification_override=args.plugin_verification_override,
        ).compose_registry()
        overrides = command.plugin_config
        registry.doctor_validate(overrides)
        if selection_url:
            fallback_override = _fallback(args)
            fallback_enabled = config.fallback.generic_html.enabled if fallback_override is None else fallback_override
            selected, _ = registry.resolve(selection_url, fallback_enabled=fallback_enabled, overrides=overrides)
            registry.validate_operation_overrides(selected, overrides)
        diagnostics = registry.diagnostics
        healthy = not any(not item.loaded and not item.warning for item in diagnostics)
        library_root = Path(__file__).resolve().parents[1]
        version = _application_version()
        payload = {
            "healthy": healthy,
            "application": {
                "version": version,
                "root_directory": str(library_root.parent),
            },
            "library": {
                "version": version,
                "root_directory": str(library_root),
            },
            "plugin_root": str(registry.plugin_root),
            "verification": effective_verification_mode(
                config.security.plugin_verification,
                args.plugin_verification_override,
            ),
            "configuration": {
                "config_file": str(resolved.main_config_path) if resolved.main_config_path is not None else None,
                "source": resolved.source,
                "main_config_kind": resolved.main_config_kind,
                "config_root": str(config_root),
                "selected_profile": config.profile.default,
                "target_host": site,
                "target_has_url_path": selection_url is not None,
                "effective": _doctor_redact(config.model_dump(by_alias=True, warnings=False)),
                "runtime": {
                    "application_overrides": _doctor_redact(_app_override(args)),
                    "plugin_overrides": _doctor_redact(overrides),
                    "fallback_generic": args.fallback_generic,
                    "plugin_verification_override": args.plugin_verification_override,
                },
                "selection": [dict(item) for item in registry.selection_diagnostics],
                "layers": [
                    {
                        "role": layer.role,
                        "path": str(layer.path) if layer.path is not None else None,
                        "status": layer.status,
                    }
                    for layer in resolved.layers
                ],
                "origins": dict(resolved.origins),
            },
            "paths": {name: str(path) for name, path in paths.items()},
            "loaded_plugins": _doctor_plugin_details(registry, overrides),
            "plugins": [
                {
                    "name": item.name,
                    "source": item.source,
                    "loaded": item.loaded,
                    "detail": item.detail,
                    "warning": item.warning,
                }
                for item in diagnostics
            ],
        }
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            _print_doctor_report(payload)
        return EXIT_SUCCESS if healthy else EXIT_PLUGIN
    except ConfigurationError as exc:
        if raise_errors:
            raise
        return _render_error(exc, json_output=args.json_output, operation="doctor")
    except PluginError as exc:
        if raise_errors:
            raise
        return _render_error(exc, json_output=args.json_output, operation="doctor")
    finally:
        if registry is not None:
            registry.close()
