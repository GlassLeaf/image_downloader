"""Immutable configuration schema and defaults."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..immutable import freeze_json
from ..privacy.sensitive_values import is_sensitive_field_name


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Profile(StrictModel):
    default: str = "default"

    @field_validator("default")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("must contain only letters, numbers, '_' or '-'")
        return value


class Output(StrictModel):
    directory_format: str = "%NUM%_%TITLE%_%SUBTITLE%"
    filename_format: str = "%NUM%.%EXT%"
    existing_file: Literal["overwrite", "skip", "rename", "error"] = "overwrite"
    image_format: Literal["JPEG", "PNG", "WEBP"] = "JPEG"
    isolate_by_plugin: bool = False
    # Opt-in only: preserve existing names unless an operator selects a limit.
    max_component_length: int | None = Field(None, ge=16)
    lock_timeout_seconds: float = Field(30.0, ge=0, allow_inf_nan=False)


class Media(StrictModel):
    input_validation: Literal["content_type", "decode", "both"] = "content_type"
    content_type_mismatch: Literal["accept", "error"] = "accept"
    # Opt-in only: arbitrary image dimensions remain accepted by default.
    max_image_pixels: int | None = Field(None, ge=1)


class ConsoleLogging(StrictModel):
    enabled: bool = True


class Logging(StrictModel):
    console: ConsoleLogging = Field(default_factory=ConsoleLogging)
    safe_query_parameters: tuple[str, ...] = ()
    safe_fragment_parameters: tuple[str, ...] = ()

    @field_validator("safe_query_parameters", "safe_fragment_parameters")
    @classmethod
    def safe_parameters(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        names: list[str] = []
        for value in values:
            name = value.lower()
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value)
                or is_sensitive_field_name(name)
                or name in names
            ):
                raise ValueError("safe parameter names are invalid")
            names.append(name)
        return tuple(names)


class Network(StrictModel):
    max_concurrency: int = Field(8, ge=1)
    max_chapter_concurrency: int = Field(3, ge=1)
    host_max_concurrency: int | None = Field(None, ge=1)
    site_max_concurrency: int | None = Field(None, ge=1)
    timeout_seconds: float = Field(30, gt=0)
    connect_timeout_seconds: float | None = Field(None, gt=0)
    read_timeout_seconds: float | None = Field(None, gt=0)
    write_timeout_seconds: float | None = Field(None, gt=0)
    pool_timeout_seconds: float | None = Field(None, gt=0)
    max_retries: int = Field(3, ge=1)
    max_retry_wait_seconds: float = Field(30, gt=0)
    max_auth_retries: int = Field(1, ge=0)
    request_interval_seconds: float = Field(0, ge=0)
    max_connections: int = Field(8, ge=1)
    max_keepalive_connections: int = Field(20, ge=0)
    max_response_bytes: int = Field(64 * 1024 * 1024, ge=1)
    http2: bool = True
    follow_redirects: bool = True
    proxy: str | None = None
    headers: Mapping[str, str] = Field(default_factory=lambda: {"User-Agent": "image-downloader/0.0.0.1b0"})

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "headers", freeze_json(self.headers))


class Email(StrictModel):
    smtp_host: str = ""
    smtp_port: int = Field(465, ge=1, le=65535)
    use_tls: bool = True
    from_: str = Field("", alias="from")
    to: tuple[str, ...] = ()
    username: str = ""
    credential_service: str = "image-downloader.smtp"


NotificationMethod = Literal["desktop", "email"]
NotificationCategory = Literal[
    "fetch_error",
    "process_error",
    "save_error",
    "auth_error",
    "parse_error",
    "plugin_error",
    "config_error",
    "update_error",
    "auth_cookie_store_access",
    "auth_credential_store_access",
    "auth_login_success",
    "auth_session_refresh_success",
    "download_success",
    "download_partial_success",
]


class Notification(StrictModel):
    enabled: bool = False
    methods: tuple[NotificationMethod, ...] = ("desktop",)
    notify_on: tuple[NotificationCategory, ...] = ("fetch_error", "process_error", "save_error", "auth_error")
    routes: Mapping[NotificationCategory, tuple[NotificationMethod, ...]] = Field(default_factory=dict)
    desktop: Mapping[str, Any] = Field(default_factory=dict)
    email: Email = Field(default_factory=lambda: Email.model_validate({}))

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "routes", freeze_json({key: tuple(value) for key, value in self.routes.items()}))
        object.__setattr__(self, "desktop", freeze_json(self.desktop))


def _valid_plugin_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", value))


class PluginSettings(StrictModel):
    enabled: bool = True
    config: Mapping[str, Any] = Field(default_factory=dict)
    secrets: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("secrets")
    @classmethod
    def secret_references(cls, values: Mapping[str, str]) -> Mapping[str, str]:
        for name, reference in values.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)):
                raise ValueError("secret names must be lower_snake_case")
            if not re.fullmatch(r"[A-Z0-9_]+", str(reference)):
                raise ValueError("secret references must be uppercase environment references")
        return values

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "config", freeze_json(self.config))
        object.__setattr__(self, "secrets", freeze_json(self.secrets))


class Security(StrictModel):
    plugin_verification: Literal["strict", "warn", "off"] = "strict"


class Storage(StrictModel):
    """The user-controlled, absolute parent directory for profile data."""

    data_root: str | None = None


class Plugins(StrictModel):
    """The user-controlled, absolute plugin/catalog directory."""

    root: str | None = None


class ImageProcessors(StrictModel):
    chain: tuple[str, ...] = ()

    @field_validator("chain")
    @classmethod
    def unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(not _valid_plugin_id(value) for value in values):
            raise ValueError("image processor IDs are invalid or duplicated")
        return tuple(values)


class GenericHtmlFallback(StrictModel):
    enabled: bool = True


class Fallback(StrictModel):
    generic_html: GenericHtmlFallback = Field(default_factory=GenericHtmlFallback)


class AppConfig(StrictModel):
    profile: Profile = Field(default_factory=Profile)
    storage: Storage = Field(default_factory=Storage)
    plugins: Plugins = Field(default_factory=Plugins)
    output: Output = Field(default_factory=lambda: Output.model_validate({}))
    media: Media = Field(default_factory=lambda: Media.model_validate({}))
    logging: Logging = Field(default_factory=Logging)
    network: Network = Field(default_factory=lambda: Network.model_validate({}))
    notification: Notification = Field(default_factory=Notification)
    security: Security = Field(default_factory=Security)
    image_processors: ImageProcessors = Field(default_factory=ImageProcessors)
    plugin_settings: Mapping[str, PluginSettings] = Field(default_factory=dict)
    fallback: Fallback = Field(default_factory=Fallback)
    continue_on_error: bool = True
    allow_empty_manifest: bool = False

    @field_validator("plugin_settings")
    @classmethod
    def plugin_ids(cls, values: Mapping[str, PluginSettings]) -> Mapping[str, PluginSettings]:
        if any(not _valid_plugin_id(str(key)) for key in values):
            raise ValueError("plugin setting IDs must be reverse-DNS identifiers")
        return values

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "plugin_settings", freeze_json(self.plugin_settings))


DEFAULT_CONFIG = AppConfig()
