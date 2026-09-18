"""plugin CLI command implementation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..configuration.models import AppConfig
from ..exceptions import ConfigurationError
from ..plugins.management import install_plugin, revoke_plugin, trust_plugin
from ..plugins.plugin_manifest import PluginCatalog, read_manifest
from ..plugins.runtime import PluginRuntime
from .constants import EXIT_SUCCESS
from .setup import (
    _config_for,
    _plugin_root,
)
from .validation import _reject_command_options


class PluginCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        _reject_command_options(
            args,
            (
                "plugin_config",
                "plugin_config_file",
                "fallback_generic",
                "host",
                "no_console_log",
                "list_updated_urls",
                "export_cookies",
                "import_cookies",
                "import_browser_cookies",
                "existing_file",
                "image_format",
            ),
            "plugin",
        )
        return plugin_command(args)


def _confirm(args: argparse.Namespace, description: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty() or input(f"{description}. Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise ConfigurationError("plugin change was not confirmed (pass --yes for non-interactive use)")


def plugin_command(args: argparse.Namespace) -> int:
    words = list(args.command_args)
    if not words:
        raise ConfigurationError("plugin command is required: install, trust, revoke, or list")
    config, _, _, _ = _config_for(args, None, allow_root_setup=True)
    command, root = words.pop(0), _plugin_root(args, config)
    yes = args.yes
    if command == "list":
        if words:
            raise ConfigurationError("plugin list takes no arguments")
        try:
            catalog = (
                PluginCatalog.load(root / "catalog.json") if (root / "catalog.json").exists() else PluginCatalog(())
            )
            catalog_warning = None
        except ConfigurationError as exc:
            catalog, catalog_warning = PluginCatalog(()), str(exc)
        registry = PluginRuntime(
            AppConfig.model_validate({"security": {"plugin_verification": "off"}}),
            root,
            mode="off",
        )
        payload = {
            "plugin_root": str(root),
            "plugins": [entry.as_json() for entry in catalog.entries],
            "diagnostics": [
                item.__dict__
                if hasattr(item, "__dict__")
                else {
                    "name": item.name,
                    "source": item.source,
                    "loaded": item.loaded,
                    "detail": item.detail,
                    "warning": item.warning,
                }
                for item in registry.diagnostics
            ],
            "catalog_warning": catalog_warning,
        }
        print(
            json.dumps(payload, ensure_ascii=False)
            if args.json_output
            else "\n".join(
                f"{entry.id} {entry.version} {'revoked' if entry.revoked else 'trusted'}" for entry in catalog.entries
            )
        )
        return EXIT_SUCCESS
    if command in {"install", "trust"}:
        if len(words) != 1:
            raise ConfigurationError(f"plugin {command} requires one absolute plugin directory")
        source = Path(words[0])
        if not source.is_absolute():
            raise ConfigurationError(f"plugin {command} source must be an absolute directory")
        manifest = read_manifest(source)
        fingerprint = str(manifest.value["key_id"])
        summary_fields = (
            f"{command} {manifest.id}",
            f"publisher={manifest.value['publisher']}",
            f"kind={manifest.kind}",
            f"version={manifest.value['version']}",
            f"key={fingerprint}",
            f"tree={manifest.value['file_tree_sha256']}",
        )
        summary = " ".join(summary_fields)
        if not yes:
            _confirm(args, summary)
        entry = (
            install_plugin(root, source, selection_priority=args.selection_priority)
            if command == "install"
            else trust_plugin(root, source, selection_priority=args.selection_priority)
        )
        print(
            json.dumps(entry.as_json(), ensure_ascii=False)
            if args.json_output
            else f"{command}ed {entry.id} ({entry.version})"
        )
        return EXIT_SUCCESS
    if command == "revoke":
        if len(words) != 1:
            raise ConfigurationError("plugin revoke requires an ID")
        if not yes:
            _confirm(args, f"revoke plugin {words[0]}")
        entry = revoke_plugin(root, words[0])
        print(json.dumps(entry.as_json(), ensure_ascii=False) if args.json_output else f"revoked {entry.id}")
        return EXIT_SUCCESS
    raise ConfigurationError("unknown plugin command")
