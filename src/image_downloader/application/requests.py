"""Immutable inputs to the public application operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .._numeric import numeric_value_error
from ..configuration.models import ImageFormat
from ..exceptions import ConfigurationError
from ..immutable import freeze_json
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides


def _freeze_options(request: object, name: str, value: PluginConfigOverrides | None) -> None:
    if value is not None:
        object.__setattr__(request, name, cast(PluginConfigOverrides, freeze_json(value)))


def _validate_selection(plugin_id: str | None, force_plugin: bool, fallback_override: bool | None) -> None:
    if plugin_id is not None and fallback_override is not None:
        raise ConfigurationError("explicit plugin selection cannot be combined with fallback_override")
    if force_plugin and plugin_id is None:
        raise ConfigurationError("force_plugin requires plugin_id")


def _validate_format(value: ImageFormat | None) -> None:
    if value not in (None, "ORIGINAL", "JPEG", "PNG", "WEBP"):
        raise ValueError("force_image_format must be ORIGINAL, JPEG, PNG, WEBP, or None")


def _validate_retry(retries: int, delay: float, timeout: float | None) -> None:
    # API retry counts have no float-conversion bound; durations must be finite.
    if type(retries) is not int or retries < 0:
        raise ValueError("workflow_retries must be a non-negative integer")
    for name, value, positive in (
        ("workflow_retry_delay", delay, False),
        ("workflow_retry_timeout", timeout, True),
    ):
        if name == "workflow_retry_timeout" and value is None:
            continue
        error = numeric_value_error(value, positive=positive)
        if error is not None:
            raise ValueError(f"{name} {error}")


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadRequest:
    """Inputs for the download operation."""

    url: str
    plugin_overrides: PluginConfigOverrides | None = None
    fallback_override: bool | None = None
    plugin_id: str | None = None
    force_plugin: bool = False
    plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None
    force_image_format: ImageFormat | None = None

    def __post_init__(self) -> None:
        _validate_selection(self.plugin_id, self.force_plugin, self.fallback_override)
        _freeze_options(self, "plugin_overrides", self.plugin_overrides)
        _freeze_options(self, "plugin_download_policy_overrides", self.plugin_download_policy_overrides)
        _validate_format(self.force_image_format)


@dataclass(frozen=True, slots=True, kw_only=True)
class InspectRequest:
    """Inputs for the inspect operation."""

    url: str
    plugin_overrides: PluginConfigOverrides | None = None
    fallback_override: bool | None = None
    plugin_id: str | None = None
    force_plugin: bool = False
    resolve_image_requests: bool = True

    def __post_init__(self) -> None:
        _validate_selection(self.plugin_id, self.force_plugin, self.fallback_override)
        _freeze_options(self, "plugin_overrides", self.plugin_overrides)
        if not isinstance(self.resolve_image_requests, bool):
            raise TypeError("resolve_image_requests must be bool")


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateCheckRequest:
    """Inputs for the check_updates operation."""

    url: str
    plugin_overrides: PluginConfigOverrides | None = None
    fallback_override: bool | None = None
    plugin_id: str | None = None
    force_plugin: bool = False
    plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None

    def __post_init__(self) -> None:
        _validate_selection(self.plugin_id, self.force_plugin, self.fallback_override)
        _freeze_options(self, "plugin_overrides", self.plugin_overrides)
        _freeze_options(self, "plugin_download_policy_overrides", self.plugin_download_policy_overrides)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowPlanRequest:
    """Inputs for the plan_workflow operation."""

    url: str
    download_scope: Literal["all", "updated"] = "updated"
    plugin_overrides: PluginConfigOverrides | None = None
    fallback_override: bool | None = None
    plugin_id: str | None = None
    force_plugin: bool = False
    plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None

    def __post_init__(self) -> None:
        _validate_selection(self.plugin_id, self.force_plugin, self.fallback_override)
        _freeze_options(self, "plugin_overrides", self.plugin_overrides)
        _freeze_options(self, "plugin_download_policy_overrides", self.plugin_download_policy_overrides)
        if self.download_scope not in ("all", "updated"):
            raise ValueError("download_scope must be all or updated")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowRequest:
    """Inputs for the workflow operation."""

    url: str
    download_scope: Literal["all", "updated"] = "updated"
    workflow_retries: int = 1
    workflow_retry_delay: float = 600.0
    workflow_retry_timeout: float | None = None
    workflow_progress_log: bool | None = None
    plugin_overrides: PluginConfigOverrides | None = None
    fallback_override: bool | None = None
    plugin_id: str | None = None
    force_plugin: bool = False
    plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None
    force_image_format: ImageFormat | None = None

    def __post_init__(self) -> None:
        _validate_selection(self.plugin_id, self.force_plugin, self.fallback_override)
        _freeze_options(self, "plugin_overrides", self.plugin_overrides)
        _freeze_options(self, "plugin_download_policy_overrides", self.plugin_download_policy_overrides)
        _validate_format(self.force_image_format)
        if self.download_scope not in ("all", "updated"):
            raise ValueError("download_scope must be all or updated")
        _validate_retry(self.workflow_retries, self.workflow_retry_delay, self.workflow_retry_timeout)
        if self.workflow_progress_log is not None and type(self.workflow_progress_log) is not bool:
            raise ValueError("workflow_progress_log must be bool or None")
