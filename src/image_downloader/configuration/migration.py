"""Explicit, read-only-planned migration from frozen v3 configuration to v2 files."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..credentials.access import compile_auth_access
from ..exceptions import ConfigurationError
from ..immutable import thaw_json
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from .document_layers import INCOMPLETE_MARKER, resolve_documents
from .documents import merge_auth
from .inventory import inventory
from .merging import deep_merge
from .models import AppConfig


@dataclass(frozen=True)
class MigrationPlan:
    source: Path
    destination: Path
    documents: Mapping[Path, dict[str, Any]]
    sources: Mapping[Path, bytes]
    profiles: tuple[str, ...]
    cases: int
    removed: tuple[dict[str, str], ...]

    def report(self, *, applied: bool = False) -> dict[str, object]:
        return {
            "source": str(self.source),
            "destination": str(self.destination),
            "config_version": 2,
            "files": [str(path.relative_to(self.destination)) for path in sorted(self.documents)],
            "profiles": list(self.profiles),
            "verified_cases": self.cases,
            "removed": list(self.removed),
            "writes_applied": applied,
            "verification_scope": [
                "application values",
                "plugin inputs",
                "connection policy templates",
                "expanded authentication permissions",
            ],
            "requires_runtime_checks": ["plugin-specific validation", "operation URL expansion", "live authentication"],
        }


def _permissions(config: AppConfig) -> dict[str, Any]:
    plugins: dict[str, Any] = {}
    for plugin_id, settings in config.plugin_settings.items():
        access = settings.auth_access
        plugin: dict[str, Any] = {"bindings": {}}
        if "mode" in access.model_fields_set:
            plugin["defaults"] = {
                "session": {"persistence": "ephemeral" if access.mode == "anonymous" else "persistent"}
            }
        used_groups: set[str] = set()
        from .endpoint_origins import referenced_host_groups

        for name, binding in access.sites.items():
            value: dict[str, Any] = {
                "origins": list(binding.origins),
                "credentials": dict(binding.secrets),
                "credential_access": "allow" if binding.provides_credentials else "deny",
                "login": binding.login_policy,
            }
            if "mode" in binding.model_fields_set:
                value["session"] = {"persistence": "ephemeral" if binding.mode == "anonymous" else "persistent"}
            plugin["bindings"][name] = value
            used_groups.update(referenced_host_groups(binding.origins))
        groups = access.host_groups if access.host_groups is not None else settings.host_groups
        if used_groups:
            plugin["host_groups"] = {name: list(groups[name]) for name in sorted(used_groups)}
        if access.model_fields_set:
            plugins[plugin_id] = plugin
    return {"plugins": plugins, "smtp": {"credential_reference": config.notification.email.credential_reference}}


def _permissions_delta(base: Mapping[str, Any], desired: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if base.get("smtp") != desired.get("smtp"):
        result["smtp"] = deepcopy(desired["smtp"])
    for plugin_id, value in desired.get("plugins", {}).items():
        previous = base.get("plugins", {}).get(plugin_id, {})
        delta = {key: deepcopy(item) for key, item in value.items() if key != "bindings" and previous.get(key) != item}
        bindings = {
            name: deepcopy(binding)
            for name, binding in value.get("bindings", {}).items()
            if previous.get("bindings", {}).get(name) != binding
        }
        bindings.update({name: None for name in previous.get("bindings", {}) if name not in value.get("bindings", {})})
        if bindings:
            delta["bindings"] = bindings
        if delta:
            result.setdefault("plugins", {})[plugin_id] = delta
    return result


def _explicit_profile_permissions(
    permissions: Mapping[str, Any],
    raw_documents: Mapping[Path, dict[str, Any]],
    source: Path,
    profile: str,
) -> dict[str, Any]:
    """Retain explicit profile choices even when their effective value equals common policy."""
    result: dict[str, Any] = {}
    directory = source.parent / "profiles" / profile
    for path in (directory / "app.yaml", directory / "sites/global.yaml"):
        raw = raw_documents.get(path, {})
        if "credential_reference" in raw.get("notification", {}).get("email", {}):
            result["smtp"] = deepcopy(permissions["smtp"])
        for plugin_id, settings in raw.get("plugin_settings", {}).items():
            access = settings.get("auth_access", {})
            if not access:
                continue
            desired = permissions["plugins"][plugin_id]
            plugin = result.setdefault("plugins", {}).setdefault(plugin_id, {})
            if "mode" in access:
                plugin["defaults"] = deepcopy(desired["defaults"])
            for name in access.get("sites", {}):
                plugin.setdefault("bindings", {})[name] = deepcopy(desired["bindings"][name])
    return result


def _signature(config: AppConfig) -> dict[str, Any]:
    data = thaw_json(config.model_dump(by_alias=True, warnings=False))
    assert isinstance(data, dict)
    data["notification"]["email"].pop("username", None)
    data["notification"]["email"].pop("credential_service", None)
    for plugin_id, settings in config.plugin_settings.items():
        plugin = data["plugin_settings"][plugin_id]
        policy = compile_auth_access(plugin_id, settings.auth_access, settings.host_groups)
        plugin["auth_access"] = {
            "mode": policy.mode,
            "sites": {
                name: {
                    "origins": sorted(binding.origins),
                    "secrets": dict(binding.secrets),
                    "credential_access": binding.provides_credentials,
                    "login": binding.login_policy,
                    "mode": binding.mode if "mode" in binding.model_fields_set else policy.mode,
                }
                for name, binding in policy.sites.items()
            },
        }
        # A partial author-policy override must remain partial after conversion.
        plugin["authentication"] = settings.authentication.model_dump(exclude_unset=True)
    return data


def _permission_signature(config: AppConfig) -> dict[str, Any]:
    """Ignore unused variables and implicit empty policies when checking host dependence."""
    signature = _signature(config)
    return {
        "smtp": config.notification.email.credential_reference,
        "plugins": {
            plugin_id: value["auth_access"]
            for plugin_id, value in signature["plugin_settings"].items()
            if value["auth_access"]["mode"] != "standard" or value["auth_access"]["sites"]
        },
    }


def _check_legacy_smtp(config: AppConfig) -> None:
    notification = config.notification
    uses_email = notification.enabled and any(
        "email" in notification.routes.get(category, notification.methods) for category in notification.notify_on
    )
    if uses_email and notification.email.username and not notification.email.credential_reference:
        raise ConfigurationError(
            "active legacy SMTP username requires an explicit credential_reference before migration"
        )


def _legacy_site_path(relative: Path) -> bool:
    parts = relative.parts
    return parts[0] == "sites" or (len(parts) == 4 and parts[0] == "profiles" and parts[2] == "sites")


def _new_scope(root: Path, original: Path, source: Path) -> Path:
    if original == source:
        return root
    relative = original.relative_to(source.parent)
    return root / relative.with_suffix("") if _legacy_site_path(relative) else root / relative.parent


def _split(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    app = deepcopy(dict(raw))
    plugins = app.pop("plugin_settings", {})
    auth: dict[str, Any] = {}
    network = app.get("network", {})
    if "auth_refresh_attempts" in network:
        auth["execution"] = {"auth_refresh_attempts": network.pop("auth_refresh_attempts")}
    email = app.get("notification", {}).get("email", {})
    email.pop("username", None)
    email.pop("credential_service", None)
    if "credential_reference" in email:
        auth["smtp"] = {"credential_reference": email.pop("credential_reference")}
    private: dict[str, Any] = {}
    for plugin_id, setting in plugins.items():
        private[plugin_id] = {
            key: value for key, value in setting.items() if key in {"enabled", "config", "download_policy"}
        }
        connections = {
            key: value for key, value in setting.items() if key in {"host_groups", "authentication", "endpoints"}
        }
        if connections:
            auth.setdefault("plugins", {})[plugin_id] = {"connections": connections}
    return app, private, auth


def plan_migration(source: Path, destination: Path) -> MigrationPlan:
    from . import legacy_layers
    from .legacy_normalization import _sanitize_user_layer

    checked_source = existing_regular_file(source, "migration source", required=True)
    assert checked_source is not None
    source = checked_source
    destination = canonical_path(destination, "migration destination")
    if destination.is_relative_to(source.parent) or source.parent.is_relative_to(destination):
        raise ConfigurationError("migration source and destination must be disjoint configuration directories")
    if destination.exists():
        raise ConfigurationError("migration destination must not exist")
    files, profiles, hosts = inventory(source, legacy=True)
    snapshots = {path: path.read_bytes() for path in files}
    raw_main = legacy_layers.load_yaml(source, required=True)
    if "config_version" in raw_main:
        raise ConfigurationError("migration requires an unversioned current-v3 configuration")
    raw_documents: dict[Path, dict[str, Any]] = {}
    documents: dict[Path, dict[str, Any]] = {}
    removed: list[dict[str, str]] = []
    for path in files:
        raw = legacy_layers.load_yaml(path, required=True, config_root=source.parent)
        clean, removals = _sanitize_user_layer(raw)
        role = (
            "app" if path == source else "site" if _legacy_site_path(path.relative_to(source.parent)) else "profile_app"
        )
        legacy_layers._validate_layer_values(clean, path, role)
        raw_documents[path] = clean
        removed.extend({"source": str(path), "field": ".".join(map(str, key))} for key in removals)
        email = clean.get("notification", {}).get("email", {})
        removed.extend(
            {"source": str(path), "field": "notification.email." + key}
            for key in ("username", "credential_service")
            if key in email
        )
        app, private, auth = _split(clean)
        # Permissions and SMTP selection are rebuilt from their effective
        # unconditional layers; connection/execution policy retains its scope.
        auth.pop("smtp", None)
        scope = _new_scope(destination, path, source)
        if path == source:
            app = {"config_version": 2, **app}
        documents[scope / "app.yaml"] = app
        for plugin_id, value in private.items():
            documents[scope / "plugin-settings" / (plugin_id + ".yaml")] = value
        if auth:
            documents[scope / "auth.yaml"] = auth
    baseline = legacy_layers.load_yaml(Path(legacy_layers.__file__).with_name("legacy-defaults.yaml"), required=True)
    common = deep_merge(baseline, raw_documents[source])
    global_path = source.parent / "sites" / "global.yaml"
    common = deep_merge(common, raw_documents.get(global_path, {}))
    common_permissions = _permissions(legacy_layers.validate_config(common))
    common_auth = destination / "auth.yaml"
    documents[common_auth] = merge_auth(documents.get(common_auth, {}), common_permissions)
    old_default = legacy_layers.resolve_application_config(source, require_config=True)
    selected_profiles = tuple(sorted(set(profiles) | {old_default.selected_profile}))
    cases = 0
    for profile in selected_profiles:
        old = legacy_layers.resolve_application_config(source, profile=profile, require_config=True)
        permissions = _permissions(old.config)
        profile_auth = destination / "profiles" / profile / "auth.yaml"
        delta = _permissions_delta(common_permissions, permissions)
        delta = merge_auth(delta, _explicit_profile_permissions(permissions, raw_documents, source, profile))
        if delta:
            documents[profile_auth] = merge_auth(documents.get(profile_auth, {}), delta)
        # Preserve a usable profile selector even for a legacy profile with
        # only unconditional/site data and no material app overrides.
        if profile != old_default.selected_profile:
            documents.setdefault(destination / "profiles" / profile / "app.yaml", {})
        for host in (None, *hosts):
            previous = legacy_layers.resolve_application_config(source, profile=profile, site=host, require_config=True)
            _check_legacy_smtp(previous.config)
            if _permission_signature(previous.config) != _permission_signature(old.config):
                raise ConfigurationError(
                    f"host-dependent authentication permissions cannot be migrated automatically (profile {profile})"
                )
            migrated = resolve_documents(
                destination / "app.yaml",
                profile,
                host,
                require_config=True,
                runtime_override=None,
                source="explicit",
                documents=documents,
            )
            if _signature(previous.config) != _signature(migrated.config):
                raise ConfigurationError(f"static configuration equivalence failed (profile {profile})")
            cases += 1
    plan = MigrationPlan(source, destination, documents, snapshots, selected_profiles, cases, tuple(removed))
    _verify_sources(plan)
    return plan


def _verify_sources(plan: MigrationPlan) -> None:
    files, _, _ = inventory(plan.source, legacy=True)
    if set(files) != set(plan.sources) or any(path.read_bytes() != data for path, data in plan.sources.items()):
        raise ConfigurationError("migration source changed during conversion")


def apply_migration(plan: MigrationPlan) -> None:
    """Reserve a new directory; publish only after validation and source checks."""
    from .layers import load_yaml

    _verify_sources(plan)
    destination = canonical_path(plan.destination, "migration destination")
    existing_directory(destination.parent, "migration destination parent")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ConfigurationError("migration destination must not exist") from exc
    marker = destination / INCOMPLETE_MARKER
    with marker.open("x", encoding="utf-8") as stream:
        stream.write("Migration incomplete. Use a new destination to retry.\n")
    for path, value in sorted(plan.documents.items()):
        canonical_path(path, "migration output")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            yaml.safe_dump(thaw_json(value), stream, allow_unicode=True, sort_keys=False)
    # Validate from the same immutable documents while the marker deliberately
    # prevents ordinary readers from observing partial publication.
    written = {path: load_yaml(path, required=True, config_root=destination) for path in plan.documents}
    if written != {path: thaw_json(value) for path, value in plan.documents.items()}:
        raise ConfigurationError("migration output changed during conversion")
    _, _, hosts = inventory(destination / "app.yaml")
    for profile in plan.profiles:
        for host in (None, *hosts):
            resolve_documents(
                destination / "app.yaml",
                profile,
                host,
                require_config=True,
                runtime_override=None,
                source="explicit",
                documents=written,
            )
    _verify_sources(plan)
    marker.unlink()
