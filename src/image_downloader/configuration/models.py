"""Immutable configuration schema and defaults."""

from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ..immutable import freeze_json
from ..privacy.sensitive_values import is_sensitive_field_name


def _strict_finite_number(value: object) -> float | int:
    """Accept YAML numeric scalars, but never bools, strings, NaN, or infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("must be a finite number")
    return value


FiniteNumber = Annotated[float, BeforeValidator(_strict_finite_number)]
ImageFormat = Literal["ORIGINAL", "JPEG", "PNG", "WEBP"]
_IMAGE_FILENAME_TOKENS = ("%ORIGINAL_STEM%", "%ORIGINAL_FILENAME%", "%ORIGINAL_EXT%")
_LEGACY_NUMBER_TOKEN = "%NUM%"
_IMAGE_INDEX_TOKEN = "%IMAGE_INDEX%"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Profile(StrictModel):
    default: StrictStr = "default"

    @field_validator("default")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("must contain only letters, numbers, '_' or '-'")
        return value


class Output(StrictModel):
    directory_format: StrictStr = "%CHAPTER_NUMBER%_%TITLE%_%SUBTITLE%"
    filename_format: StrictStr = "%IMAGE_INDEX%.%EXT%"
    existing_file: Literal["overwrite", "skip", "rename", "error"] = "overwrite"
    image_format: ImageFormat = "ORIGINAL"
    isolate_by_plugin: StrictBool = False
    # Opt-in only: preserve existing names unless an operator selects a limit.
    max_component_length: StrictInt | None = Field(None, ge=16)
    lock_timeout_seconds: FiniteNumber = Field(30.0, ge=0)

    @model_validator(mode="after")
    def format_tokens_are_valid(self) -> Output:
        if _LEGACY_NUMBER_TOKEN in self.directory_format or _LEGACY_NUMBER_TOKEN in self.filename_format:
            raise ValueError(
                "output format cannot contain %NUM%; use %CHAPTER_NUMBER% or %IMAGE_INDEX% instead"
            )
        if _IMAGE_INDEX_TOKEN in self.directory_format or any(
            token in self.directory_format for token in _IMAGE_FILENAME_TOKENS
        ):
            raise ValueError("output.directory_format cannot contain image-only tokens")
        return self


class Media(StrictModel):
    input_validation: Literal["content_type", "decode", "both"] = "content_type"
    content_type_mismatch: Literal["accept", "error"] = "accept"
    # Opt-in only: arbitrary image dimensions remain accepted by default.
    max_image_pixels: StrictInt | None = Field(None, ge=1)


class ConsoleLogging(StrictModel):
    enabled: StrictBool = True


class Logging(StrictModel):
    console: ConsoleLogging = Field(default_factory=ConsoleLogging)
    safe_query_parameters: tuple[StrictStr, ...] = ()
    safe_fragment_parameters: tuple[StrictStr, ...] = ()

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
    request_concurrency: StrictInt = Field(8, ge=1)
    origin_request_concurrency: StrictInt | None = Field(None, ge=1)
    registrable_domain_request_concurrency: StrictInt | None = Field(None, ge=1)
    request_timeout_seconds: FiniteNumber = Field(30, gt=0)
    connect_timeout_seconds: FiniteNumber | None = Field(None, gt=0)
    read_timeout_seconds: FiniteNumber | None = Field(None, gt=0)
    write_timeout_seconds: FiniteNumber | None = Field(None, gt=0)
    pool_timeout_seconds: FiniteNumber | None = Field(None, gt=0)
    max_attempts: StrictInt = Field(3, ge=1)
    retry_max_delay_seconds: FiniteNumber = Field(30, ge=0)
    auth_refresh_attempts: StrictInt = Field(1, ge=0)
    global_request_interval_seconds: FiniteNumber = Field(0, ge=0)
    pool_max_connections: StrictInt = Field(8, ge=1)
    pool_max_idle_connections: StrictInt = Field(8, ge=0)
    max_response_bytes: StrictInt = Field(64 * 1024 * 1024, ge=1)
    http2: StrictBool = True
    follow_redirects: StrictBool = True
    proxy: StrictStr | None = None
    headers: Mapping[StrictStr, StrictStr] = Field(default_factory=lambda: {"User-Agent": "image-downloader/0.0.0.1b0"})

    @model_validator(mode="after")
    def coherent_limits(self) -> Network:
        if self.pool_max_idle_connections > self.pool_max_connections:
            raise ValueError("pool_max_idle_connections must not exceed pool_max_connections")
        for name, value in (
            ("origin_request_concurrency", self.origin_request_concurrency),
            ("registrable_domain_request_concurrency", self.registrable_domain_request_concurrency),
        ):
            if value is not None and value > self.request_concurrency:
                raise ValueError(f"{name} must not exceed request_concurrency")
        return self

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "headers", freeze_json(self.headers))


class Email(StrictModel):
    smtp_host: StrictStr = ""
    smtp_port: StrictInt = Field(465, ge=1, le=65535)
    use_tls: StrictBool = True
    from_: StrictStr = Field("", alias="from")
    to: tuple[StrictStr, ...] = ()
    username: StrictStr = ""
    credential_service: StrictStr = "image-downloader.smtp"


NotificationMethod = Literal["desktop", "email"]
NotificationCategory = Literal[
    "fetch_error",
    "process_error",
    "save_error",
    "auth_error",
    "parse_error",
    "plugin_error",
    "config_error",
    "storage_error",
    "runtime_error",
    "update_error",
    "auth_cookie_store_access",
    "auth_credential_store_access",
    "auth_login_success",
    "auth_session_refresh_success",
    "download_success",
    "download_partial_success",
]


class Notification(StrictModel):
    enabled: StrictBool = False
    methods: tuple[NotificationMethod, ...] = ("desktop",)
    notify_on: tuple[NotificationCategory, ...] = (
        "fetch_error",
        "process_error",
        "save_error",
        "auth_error",
        "config_error",
        "plugin_error",
        "update_error",
        "storage_error",
        "runtime_error",
    )
    routes: Mapping[NotificationCategory, tuple[NotificationMethod, ...]] = Field(default_factory=dict)
    desktop: Mapping[str, Any] = Field(default_factory=dict)
    email: Email = Field(default_factory=lambda: Email.model_validate({}))

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "routes", freeze_json({key: tuple(value) for key, value in self.routes.items()}))
        object.__setattr__(self, "desktop", freeze_json(self.desktop))


def _valid_plugin_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", value))


class PluginDownloadPolicy(StrictModel):
    """Core-owned limits and ordering for one selected site plugin operation."""

    request_concurrency: StrictInt | None = Field(None, ge=1)
    chapter_concurrency: StrictInt | None = Field(None, ge=1)
    image_concurrency_per_chapter: StrictInt | None = Field(None, ge=1)
    preserve_image_start_order: StrictBool = False


class PluginSettings(StrictModel):
    enabled: StrictBool = True
    config: Mapping[str, Any] = Field(default_factory=dict)
    secrets: Mapping[StrictStr, StrictStr] = Field(default_factory=dict)
    download_policy: PluginDownloadPolicy = Field(default_factory=PluginDownloadPolicy)

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

    data_root: StrictStr | None = None

    @field_validator("data_root")
    @classmethod
    def absolute_or_automatic(cls, value: str | None) -> str | None:
        if value is not None and (not value or not Path(value).is_absolute()):
            raise ValueError("must be null or an absolute path")
        return value


class Plugins(StrictModel):
    """The user-controlled, absolute plugin/catalog directory."""

    root: StrictStr | None = None

    @field_validator("root")
    @classmethod
    def absolute_or_automatic(cls, value: str | None) -> str | None:
        if value is not None and (not value or not Path(value).is_absolute()):
            raise ValueError("must be null or an absolute path")
        return value


class ImageProcessors(StrictModel):
    chain: tuple[StrictStr, ...] = ()
    transport_metadata_access: Mapping[StrictStr, tuple[StrictStr, ...]] = Field(default_factory=dict)

    @field_validator("chain")
    @classmethod
    def unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(not _valid_plugin_id(value) for value in values):
            raise ValueError("image processor IDs are invalid or duplicated")
        return tuple(values)

    @field_validator("transport_metadata_access")
    @classmethod
    def valid_transport_metadata_access(
        cls, values: Mapping[str, tuple[str, ...]]
    ) -> Mapping[str, tuple[str, ...]]:
        for processor_id, site_ids in values.items():
            if not _valid_plugin_id(processor_id) or not isinstance(site_ids, tuple):
                raise ValueError("transport metadata access IDs are invalid")
            if (
                not site_ids
                or len(site_ids) != len(set(site_ids))
                or any(not _valid_plugin_id(site_id) for site_id in site_ids)
            ):
                raise ValueError("transport metadata access site IDs are invalid or duplicated")
        return freeze_json(values)


class GenericHtmlFallback(StrictModel):
    enabled: StrictBool = True


class Fallback(StrictModel):
    generic_html: GenericHtmlFallback = Field(default_factory=GenericHtmlFallback)


class Download(StrictModel):
    chapter_concurrency: StrictInt = Field(3, ge=1)
    image_concurrency_per_chapter: StrictInt = Field(8, ge=1)
    continue_on_image_error: StrictBool = True
    allow_empty_chapter_manifest: StrictBool = False


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
    download: Download = Field(default_factory=Download)
    image_processors: ImageProcessors = Field(default_factory=ImageProcessors)
    plugin_settings: Mapping[str, PluginSettings] = Field(default_factory=dict)
    fallback: Fallback = Field(default_factory=Fallback)

    @field_validator("plugin_settings")
    @classmethod
    def plugin_ids(cls, values: Mapping[str, PluginSettings]) -> Mapping[str, PluginSettings]:
        if any(not _valid_plugin_id(str(key)) for key in values):
            raise ValueError("plugin setting IDs must be reverse-DNS identifiers")
        return values

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "plugin_settings", freeze_json(self.plugin_settings))


DEFAULT_CONFIG = AppConfig()
