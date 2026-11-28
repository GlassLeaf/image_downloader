"""Atomic binding replacements and operation-local policy patches."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from ..configuration.documents import AccessPatchDocument, BindingDocument
from ..configuration.merging import deep_merge
from ..configuration.models import PluginAuthAccess, SiteAuthAccess
from ..diagnostics import configuration_error
from ..exceptions import ConfigurationError
from .access import ResolvedAuthAccess, resolve_auth_access


def validate_auth_changes(
    legacy: Mapping[str, Any] | None, replacements: Mapping[str, Any] | None, patches: Mapping[str, Any] | None
) -> None:
    for value in (legacy, replacements, patches):
        if value is not None and not isinstance(value, Mapping):
            raise ConfigurationError("authentication overrides must be mappings")
    if set(legacy or {}).intersection(set(replacements or {}) | set(patches or {})):
        raise ConfigurationError("legacy authentication overrides cannot be combined with replacements or patches")
    try:
        for bindings in (replacements or {}).values():
            if not isinstance(bindings, Mapping):
                raise ConfigurationError("binding replacements must be mappings")
            for name, value in bindings.items():
                if not isinstance(name, str) or not name.strip() or name != name.strip() or not name.isprintable():
                    raise ConfigurationError("binding names must be non-empty printable names")
                if value is not None:
                    BindingDocument.model_validate(value)
        for value in (patches or {}).values():
            AccessPatchDocument.model_validate(value)
    except ValidationError as exc:
        raise configuration_error(
            "invalid authentication change", "configuration_value", field="authentication_override"
        ) from exc


def operation_access(
    plugin_id: str,
    url: str,
    base: Mapping[str, Any],
    *,
    legacy: Mapping[str, Any] | None,
    replacements: Mapping[str, Any] | None,
    patches: Mapping[str, Any] | None,
    host_groups: Mapping[str, tuple[str, ...]],
) -> ResolvedAuthAccess:
    validate_auth_changes(legacy, replacements, patches)
    for changes in (legacy, replacements, patches):
        if changes and set(changes) != {plugin_id}:
            raise ConfigurationError("authentication change must target the selected site plugin")
    data = deep_merge(base, (legacy or {}).get(plugin_id, {}))
    sites = dict(data.get("sites", {}))
    for name, value in (replacements or {}).get(plugin_id, {}).items():
        if value is None:
            sites.pop(name, None)
        else:
            sites[name] = BindingDocument.model_validate(value).runtime()
    if replacements:
        data["sites"] = sites
    access = resolve_auth_access(plugin_id, url, PluginAuthAccess.model_validate(data), host_groups=host_groups)
    if not patches or plugin_id not in patches:
        return access
    patch = AccessPatchDocument.model_validate(patches[plugin_id])
    # Freeze inherited effective values before applying a limited change. In
    # particular, changing credential access must not implicitly change login.
    values = access.site.model_dump(warnings=False)
    values.update(
        credential_access="allow" if access.site.provides_credentials else "deny", login=access.site.login_policy
    )
    if "credential_access" in patch.model_fields_set:
        values["credential_access"] = patch.credential_access
    if "login" in patch.model_fields_set:
        values["login"] = patch.login
    if patch.session is not None and "persistence" in patch.session.model_fields_set:
        values["mode"] = "anonymous" if patch.session.persistence == "ephemeral" else "standard"
    return ResolvedAuthAccess(plugin_id, access.site_id, SiteAuthAccess.model_validate(values))
