"""doctor CLI command implementation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..configuration.hosts import normalize_host
from ..configuration.paths import resolve_paths
from ..exceptions import ConfigurationError, PluginError
from ..plugins.runtime import PluginRuntime
from .constants import EXIT_CONFIGURATION, EXIT_PLUGIN, EXIT_SUCCESS
from .reporting import _application_version, _doctor_plugin_details, _doctor_redact, _print_doctor_report
from .setup import (
    _app_override,
    _config_for,
    _fallback,
    _plugin_root,
    _runtime_overrides,
)
from .validation import _reject_command_options


class DoctorCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        _reject_command_options(
            args,
            (
                "selection_priority",
                "list_updated_urls",
                "export_cookies",
                "import_cookies",
                "import_browser_cookies",
            ),
            "doctor",
        )
        return await doctor(args)


async def doctor(args: argparse.Namespace) -> int:
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
        config, config_root, config_path, config_source = _config_for(args, site, allow_root_setup=True)
        paths = resolve_paths(config)
        registry = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
        ).compose_registry()
        overrides = _runtime_overrides(args)
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
            "verification": config.security.plugin_verification,
            "configuration": {
                "config_file": str(config_path),
                "source": config_source,
                "config_root": str(config_root),
                "selected_profile": config.profile.default,
                "target_host": site,
                "target_has_url_path": selection_url is not None,
                "effective": _doctor_redact(config.model_dump(by_alias=True, warnings=False)),
                "runtime": {
                    "application_overrides": _doctor_redact(_app_override(args)),
                    "plugin_overrides": _doctor_redact(overrides),
                    "fallback_generic": args.fallback_generic,
                },
                "selection": [dict(item) for item in registry.selection_diagnostics],
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
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PLUGIN
    finally:
        if registry is not None:
            registry.close()
