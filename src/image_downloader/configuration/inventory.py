"""Safe, deterministic inventory of recognized configuration scopes."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from ..exceptions import ConfigurationError
from ..storage.path_safety import existing_directory, existing_regular_file
from .hosts import normalize_host, site_file_name


def directory_children(path: Path) -> tuple[Path, ...]:
    directory = existing_directory(path, "configuration directory")
    if not directory.exists():
        return ()
    result = []
    for child in sorted(directory.iterdir()):
        existing_directory(child, "configuration scope", required=True)
        result.append(child)
    return tuple(result)


def host_from_name(name: str) -> str:
    if name.startswith("ipv6-"):
        try:
            normalized = str(ipaddress.IPv6Address(int(name[5:], 16)))
        except ValueError as exc:
            raise ConfigurationError("invalid IPv6 site filename") from exc
    else:
        normalized, _ = normalize_host(name)
    if site_file_name(normalized)[:-5] != name:
        raise ConfigurationError("site directory must use its normalized host name")
    return normalized


def _scope_files(directory: Path, *, main_scope: bool, legacy: bool) -> set[Path]:
    files: set[Path] = set()
    if not main_scope:
        app = directory / "app.yaml"
        if existing_regular_file(app, "configuration file", required=False) is not None:
            files.add(app)
    if not legacy:
        auth = directory / "auth.yaml"
        if existing_regular_file(auth, "authentication file", required=False) is not None:
            files.add(auth)
        plugins = existing_directory(directory / "plugin-settings", "plugin settings directory")
        if plugins.exists():
            for plugin in sorted(plugins.iterdir()):
                if plugin.suffix == ".yaml":
                    existing_regular_file(plugin, "plugin settings file", required=True)
                    files.add(plugin)
    return files


def inventory(main: Path, *, legacy: bool = False) -> tuple[tuple[Path, ...], tuple[str, ...], tuple[str, ...]]:
    root = main.parent
    files: set[Path] = {main}
    profiles: set[str] = set()
    hosts: set[str] = set()

    def sites(directory: Path) -> None:
        site_root = existing_directory(directory / "sites", "site configuration directory")
        if not site_root.exists():
            return
        for child in sorted(site_root.iterdir()):
            if legacy:
                if child.suffix != ".yaml":
                    continue
                existing_regular_file(child, "site configuration file", required=True)
                name = child.stem
                files.add(child)
            else:
                if child.suffix == ".yaml":
                    raise ConfigurationError("legacy site layout requires 'config migrate'")
                existing_directory(child, "site configuration directory", required=True)
                name = child.name
                files.update(_scope_files(child, main_scope=False, legacy=legacy))
            if name != "global":
                hosts.add(host_from_name(name))

    existing_regular_file(main, "main configuration", required=True)
    files.update(_scope_files(root, main_scope=True, legacy=legacy))
    sites(root)
    for directory in directory_children(root / "profiles"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", directory.name):
            raise ConfigurationError("invalid configuration profile directory")
        profiles.add(directory.name)
        files.update(_scope_files(directory, main_scope=False, legacy=legacy))
        sites(directory)
    return tuple(sorted(files)), tuple(sorted(profiles)), tuple(sorted(hosts))


def validate_tree(main: Path) -> dict[str, object]:
    from .layers import resolve_application_config

    files, profiles, hosts = inventory(main)
    default = resolve_application_config(main, require_config=True)
    cases = 0
    for profile in sorted(set(profiles) | {default.selected_profile}):
        for host in (None, *hosts):
            resolve_application_config(main, profile=profile, site=host, require_config=True)
            cases += 1
    return {
        "valid": True,
        "config_version": 2,
        "files": [str(path) for path in files],
        "profiles": sorted(set(profiles) | {default.selected_profile}),
        "cases": cases,
        "plugin_validation_complete": False,
    }
