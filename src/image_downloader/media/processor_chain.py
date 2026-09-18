"""Operation-scoped image processor instances and cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal

from ..exceptions import ConfigurationError
from ..observability.logging import DownloadLogger
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import ImageProcessor


@dataclass(frozen=True, slots=True)
class PreparedProcessor:
    plugin_id: str
    record: PluginRecord
    instance: ImageProcessor
    config: Mapping[str, Any]
    app_settings: Mapping[str, Any]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False, repr=False)


class OperationProcessorChain:
    """Prepare once per download, then close every constructed processor."""

    def __init__(
        self,
        registry: PluginRuntime,
        plugin_ids: Sequence[str],
        overrides: PluginConfigOverrides | None,
        logger: DownloadLogger,
    ) -> None:
        self.registry = registry
        self.plugin_ids = plugin_ids
        self.overrides = overrides
        self.logger = logger
        self.bindings: tuple[PreparedProcessor | str, ...] = ()
        self._constructed: list[PreparedProcessor] = []

    async def __aenter__(self) -> OperationProcessorChain:
        bindings: list[PreparedProcessor | str] = []
        app_settings = safe_app_settings(self.registry.config)
        try:
            for plugin_id in self.plugin_ids:
                record = self.registry.records.get(plugin_id)
                if record is None or record.kind != "image_processor_plugin":
                    raise ConfigurationError(f"configured image processor is unavailable: {plugin_id}")
                if not self.registry.enabled(record):
                    bindings.append(plugin_id)
                    continue
                config = self.registry.effective_config(record, self.overrides)
                instance = self.registry.processor_instance(record)
                binding = PreparedProcessor(
                    plugin_id,
                    record,
                    instance,
                    config,
                    app_settings,
                )
                self._constructed.append(binding)
                PluginInvoker(plugin_id).validate_config(instance, binding.config, app_settings)
                bindings.append(binding)
        except BaseException as exc:
            await self._close(primary=exc)
            raise
        self.bindings = tuple(bindings)
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        await self._close(primary=exc)
        return False

    async def _close(self, *, primary: BaseException | None) -> None:
        constructed, self._constructed = self._constructed, []
        first_error: BaseException | None = None
        for binding in reversed(constructed):
            try:
                await PluginInvoker(binding.plugin_id, self.logger).close_processor(binding.instance)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                if primary is not None and isinstance(exc, Exception):
                    try:
                        await self.logger.core(
                            "processor_close_failed",
                            module="processor",
                            plugin_id=binding.plugin_id,
                            error=exc,
                            debug=True,
                        )
                    except BaseException:
                        pass
        if first_error is not None and primary is None:
            raise first_error
