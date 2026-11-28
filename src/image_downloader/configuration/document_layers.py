"""Resolve format-v2 file families without mutating user configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from ..diagnostics import configuration_source, diagnostic_for
from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from .documents import (
    PluginDocument,
    auth_runtime,
    document_error,
    merge_auth,
    validate_app_document,
    validate_auth_document,
    validate_document,
)
from .merging import deep_merge
from .models import DEFAULT_CONFIG, _valid_plugin_id
from .provenance import _flatten, _merge_layer

INCOMPLETE_MARKER = ".migration-incomplete"

if TYPE_CHECKING:
    from .resolution import ResolvedApplicationConfig


def _read(
    path: Path,
    root: Path,
    role: str,
    layers: list[Any],
    *,
    required: bool = False,
    documents: Mapping[Path, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    from .resolution import ConfigurationLayer, _read_yaml_document, _resolved_yaml_path

    if documents is not None:
        raw = documents.get(path)
        if required and raw is None:
            raise document_error("configuration", "required configuration document is missing")
        layers.append(ConfigurationLayer(role, path, "missing" if raw is None else "applied"))
        return raw
    resolved = _resolved_yaml_path(path, required=required, config_root=root)
    layers.append(
        ConfigurationLayer(role, path if resolved is None else resolved, "missing" if resolved is None else "applied")
    )
    try:
        return None if resolved is None else _read_yaml_document(resolved, path)[0]
    except ConfigurationError as error:
        _source_error(error, path, role)
        raise


def _source_error(error: ConfigurationError, path: Path, role: str, field: str | None = None) -> None:
    from ..diagnostics import Diagnostic
    from .resolution import _field_mark

    diagnostic = diagnostic_for(error)
    if diagnostic is not None:
        details = dict(diagnostic.details)
        details["source"] = configuration_source(path, role)
        marked_field = field or details.get("field")
        if isinstance(marked_field, str) and path.exists():
            details.update(_field_mark(path.read_bytes(), marked_field))
        error._core_diagnostic = Diagnostic(diagnostic.message, MappingProxyType(details))


def _external_field(field: str, path: Path) -> str:
    if path.parent.name == "plugin-settings":
        return field.removeprefix("plugin_settings." + path.stem + ".")
    if path.name != "auth.yaml":
        return field
    for prefix, replacement in (("network.", "execution."), ("notification.email.", "smtp.")):
        if field.startswith(prefix):
            return replacement + field.removeprefix(prefix)
    if not field.startswith("plugin_settings."):
        return field
    parts = field.removeprefix("plugin_settings.")
    # Plugin IDs contain dots, so use the reserved runtime member as delimiter.
    for member in ("auth_access", "host_groups", "authentication", "endpoints"):
        token = "." + member
        if token not in parts:
            continue
        plugin_id, suffix = parts.split(token, 1)
        if member != "auth_access":
            return "plugins." + plugin_id + ".connections." + member + suffix
        suffix = suffix.removeprefix(".")
        if suffix == "mode":
            suffix = "defaults.session.persistence"
        elif suffix.startswith("sites."):
            suffix = "bindings." + suffix.removeprefix("sites.")
            suffix = re.sub(r"\.secrets(?=\.|$)", ".credentials", suffix)
            if suffix.endswith(".mode"):
                suffix = suffix[:-5] + ".session.persistence"
        return "plugins." + plugin_id + "." + suffix
    return field


def _effective_error(error: ConfigurationError, origins: Mapping[str, str], layers: list[Any]) -> None:
    diagnostic = diagnostic_for(error)
    if diagnostic is None:
        return
    fields = [diagnostic.details.get("field")]
    actual = getattr(error, "_core_configuration_path", ())
    if actual:
        fields.append(".".join(actual))
    for field in fields:
        if not isinstance(field, str):
            continue
        label = origins.get(field)
        if label is None:
            continue
        layer = next((layer for layer in reversed(layers) if str(layer.path) == label), None)
        if layer is not None and layer.path.is_file():
            _source_error(error, layer.path, layer.role, _external_field(field, layer.path))
            return


def site_directories(root: Path, profile: str, host: str | None) -> tuple[Path, ...]:
    from .resolution import _site_layers

    return tuple(path.with_suffix("") for path in _site_layers(root, profile, host))


def _reject_old_sites(root: Path) -> None:
    sites = existing_directory(root / "sites", "site configuration directory")
    if sites.exists() and any(path.suffix == ".yaml" for path in sites.iterdir()):
        raise document_error(
            "sites", "legacy site layout is not supported; use 'config migrate'", kind="configuration_version"
        )


def _read_scope(directory: Path, app: Path, role: str, state: dict[str, Any], *, required: bool = False) -> None:
    root, layers, origins = state["root"], state["layers"], state["origins"]
    documents = state.get("documents")
    raw = _read(app, root, role, layers, required=required, documents=documents)
    if raw is not None:
        try:
            incoming = validate_app_document(raw, main=role == "app", role=role)
        except ConfigurationError as error:
            _source_error(error, app, role)
            raise
        state["data"] = _merge_layer(state["data"], origins, incoming, str(app))
    plugin_dir = existing_directory(directory / "plugin-settings", "plugin settings directory")
    plugin_paths = (
        (path for path in documents if path.parent == plugin_dir)
        if documents is not None
        else (plugin_dir.iterdir() if plugin_dir.exists() else ())
    )
    if plugin_dir.exists() or documents is not None:
        for path in sorted(plugin_paths):
            if path.suffix != ".yaml":
                continue
            if not _valid_plugin_id(path.stem):
                raise document_error("plugin-settings", "plugin setting filename must be a reverse-DNS ID")
            raw_plugin = _read(path, root, role + "_plugin", layers, required=True, documents=documents)
            assert raw_plugin is not None
            try:
                value = validate_document(PluginDocument, raw_plugin)
            except ConfigurationError as error:
                _source_error(error, path, role)
                raise
            state["data"] = _merge_layer(state["data"], origins, {"plugin_settings": {path.stem: value}}, str(path))
    auth_path = directory / "auth.yaml"
    raw_auth = _read(auth_path, root, role + "_auth", layers, documents=documents)
    if raw_auth is None:
        return
    try:
        incoming_auth = validate_auth_document(raw_auth, site=role == "site")
        if role == "app" and any(
            binding is None
            for plugin in incoming_auth.get("plugins", {}).values()
            for binding in plugin.get("bindings", {}).values()
        ):
            raise document_error("plugins.<id>.bindings", "binding deletion is only allowed in a profile")
    except ConfigurationError as error:
        _source_error(error, auth_path, role)
        raise
    tracking_base = deep_merge(state["data"], auth_runtime(state["auth"], partial=True))
    for plugin_id, plugin in incoming_auth.get("plugins", {}).items():
        for name in plugin.get("bindings", {}):
            prefix = f"plugin_settings.{plugin_id}.auth_access.sites.{name}"
            for key in tuple(origins):
                if key == prefix or key.startswith(prefix + "."):
                    del origins[key]
            # The old binding is not a merge base, including for empty credentials.
            tracking_base.get("plugin_settings", {}).get(plugin_id, {}).get("auth_access", {}).get("sites", {}).pop(
                name, None
            )
    _merge_layer(tracking_base, origins, auth_runtime(incoming_auth, partial=True), str(auth_path))
    state["auth"] = merge_auth(state["auth"], incoming_auth)


def resolve_documents(
    path: Path | None,
    profile: str | None,
    site: str | None,
    *,
    require_config: bool,
    runtime_override: Mapping[str, Any] | None,
    source: Literal["defaults", "user", "explicit"] | None,
    documents: Mapping[Path, dict[str, Any]] | None = None,
) -> ResolvedApplicationConfig:
    from .resolution import (
        ConfigurationLayer,
        ResolvedApplicationConfig,
        _materialize_automatic_roots,
        _reject_profile_override,
        _validate_effective_auth_access,
        _validate_layer_values,
        validate_config,
    )

    if runtime_override is not None:
        _reject_profile_override(runtime_override, "runtime_override")
        _validate_layer_values(runtime_override, Path("<runtime override>"), "runtime")
    bundled = Path(__file__).parents[1] / "app.yaml"
    baseline_layers: list[Any] = []
    bundled_raw = _read(bundled, bundled.parent, "baseline", baseline_layers, required=True)
    assert bundled_raw is not None
    baseline = validate_app_document(bundled_raw, main=True, role="app")
    data = DEFAULT_CONFIG.model_dump(by_alias=True, warnings=False)
    origins = {".".join(item): "schema default" for item in _flatten(data) if item}
    data = _merge_layer(data, origins, baseline, str(bundled))
    layers = [ConfigurationLayer("schema_default", None, "applied"), *baseline_layers]
    main_path = None if path is None else canonical_path(path, "configuration path")
    root = bundled.parent if main_path is None else main_path.parent
    existing_directory(root, "configuration root", required=require_config and documents is None)
    if (
        documents is None
        and existing_regular_file(root / INCOMPLETE_MARKER, "migration marker", required=False) is not None
    ):
        raise document_error("configuration", "configuration migration is incomplete", kind="configuration_incomplete")
    state: dict[str, Any] = {
        "root": root,
        "layers": layers,
        "data": data,
        "auth": {},
        "origins": origins,
        "documents": documents,
    }
    if main_path is not None and main_path != bundled.resolve():
        _reject_old_sites(root)
        _read_scope(root, main_path, "app", state, required=require_config)
    else:
        layers.append(ConfigurationLayer("app", main_path, "not_applicable"))
    configured_default = state["data"]["profile"]["default"]
    selected = configured_default if profile is None else profile
    if not isinstance(selected, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", selected):
        raise document_error("profile.default", "invalid profile name")
    profile_dir = root / "profiles" / selected
    _reject_old_sites(profile_dir)
    _read_scope(
        profile_dir,
        profile_dir / "app.yaml",
        "profile_app",
        state,
        required=profile is not None and selected != configured_default,
    )
    for directory in site_directories(root, selected, site):
        _read_scope(directory, directory / "app.yaml", "site", state)
    data = deep_merge(state["data"], auth_runtime(state["auth"]))
    # Explicit empty authentication namespaces prevent general host variables
    # from silently granting permissions for file-based configurations.
    for setting in data.get("plugin_settings", {}).values():
        setting.setdefault("auth_access", {}).setdefault("host_groups", {})
    data["profile"] = {"default": selected}
    if profile is not None:
        origins["profile.default"] = "CLI --profile"
    if runtime_override:
        data = _merge_layer(data, origins, runtime_override, "runtime override")
    data = _materialize_automatic_roots(data, origins)
    try:
        config = _validate_effective_auth_access(validate_config(data))
    except ConfigurationError as error:
        _effective_error(error, origins, layers)
        raise
    return ResolvedApplicationConfig(
        config=config,
        main_config_path=main_path,
        config_root=root,
        source=source or ("defaults" if path is None else "explicit"),
        selected_profile=selected,
        layers=tuple(layers),
        origins=MappingProxyType(dict(sorted(origins.items()))),
    )
