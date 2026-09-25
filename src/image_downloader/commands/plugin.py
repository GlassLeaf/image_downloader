"""plugin CLI command implementation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from ..exceptions import ConfigurationError
from ..plugins.management import install_plugin, revoke_plugin, trust_plugin, uninstall_plugin
from ..plugins.plugin_manifest import (
    PluginCatalog,
    PluginVerificationMode,
    effective_verification_mode,
    read_manifest,
)
from ..plugins.runtime import PluginRuntime
from .constants import EXIT_SUCCESS
from .setup import (
    _config_for,
    _persist_initial_user_config,
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


def _plugin_root_for_mutation(args: argparse.Namespace) -> Path:
    config, _, _, configuration_source = _config_for(
        args,
        None,
        allow_root_setup=True,
        rewrite_user_layers=True,
    )
    root = _plugin_root(args, config)
    _persist_initial_user_config(configuration_source)
    return root


def _plugin_list(args: argparse.Namespace, words: list[str]) -> int:
    if words:
        raise ConfigurationError("plugin list takes no arguments")
    config, _, _, _ = _config_for(args, None, allow_root_setup=True)
    root = _plugin_root(args, config)
    try:
        catalog = PluginCatalog.load(root / "catalog.json") if (root / "catalog.json").exists() else PluginCatalog(())
        catalog_warning = None
    except ConfigurationError as exc:
        catalog, catalog_warning = PluginCatalog(()), str(exc)
    mode = effective_verification_mode(config.security.plugin_verification, args.plugin_verification_override)
    registry = PluginRuntime(config, root, mode=mode)
    payload = {
        "plugin_root": str(root),
        "plugins": [entry.as_json() for entry in catalog.entries],
        "diagnostics": [
            {
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


def _plugin_source_mutation(args: argparse.Namespace, command: str, words: list[str]) -> int:
    if len(words) != 1:
        raise ConfigurationError(f"plugin {command} requires one absolute plugin directory")
    plugin_source = Path(words[0])
    if not plugin_source.is_absolute():
        raise ConfigurationError(f"plugin {command} source must be an absolute directory")
    management_mode = cast(PluginVerificationMode, args.plugin_verification_override or "strict")
    manifest = (
        read_manifest(plugin_source, mode=management_mode)
        if args.plugin_verification_override is not None
        else read_manifest(plugin_source)
    )
    summary_fields = [f"{command} {manifest.id}", f"kind={manifest.kind}"]
    for key, label in (
        ("publisher", "publisher"),
        ("version", "version"),
        ("key_id", "key"),
        ("file_tree_sha256", "tree"),
    ):
        if key in manifest.value:
            summary_fields.append(f"{label}={manifest.value[key]}")
    if not args.yes:
        _confirm(args, " ".join(summary_fields))
    root = _plugin_root_for_mutation(args)
    entry = (
        install_plugin(root, plugin_source, selection_priority=args.selection_priority, mode=management_mode)
        if command == "install"
        else trust_plugin(root, plugin_source, selection_priority=args.selection_priority, mode=management_mode)
    )
    print(
        json.dumps(entry.as_json(), ensure_ascii=False)
        if args.json_output
        else f"{command}ed {entry.id} ({entry.version})"
    )
    return EXIT_SUCCESS


def _plugin_id_mutation(args: argparse.Namespace, command: str, words: list[str]) -> int:
    if len(words) != 1:
        raise ConfigurationError(f"plugin {command} requires an ID")
    plugin_id = words[0]
    if not args.yes:
        _confirm(args, f"{command} plugin {plugin_id}")
    root = _plugin_root_for_mutation(args)
    if command == "revoke":
        entry = revoke_plugin(root, plugin_id)
        print(json.dumps(entry.as_json(), ensure_ascii=False) if args.json_output else f"revoked {entry.id}")
    else:
        result = uninstall_plugin(root, plugin_id)
        print(json.dumps(result.as_json(), ensure_ascii=False) if args.json_output else f"uninstalled {result.id}")
    return EXIT_SUCCESS


def plugin_command(args: argparse.Namespace) -> int:
    words = list(args.command_args)
    if not words:
        raise ConfigurationError("plugin command is required: install, trust, revoke, uninstall, or list")
    command = words.pop(0)
    if command == "list":
        return _plugin_list(args, words)
    if command in {"install", "trust"}:
        return _plugin_source_mutation(args, command, words)
    if command in {"revoke", "uninstall"}:
        return _plugin_id_mutation(args, command, words)
    raise ConfigurationError("unknown plugin command")
