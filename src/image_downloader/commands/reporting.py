"""Doctor report rendering and safe display values."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

from ..plugins.runtime import PluginRuntime
from ..privacy.sensitive_values import redact_sensitive_values


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


def _doctor_redact(value: object) -> object:
    """Make an inspectable doctor report without printing credentials."""
    return redact_sensitive_values(value)


def _doctor_plugin_details(
    registry: PluginRuntime, overrides: Mapping[str, Mapping[str, object]]
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
                "publisher": manifest["publisher"],
                "version": manifest["version"],
                "api_version": manifest["api_version"],
                "source": "builtin" if record.builtin else str(record.manifest.directory),
                "enabled": registry.enabled(record),
                "status": "failed" if record.id in failed_ids else "loaded",
                "match_priority": manifest["match_priority"],
                "capabilities": list(manifest["capabilities"]),
                "entry": dict(manifest["entry"]),
                "config_file": manifest["config_file"],
                "key_id": manifest["key_id"],
                "manifest_digest": record.manifest.digest,
                "file_tree_sha256": manifest["file_tree_sha256"],
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
                "author_defaults": _doctor_redact(record.author_defaults),
                "effective_config": _doctor_redact(registry.effective_config(record, overrides)),
            }
        )
    return details


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
        _print_json_block("author defaults", detail["author_defaults"], indent="  ")
        _print_json_block("effective config", detail["effective_config"], indent="  ")
    print("plugin diagnostics:")
    plugin_diagnostics = payload["plugins"]
    assert isinstance(plugin_diagnostics, list)
    for item in plugin_diagnostics:
        assert isinstance(item, Mapping)
        state = "warning" if item["warning"] else "loaded" if item["loaded"] else "failed"
        print(f"- {item['name']}: {state} {item['detail']}".rstrip())
