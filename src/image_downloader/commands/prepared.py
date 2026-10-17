"""Validated CLI inputs, separate from parser-owned syntax metadata."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, Protocol, cast

from ..configuration.models import ImageFormat
from ..immutable import freeze_json
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides, PluginVerificationOverride


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigurationArguments:
    """Configuration selection shared by commands; parser metadata stays outside."""

    config: Path | None = None
    profile: str | None = None
    data_root: Path | None = None
    plugin_root: Path | None = None
    plugin_verification_override: PluginVerificationOverride | None = None
    json_output: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class SiteArguments(ConfigurationArguments):
    fallback_generic: Literal["auto", "enabled", "disabled"] = "auto"
    plugin_id: str | None = None
    force_plugin_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadOutputArguments(SiteArguments):
    existing_file: Literal["overwrite", "skip", "rename", "error"] | None = None
    image_format: ImageFormat | None = None
    force_image_format: ImageFormat | None = None
    output_dir: Path | None = None
    directory_format: str | None = None
    no_console_log: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadArguments(DownloadOutputArguments):
    url: str
    inspect_only: bool = False
    list_updated_urls: bool = False
    manifest_only: bool = False
    inspection_data: Literal["url", "http", "all"] = "url"


@dataclass(frozen=True, slots=True, kw_only=True)
class InspectArguments(SiteArguments):
    url: str
    manifest_only: bool = False
    inspection_data: Literal["url", "http", "all"] = "url"


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowArguments(DownloadOutputArguments):
    url: str
    dry_run: bool = False
    download_scope: Literal["all", "updated"] | None = None
    workflow_retries: int | None = None
    workflow_retry_delay: float | None = None
    workflow_retry_timeout: float | None = None
    workflow_progress_log: Literal["enabled", "disabled"] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DoctorArguments(ConfigurationArguments):
    host: str | None = None
    fallback_generic: Literal["auto", "enabled", "disabled"] = "auto"
    existing_file: Literal["overwrite", "skip", "rename", "error"] | None = None
    image_format: ImageFormat | None = None
    no_console_log: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigArguments(ConfigurationArguments):
    command_args: tuple[str, ...]
    host: str | None = None
    existing_file: Literal["overwrite", "skip", "rename", "error"] | None = None
    image_format: ImageFormat | None = None
    no_console_log: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class PluginArguments(ConfigurationArguments):
    command_args: tuple[str, ...]
    yes: bool = False
    selection_priority: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class CookieArguments(ConfigurationArguments):
    cookie_action: Literal["export", "import", "browser-import"]
    cookie_value: str | Path


@dataclass(frozen=True, slots=True, kw_only=True)
class StateArguments(ConfigurationArguments):
    command_args: tuple[str, ...]
    plugin_id: str | None = None
    limit: int | None = None
    dry_run: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifyArguments(ConfigurationArguments):
    verify_resource: Literal["logs", "workflow"]
    target: str
    output_dir: Path | None = None


CommandArguments = (
    DownloadArguments
    | InspectArguments
    | WorkflowArguments
    | DoctorArguments
    | ConfigArguments
    | PluginArguments
    | CookieArguments
    | StateArguments
    | VerifyArguments
)

_ARGUMENT_TYPES: dict[str, type[ConfigurationArguments]] = {
    "download": DownloadArguments,
    "inspect": InspectArguments,
    "workflow": WorkflowArguments,
    "doctor": DoctorArguments,
    "config": ConfigArguments,
    "plugin": PluginArguments,
    "cookie": CookieArguments,
    "state": StateArguments,
    "verify": VerifyArguments,
}


def command_arguments(name: str, namespace: argparse.Namespace) -> CommandArguments:
    """Copy validated parser values once into the command's closed, immutable shape."""
    argument_type = _ARGUMENT_TYPES[name]
    values = {
        field.name: getattr(namespace, field.name) for field in fields(argument_type) if hasattr(namespace, field.name)
    }
    if "command_args" in values:
        values["command_args"] = tuple(values["command_args"])
    if name == "cookie" and values.get("cookie_action") is None:
        for action, attribute in (
            ("export", "export_cookies"),
            ("import", "import_cookies"),
            ("browser-import", "import_browser_cookies"),
        ):
            if (value := getattr(namespace, attribute, None)) is not None:
                values.update(cookie_action=action, cookie_value=value)
                break
    return cast(CommandArguments, argument_type(**values))


@dataclass(frozen=True, slots=True)
class PreparedCommand:
    name: str
    arguments: CommandArguments
    plugin_config: PluginConfigOverrides
    download_policy: PluginDownloadPolicyOverrides

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_config", cast(PluginConfigOverrides, freeze_json(self.plugin_config)))
        object.__setattr__(
            self, "download_policy", cast(PluginDownloadPolicyOverrides, freeze_json(self.download_policy))
        )


class CommandHandler(Protocol):
    async def handle(self, command: PreparedCommand) -> int: ...
