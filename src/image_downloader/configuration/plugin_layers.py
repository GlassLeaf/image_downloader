"""Strict core plugin keys and syntax checks for partial configuration layers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from ..diagnostics import configuration_error, host_group_diagnostic, validation_diagnostic
from .host_groups import HostGroupError
from .models import EndpointSettings, PluginAuthAccess, PluginSettings, SiteAuthAccess, _valid_plugin_id


def _known_keys(value: Mapping[object, object], fields: set[str], field: str, plugin_id: str | None) -> None:
    if any(key not in fields for key in value):
        details: dict[str, object] = {"field": field}
        if plugin_id is not None:
            details["plugin_id"] = plugin_id
        raise configuration_error(
            "plugin configuration contains an unsupported field", "configuration_unknown_field", **details
        )


def validate_plugin_keys(raw: Mapping[str, Any]) -> None:
    settings = raw.get("plugin_settings")
    if not isinstance(settings, Mapping):
        return
    for key, setting in settings.items():
        if not isinstance(setting, Mapping):
            continue
        plugin_id = key if isinstance(key, str) and _valid_plugin_id(key) else None
        prefix = "plugin_settings." + (plugin_id or "<plugin>")
        if "allowed_hosts" in setting:
            raise host_group_diagnostic(HostGroupError("legacy"), plugin_id=plugin_id)
        _known_keys(setting, set(PluginSettings.model_fields), prefix, plugin_id)
        access = setting.get("auth_access")
        if not isinstance(access, Mapping):
            continue
        _known_keys(access, set(PluginAuthAccess.model_fields), prefix + ".auth_access", plugin_id)
        sites = access.get("sites")
        if isinstance(sites, Mapping):
            for site in sites.values():
                if isinstance(site, Mapping):
                    _known_keys(site, set(SiteAuthAccess.model_fields), prefix + ".auth_access.sites.<site>", plugin_id)


def partial_plugin_layer(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Supply validation-only required fields; never merge these placeholders."""
    validate_plugin_keys(raw)
    result = dict(raw)
    settings = raw.get("plugin_settings")
    if not isinstance(settings, Mapping):
        return result
    prepared = dict(settings)
    for plugin_id, value in settings.items():
        if not isinstance(value, Mapping):
            continue
        setting = dict(value)
        access = setting.get("auth_access")
        if isinstance(access, Mapping) and isinstance(access.get("sites"), Mapping):
            access = dict(access)
            access["sites"] = {
                name: {"origins": "https://layer-validation.invalid", **site} if isinstance(site, Mapping) else site
                for name, site in access["sites"].items()
            }
            setting["auth_access"] = access
        _validate_endpoint_layers(plugin_id, setting.get("endpoints"))
        prepared[plugin_id] = setting
    result["plugin_settings"] = prepared
    return result


def _validate_endpoint_layers(plugin_id: object, endpoints: object) -> None:
    if not isinstance(endpoints, Mapping):
        return
    for endpoint in endpoints.values():
        if not isinstance(endpoint, Mapping):
            continue
        try:
            EndpointSettings.model_validate({"origin": "https://layer-validation.invalid", "auth": "none", **endpoint})
        except ValidationError as exc:
            issue = exc.errors(include_input=False, include_url=False)[0]
            issue["loc"] = (
                "plugin_settings",
                plugin_id if isinstance(plugin_id, str) else "<plugin>",
                "endpoints",
                "<endpoint>",
                *issue["loc"],
            )
            raise validation_diagnostic(issue) from exc
