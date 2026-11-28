"""Doctor report rendering and safe display values."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any, cast

from ..configuration.auth_origins import OriginMatcher
from ..configuration.endpoint_origins import expand_origins, referenced_host_groups
from ..configuration.models import AppConfig, PluginConnections, PluginSettings
from ..credentials.access import compile_auth_access, resolve_auth_access
from ..plugins.runtime import PluginRuntime
from ..privacy.sensitive_values import REDACTED, redact_sensitive_values
from ..transport.endpoint_rules import expand_connections


def _application_version() -> str:
    """Return the installed distribution version, including source checkouts."""
    try:
        return distribution_version("image-downloader")
    except PackageNotFoundError:
        # ``python -m pytest`` against a source checkout has no installed
        # distribution metadata.  Reading the adjacent project metadata keeps
        # doctor useful there without pretending an unknown version is current.
        pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
        try:
            value = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            source_version = value.get("project", {}).get("version")
        except (OSError, tomllib.TOMLDecodeError):
            source_version = None
        return source_version if isinstance(source_version, str) else "unknown"


def _doctor_redact(
    value: object,
    *,
    origins: Mapping[str, str] | None = None,
    connections: Mapping[str, PluginConnections] | None = None,
) -> object:
    """Make an inspectable doctor report without printing credentials."""
    if not isinstance(value, AppConfig):
        return redact_sensitive_values(value)
    safe = cast(dict[str, Any], redact_sensitive_values(value.model_dump(by_alias=True, warnings=False)))
    # Only typed host-owned policies can bypass generic sensitive-key masking.
    # Arbitrary plugin data named auth_access remains subject to normal redaction.
    for plugin_id, settings in value.plugin_settings.items():
        plugins = safe.setdefault("plugin_settings", {})
        if not isinstance(plugins.get(plugin_id), dict):
            plugins[plugin_id] = {}
        plugins[plugin_id]["resolved_endpoints"] = _user_endpoint_previews(settings)
        access = settings.auth_access
        compiled = compile_auth_access(plugin_id, access, settings.host_groups)
        references = _host_group_references(settings, (connections or {}).get(plugin_id))
        auth_groups = settings.host_groups if access.host_groups is None else access.host_groups
        if access.host_groups is not None:
            references = {}
            for name, site in access.sites.items():
                for group in referenced_host_groups(site.origins):
                    references.setdefault(group, set()).add(f"auth_access.sites.{name}.origins")
        plugins[plugin_id]["host_groups"] = {name: list(hosts) for name, hosts in settings.host_groups.items()}
        connection_references = _host_group_references(settings, (connections or {}).get(plugin_id))
        plugins[plugin_id]["connection_variables"] = {
            "host_groups": {
                name: {
                    "values": list(hosts),
                    "source": (origins or {}).get(
                        "plugin_settings." + plugin_id + ".host_groups." + name, "schema default"
                    ),
                    "referenced_by": sorted(
                        field for field in connection_references.get(name, ()) if field.startswith("endpoints.")
                    ),
                    "usage_complete": plugin_id in (connections or {}),
                }
                for name, hosts in settings.host_groups.items()
            }
        }
        sites: dict[str, dict[str, Any]] = {}
        for name, site in access.sites.items():
            sites[name] = {
                "origins": list(site.origins),
                "resolved_origins": list(compiled.sites[name].origins),
                "mode": site.mode if "mode" in site.model_fields_set else access.mode,
                "credential_access": "allow" if site.provides_credentials else "deny",
                "login": site.login_policy,
                "secrets": {key: REDACTED for key in site.secrets},
                "session_inheritance": "binding" if "mode" in site.model_fields_set else "defaults.session.persistence",
            }
        plugins[plugin_id]["auth_access"] = {
            "mode": access.mode,
            "sites": sites,
            "variables": {
                "host_groups": {
                    name: {
                        "field": "plugin_settings."
                        + plugin_id
                        + (".host_groups." if access.host_groups is None else ".auth_access.host_groups.")
                        + name,
                        "source": (origins or {}).get(
                            "plugin_settings."
                            + plugin_id
                            + (".host_groups." if access.host_groups is None else ".auth_access.host_groups.")
                            + name,
                            "schema default",
                        ),
                        "values": list(hosts),
                        "referenced_by": sorted(references.get(name, ())),
                        "usage_complete": plugin_id in (connections or {}),
                    }
                    for name, hosts in auth_groups.items()
                }
            },
        }
    return safe


def _authentication_selection(config: AppConfig, url: str | None) -> dict[str, object]:
    if url is None:
        return {}
    result: dict[str, object] = {}
    for plugin_id, settings in config.plugin_settings.items():
        selected = resolve_auth_access(plugin_id, url, settings.auth_access, host_groups=settings.host_groups)
        declared = settings.auth_access.sites.get(selected.site_id)
        result[plugin_id] = {
            "binding": selected.site_id if declared is not None else None,
            "reason": "matching input origin" if declared is not None else "unregistered input origin; default policy",
            "origins": list(selected.site.origins),
            "credential_access": "allow" if selected.site.provides_credentials else "deny",
            "login": selected.site.login_policy,
            "session": {"persistence": "ephemeral" if selected.site.mode == "anonymous" else "persistent"},
            "session_inheritance": "binding"
            if declared is not None and "mode" in declared.model_fields_set
            else "defaults.session.persistence",
        }
    return result


def _user_endpoint_previews(settings: PluginSettings) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, item in settings.endpoints.items():
        origin = item.get("origin")
        if not isinstance(origin, (str, list, tuple)):
            continue
        value = origin if isinstance(origin, str) else tuple(origin)
        try:
            expanded = expand_origins(value, settings.config, None, host_groups=settings.host_groups)
            result[name] = {
                "origin": value,
                "resolved_origins": expanded,
                "complete": all("$" + "{" not in item for item in expanded),
            }
        except ValueError:
            result[name] = {"origin": value, "resolved_origins": None, "complete": False}
    return result


def _host_group_references(settings: PluginSettings, connections: PluginConnections | None) -> dict[str, set[str]]:
    references: dict[str, set[str]] = {}
    entries: list[tuple[str, str | tuple[str, ...]]] = [
        (f"auth_access.sites.{name}.origins", site.origins) for name, site in settings.auth_access.sites.items()
    ]
    if connections is not None:
        entries.extend((f"endpoints.{name}.origin", item.origin) for name, item in connections.endpoints.items())
    else:
        for name, item in settings.endpoints.items():
            origin = item.get("origin")
            if isinstance(origin, str):
                entries.append((f"endpoints.{name}.origin", origin))
            elif isinstance(origin, (list, tuple)) and all(isinstance(value, str) for value in origin):
                entries.append((f"endpoints.{name}.origin", tuple(origin)))
    for field, origins in entries:
        for group in referenced_host_groups(origins):
            references.setdefault(group, set()).add(field)
    return references


def _doctor_host_connections(
    registry: PluginRuntime, overrides: Mapping[str, Mapping[str, object]]
) -> dict[str, PluginConnections]:
    failed = {item.name for item in registry.diagnostics if not item.loaded and not item.warning}
    return {
        record.id: registry.effective_connections(record, plugin_config=registry.effective_config(record, overrides))
        for record in registry.records.values()
        if record.kind == "site_plugin" and record.id not in failed
    }


def _unused_host_group_warnings(
    config: AppConfig, connections: Mapping[str, PluginConnections]
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for plugin_id, settings in config.plugin_settings.items():
        if plugin_id not in connections:
            continue
        references = _host_group_references(settings, connections[plugin_id])
        if settings.auth_access.host_groups is not None:
            references = {
                name: {field for field in fields if field.startswith("endpoints.")}
                for name, fields in references.items()
            }
        warnings.extend(
            {
                "kind": "unused_host_group",
                "plugin_id": plugin_id,
                "field": "plugin_settings." + plugin_id + ".host_groups." + name,
                "message": "host group is not referenced by the effective authentication or endpoint settings",
            }
            for name in settings.host_groups
            if not references.get(name)
        )
    return warnings


def _doctor_plugin_details(
    registry: PluginRuntime,
    overrides: Mapping[str, Mapping[str, object]],
    connections: Mapping[str, PluginConnections] | None = None,
) -> list[dict[str, object]]:
    """Return provenance and effective settings for each accepted plugin unit."""
    failed_ids = {item.name for item in registry.diagnostics if not item.loaded and not item.warning}
    details: list[dict[str, object]] = []
    for record in sorted(registry.records.values(), key=lambda item: item.id):
        manifest = record.manifest.value
        catalog = record.catalog
        details.append(
            {
                "id": record.id,
                "kind": record.kind,
                "publisher": manifest.get("publisher"),
                "version": manifest.get("version"),
                "api_version": manifest.get("api_version"),
                "source": "builtin" if record.builtin else str(record.manifest.directory),
                "enabled": registry.enabled(record),
                "status": "failed" if record.id in failed_ids else "loaded",
                "match_priority": manifest["match_priority"],
                "capabilities": list(manifest.get("capabilities", ())),
                "entry": dict(manifest["entry"]),
                "config_file": manifest.get("config_file"),
                "key_id": manifest.get("key_id"),
                "manifest_digest": record.manifest.digest,
                "file_tree_sha256": manifest.get("file_tree_sha256"),
                "verification": ("builtin" if record.builtin else "trusted" if catalog is not None else "not-pinned"),
                "catalog": (
                    None
                    if catalog is None
                    else {
                        "selection_priority": catalog.selection_priority,
                        "revoked": catalog.revoked,
                        "key_id": catalog.key_id,
                        "manifest_digest": catalog.manifest_digest,
                        "file_tree_sha256": catalog.file_tree_sha256,
                    }
                ),
                "endpoints": _endpoint_display(registry, record.id, (connections or {}).get(record.id), overrides),
                "author_defaults": _doctor_redact(record.author_defaults),
                "effective_config": _doctor_redact(registry.effective_config(record, overrides)),
            }
        )
    return details


def _endpoint_display(
    registry: PluginRuntime,
    plugin_id: str,
    connections: PluginConnections | None,
    overrides: Mapping[str, Mapping[str, object]],
) -> dict[str, object] | None:
    if connections is None:
        return None
    settings = registry.config.plugin_settings.get(plugin_id)
    expanded = expand_connections(
        connections,
        registry.effective_config(registry.records[plugin_id], overrides),
        host_groups=settings.host_groups if settings else {},
    )
    result: dict[str, object] = {
        name: {
            "origin": item.origin,
            "resolved_origins": expanded.endpoints[name].origin,
            "host_groups": sorted(referenced_host_groups(item.origin)),
            "auth": item.auth,
        }
        for name, item in connections.endpoints.items()
    }
    access = compile_auth_access(plugin_id, settings.auth_access, settings.host_groups) if settings else None
    for name, preview in result.items():
        assert isinstance(preview, dict)
        try:
            origins = expanded.endpoints[name].origin
            matcher = OriginMatcher.compile((origins,) if isinstance(origins, str) else origins)
            preview["permitted_bindings"] = (
                [
                    binding
                    for binding, policy in access.sites.items()
                    if matcher.overlaps(OriginMatcher.compile(policy.origins))
                ]
                if access
                else []
            )
            preview["scope_complete"] = True
        except ValueError:
            preview["permitted_bindings"] = None
            preview["scope_complete"] = False
    return result


def _print_json_block(label: str, value: object, *, indent: str = "") -> None:
    print(f"{indent}{label}:")
    for line in json.dumps(value, ensure_ascii=False, indent=2, default=str).splitlines():
        print(f"{indent}  {line}")


def _print_doctor_report(payload: Mapping[str, object]) -> None:
    """Render the human-facing report from the same payload as ``--json``."""
    application = payload["application"]
    library = payload["library"]
    configuration = payload["configuration"]
    assert isinstance(application, Mapping) and isinstance(library, Mapping) and isinstance(configuration, Mapping)
    print("doctor: healthy" if payload["healthy"] else "doctor: unhealthy")
    print("application:")
    print(f"- version: {application['version']}")
    print(f"- root directory: {application['root_directory']}")
    print("library:")
    print(f"- version: {library['version']}")
    print(f"- root directory: {library['root_directory']}")
    print(f"plugin root: {payload['plugin_root']}")
    print(f"plugin verification: {payload['verification']}")
    _print_json_block("configuration (sensitive values redacted)", configuration)
    selection = configuration.get("selection", [])
    if selection:
        print("site plugin selection:")
        for candidate in selection:
            assert isinstance(candidate, Mapping)
            print(f"- {candidate['id']}: matcher={candidate['matcher']} matched={candidate['matched']}")
    _print_json_block("runtime paths", payload["paths"])
    print("loaded plugins:")
    loaded_plugins = payload["loaded_plugins"]
    assert isinstance(loaded_plugins, list)
    for detail in loaded_plugins:
        assert isinstance(detail, Mapping)
        print(
            f"- {detail['id']}: {detail['status']} kind={detail['kind']} "
            f"publisher={detail['publisher']} version={detail['version']} "
            f"enabled={detail['enabled']} verification={detail['verification']}"
        )
        entry = detail["entry"]
        assert isinstance(entry, Mapping)
        print(f"  source={detail['source']} entry={entry['file']}:{entry['class']} config={detail['config_file']}")
        if detail["endpoints"] is not None:
            _print_json_block("endpoints", detail["endpoints"], indent="  ")
        _print_json_block("author defaults", detail["author_defaults"], indent="  ")
        _print_json_block("effective config", detail["effective_config"], indent="  ")
    print("plugin diagnostics:")
    plugin_diagnostics = payload["plugins"]
    assert isinstance(plugin_diagnostics, list)
    for item in plugin_diagnostics:
        assert isinstance(item, Mapping)
        state = "warning" if item["warning"] else "loaded" if item["loaded"] else "failed"
        print(f"- {item['name']}: {state} {item['detail']}".rstrip())
