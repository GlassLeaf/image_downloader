"""File-format v2 models, independent of the stable runtime/Python schema."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from functools import cache
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from ..diagnostics import configuration_error, validation_diagnostic
from ..exceptions import ConfigurationError
from ..immutable import thaw_json
from .host_groups import normalize_host_groups
from .merging import deep_merge
from .models import (
    AppConfig,
    AuthenticationPolicy,
    PluginDownloadPolicy,
    SiteAuthAccess,
    StrictModel,
    _valid_plugin_id,
)
from .plugin_layers import partial_plugin_layer


class SessionDocument(StrictModel):
    persistence: Literal["persistent", "ephemeral"] = "persistent"


class AuthDefaultsDocument(StrictModel):
    session: SessionDocument = Field(default_factory=SessionDocument)


class BindingDocument(StrictModel):
    origins: tuple[StrictStr, ...]
    credentials: Mapping[StrictStr, StrictStr] = Field(default_factory=dict)
    credential_access: Literal["allow", "deny"]
    login: Literal["allow", "ask", "deny"]
    session: SessionDocument | None = None

    @field_validator("session", mode="before")
    @classmethod
    def no_null_session(cls, value: object) -> object:
        if value is None:
            raise ValueError("omit session to inherit the default persistence")
        return value

    @field_validator("origins", mode="before")
    @classmethod
    def valid_origins(cls, value: object) -> tuple[str, ...]:
        return SiteAuthAccess.exact_origins(value)

    @field_validator("credentials")
    @classmethod
    def valid_credentials(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return SiteAuthAccess.valid_references(value)

    def runtime(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "origins": self.origins,
            "secrets": dict(self.credentials),
            "credential_access": self.credential_access,
            "login": self.login,
        }
        if self.session is not None and "persistence" in self.session.model_fields_set:
            result["mode"] = "anonymous" if self.session.persistence == "ephemeral" else "standard"
        return result


class AccessPatchDocument(StrictModel):
    credential_access: Literal["allow", "deny"] | None = None
    login: Literal["allow", "ask", "deny"] | None = None
    session: SessionDocument | None = None

    @field_validator("credential_access", "login", "session", mode="before")
    @classmethod
    def no_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("explicit null is not an authentication policy")
        return value


class PluginDocument(StrictModel):
    enabled: StrictBool = True
    config: Mapping[str, Any] = Field(default_factory=dict)
    download_policy: PluginDownloadPolicy = Field(default_factory=PluginDownloadPolicy)


class ConnectionsDocument(StrictModel):
    host_groups: Mapping[StrictStr, tuple[StrictStr, ...]] = Field(default_factory=dict)
    authentication: AuthenticationPolicy = Field(default_factory=AuthenticationPolicy)
    endpoints: Mapping[StrictStr, Mapping[str, Any]] = Field(default_factory=dict)

    @field_validator("host_groups", mode="before")
    @classmethod
    def valid_hosts(cls, value: object) -> Mapping[str, tuple[str, ...]]:
        return normalize_host_groups(value)


class PluginAuthDocument(StrictModel):
    defaults: AuthDefaultsDocument = Field(default_factory=AuthDefaultsDocument)
    host_groups: Mapping[StrictStr, tuple[StrictStr, ...]] = Field(default_factory=dict)
    bindings: Mapping[StrictStr, BindingDocument | None] = Field(default_factory=dict)
    connections: ConnectionsDocument = Field(default_factory=ConnectionsDocument)

    @field_validator("host_groups", mode="before")
    @classmethod
    def valid_hosts(cls, value: object) -> Mapping[str, tuple[str, ...]]:
        return normalize_host_groups(value)

    @field_validator("bindings")
    @classmethod
    def valid_names(cls, value: Mapping[str, BindingDocument | None]) -> Mapping[str, BindingDocument | None]:
        if any(not name or name != name.strip() or not name.isprintable() for name in value):
            raise ValueError("binding names must be non-empty printable names without surrounding whitespace")
        return value


class SmtpAuthDocument(StrictModel):
    credential_reference: StrictStr | None = None

    @field_validator("credential_reference")
    @classmethod
    def valid_reference(cls, value: str | None) -> str | None:
        from .models import Email

        return Email.smtp_reference(value)


class AuthExecutionDocument(StrictModel):
    auth_refresh_attempts: StrictInt = Field(1, ge=0)


class AuthDocument(StrictModel):
    plugins: Mapping[StrictStr, PluginAuthDocument] = Field(default_factory=dict)
    smtp: SmtpAuthDocument = Field(default_factory=SmtpAuthDocument)
    execution: AuthExecutionDocument = Field(default_factory=AuthExecutionDocument)

    @field_validator("plugins")
    @classmethod
    def valid_ids(cls, value: Mapping[str, PluginAuthDocument]) -> Mapping[str, PluginAuthDocument]:
        if any(not _valid_plugin_id(key) for key in value):
            raise ValueError("plugin IDs must be reverse-DNS identifiers")
        return value


def document_error(field: str, message: str, *, kind: str = "configuration_value") -> ConfigurationError:
    return configuration_error(message, kind, field=field)


def validate_document(model: type[StrictModel], raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = thaw_json(model.model_validate(raw).model_dump(exclude_unset=True, warnings=False))
        assert isinstance(result, dict)
        return result
    except ValidationError as exc:
        raise validation_diagnostic(exc.errors(include_input=False)[0], model=model) from exc


def validate_app_document(raw: Mapping[str, Any], *, main: bool, role: str) -> dict[str, Any]:
    data = dict(raw)
    if main:
        version = data.pop("config_version", None)
        if type(version) is not int or version != 2:
            raise document_error(
                "config_version",
                "configuration format 2 is required; use 'config migrate'",
                kind="configuration_version",
            )
    forbidden = {"plugin_settings", "config_version"}
    if role != "app":
        forbidden.update({"profile", "storage", "plugins"})
    if role == "site":
        forbidden.add("security")
    for key in forbidden.intersection(data):
        raise document_error(key, "field is not allowed in this application document", kind="configuration_layer")
    network = data.get("network")
    if isinstance(network, Mapping) and "auth_refresh_attempts" in network:
        raise document_error("network.auth_refresh_attempts", "use auth.yaml execution.auth_refresh_attempts")
    notification = data.get("notification")
    email = notification.get("email") if isinstance(notification, Mapping) else None
    if isinstance(email, Mapping) and {"credential_reference", "username", "credential_service"}.intersection(email):
        raise document_error("notification.email", "SMTP credentials belong in auth.yaml")
    validate_partial_runtime(data)
    return data


def validate_partial_runtime(raw: Mapping[str, Any]) -> None:
    """Validate supplied fields independently; cross-field checks run after merging."""
    from pydantic import BaseModel

    def visit(model: type[BaseModel], value: Mapping[str, Any], prefix: tuple[str, ...]) -> None:
        fields = {field.alias or name: (name, field) for name, field in model.model_fields.items()}
        for key, item in value.items():
            path = (*prefix, key if isinstance(key, str) else "<unknown>")
            entry = fields.get(key)
            if entry is None:
                raise document_error(
                    ".".join(path), "configuration contains an unsupported field", kind="configuration_unknown_field"
                )
            name, info = entry
            annotation = info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(item, Mapping):
                visit(annotation, item, path)
                continue
            try:
                _field_validator(model, name).validate_python(item)
                if model is AppConfig.model_fields["output"].annotation:
                    model.model_validate({key: item})
            except ValidationError as exc:
                issue = exc.errors(include_input=False)[0]
                issue["loc"] = (*path, *issue["loc"])
                raise validation_diagnostic(issue, model=AppConfig) from exc

    visit(AppConfig, raw, ())


@cache
def _field_validator(model: type[StrictModel], name: str) -> Any:
    """Use the runtime field's constraints and custom validators, without model checks."""
    from pydantic_core import SchemaValidator

    schema = model.__pydantic_core_schema__
    definitions = []
    while schema["type"] != "model-fields":
        if schema["type"] == "definitions":
            definitions.extend(schema["definitions"])
        schema = schema["schema"]
    field_schema = schema["fields"][name]["schema"]
    if definitions:
        field_schema = {"type": "definitions", "schema": field_schema, "definitions": definitions}
    return SchemaValidator(field_schema)


def validate_auth_document(raw: Mapping[str, Any], *, site: bool) -> dict[str, Any]:
    data = validate_document(AuthDocument, raw)
    if site and "smtp" in data:
        raise document_error("smtp", "SMTP credential selection is not allowed in a site layer")
    for plugin_id, plugin in data.get("plugins", {}).items():
        if site and set(plugin) - {"connections"}:
            raise document_error("plugins." + plugin_id, "site layers allow only connection policy overrides")
        connections = plugin.get("connections", {})
        partial_plugin_layer(
            {
                "plugin_settings": {
                    plugin_id: {
                        "authentication": connections.get("authentication", {}),
                        "endpoints": connections.get("endpoints", {}),
                    }
                }
            }
        )
    return data


def merge_auth(base: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-merge policy, but replace/delete identity bindings atomically."""
    result = deep_merge(base, incoming)
    for plugin_id, plugin in incoming.get("plugins", {}).items():
        if "bindings" not in plugin:
            continue
        bindings = deepcopy(dict(base.get("plugins", {}).get(plugin_id, {}).get("bindings", {})))
        for name, binding in plugin["bindings"].items():
            if binding is None:
                bindings.pop(name, None)
            else:
                bindings[name] = deepcopy(binding)
        result["plugins"][plugin_id]["bindings"] = bindings
    return result


def auth_runtime(raw: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"plugin_settings": {}}
    if "smtp" in raw:
        result["notification"] = {"email": dict(raw["smtp"])}
    if "execution" in raw:
        result["network"] = dict(raw["execution"])
    for plugin_id, plugin in raw.get("plugins", {}).items():
        settings = deepcopy(plugin.get("connections", {}))
        session = plugin.get("defaults", {}).get("session", {})
        persistence = session.get("persistence", "persistent")
        access: dict[str, Any] = {}
        if not partial or "persistence" in session:
            access["mode"] = "anonymous" if persistence == "ephemeral" else "standard"
        if not partial or "host_groups" in plugin:
            access["host_groups"] = deepcopy(plugin.get("host_groups", {}))
        if not partial or "bindings" in plugin:
            access["sites"] = {
                name: None if value is None else BindingDocument.model_validate(value).runtime()
                for name, value in plugin.get("bindings", {}).items()
            }
        if access:
            settings["auth_access"] = access
        result["plugin_settings"][plugin_id] = settings
    return result
