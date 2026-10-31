"""Construction and cleanup of one selected site operation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace

from ..configuration.models import AppConfig, PluginDownloadPolicy
from ..credentials.plugin_secrets import RuntimeSecrets
from ..credentials.profile_store import ProfileCredentialStore
from ..exceptions import (
    ConfigurationError,
)
from ..models import (
    DownloadManifest,
    RequestResponse,
    RequestSpec,
)
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.logging import DownloadLogger
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import PluginExecutionContext, SitePlugin
from ..transport.gateway import OperationRequestGateway, RequestGateway


@dataclass(frozen=True, slots=True)
class _OperationDownloadPolicy:
    request_concurrency: int
    chapter_concurrency: int
    image_concurrency_per_chapter: int


def _effective_download_policy(config: AppConfig, policy: PluginDownloadPolicy) -> _OperationDownloadPolicy:
    request_concurrency = min(
        config.network.request_concurrency,
        policy.request_concurrency or config.network.request_concurrency,
    )
    chapter_concurrency = min(
        config.download.chapter_concurrency,
        policy.chapter_concurrency or config.download.chapter_concurrency,
    )
    image_concurrency = min(
        config.download.image_concurrency_per_chapter,
        policy.image_concurrency_per_chapter or config.download.image_concurrency_per_chapter,
    )
    if policy.preserve_image_start_order:
        chapter_concurrency = image_concurrency = 1
    return _OperationDownloadPolicy(request_concurrency, chapter_concurrency, image_concurrency)


class _PluginRequests:
    """Capability-reduced request port exposed through PluginExecutionContext."""

    __slots__ = ("_gateway",)

    def __init__(self, gateway: OperationRequestGateway) -> None:
        self._gateway = gateway

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        return await self._gateway.execute(spec)


@dataclass(frozen=True, slots=True)
class SiteOperation:
    """The named, operation-owned capabilities of one selected site plugin."""

    record: PluginRecord
    plugin: SitePlugin
    context: PluginExecutionContext
    gateway: OperationRequestGateway
    policy: _OperationDownloadPolicy
    invoker: PluginInvoker


@dataclass(slots=True)
class SiteSelection:
    """Own a selected instance until an operation takes responsibility for it."""

    record: PluginRecord
    plugin: SitePlugin
    owned: bool = field(default=True, init=False)

    def claim(self) -> tuple[PluginRecord, SitePlugin]:
        if not self.owned:
            raise RuntimeError("site selection has already been claimed")
        self.owned = False
        return self.record, self.plugin


class SiteOperationFactory:
    def __init__(
        self,
        config: AppConfig,
        registry: PluginRuntime,
        gateway: RequestGateway,
        logger: DownloadLogger,
        credentials: ProfileCredentialStore,
    ):
        self.config = config
        self.registry = registry
        self.gateway = gateway
        self.logger = logger
        self.credentials = credentials

    def select(
        self,
        url: str,
        overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        plugin_id: str | None,
        force_plugin: bool,
    ) -> tuple[PluginRecord, SitePlugin]:
        if plugin_id is not None and fallback_override is not None:
            raise ConfigurationError("explicit plugin selection cannot be combined with fallback_override")
        fallback_enabled = self.config.fallback.generic_html.enabled if fallback_override is None else fallback_override
        return self.registry.resolve(
            url,
            fallback_enabled=fallback_enabled,
            overrides=overrides,
            plugin_id=plugin_id,
            force_plugin=force_plugin,
        )

    @asynccontextmanager
    async def selection(
        self,
        url: str,
        overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        plugin_id: str | None,
        force_plugin: bool,
        *,
        diagnostics_enabled: bool = True,
    ) -> AsyncIterator[SiteSelection]:
        selected = SiteSelection(*self.select(url, overrides, fallback_override, plugin_id, force_plugin))
        try:
            yield selected
        except BaseException as primary:
            await self.release_selection(selected, primary, diagnostics_enabled=diagnostics_enabled)
            raise
        else:
            await self.release_selection(selected, diagnostics_enabled=diagnostics_enabled)

    async def release_selection(
        self,
        selected: SiteSelection,
        primary: BaseException | None = None,
        *,
        diagnostics_enabled: bool = True,
    ) -> None:
        """Release an unclaimed selection, optionally before its feed lock exits."""
        if not selected.owned:
            return
        record, plugin = selected.claim()
        logger = self.logger if diagnostics_enabled else None
        invoker = PluginInvoker(record.id, logger)
        if primary is None:
            await invoker.cleanup_after_use(plugin)
        else:
            await self.cleanup_after_failure(invoker, plugin, primary, logger)

    def build(
        self,
        url: str,
        record: PluginRecord,
        plugin: SitePlugin,
        invoker: PluginInvoker,
        overrides: PluginConfigOverrides | None,
        download_policy_overrides: PluginDownloadPolicyOverrides | None,
        request_gateway: RequestGateway | None = None,
    ) -> tuple[PluginExecutionContext, OperationRequestGateway, _OperationDownloadPolicy]:
        self.registry.validate_operation_overrides(record, overrides)
        self.registry.validate_download_policy_overrides(record, download_policy_overrides)
        policy = _effective_download_policy(
            self.config,
            self.registry.effective_download_policy(record, download_policy_overrides),
        )
        settings = self.config.plugin_settings.get(record.id)
        secrets = RuntimeSecrets(record.id, settings.secrets if settings else {}, store=self.credentials)
        context: PluginExecutionContext | None = None

        def auth_flow_factory(session: OperationRequestGateway) -> object | None:
            nonlocal context
            context = PluginExecutionContext(
                self.registry.effective_config(record, overrides),
                safe_app_settings(self.config),
                record.manifest.value,
                record.catalog.as_json() if record.catalog else None,
                secrets,
                _PluginRequests(session),
                cookie_provider=request_gateway or self.gateway,
            )
            return invoker.auth_flow(plugin, context)

        operation_gateway = (request_gateway or self.gateway).operation(
            plugin_id=record.id,
            operation_url=url,
            auth_flow_factory=auth_flow_factory,
            invoker=invoker,
            request_concurrency=policy.request_concurrency,
        )
        if context is None:
            raise RuntimeError("operation context was not initialized")
        return context, operation_gateway, policy

    @asynccontextmanager
    async def open(
        self,
        url: str,
        overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
        request_gateway: RequestGateway | None = None,
        diagnostics_enabled: bool = True,
        selected_plugin: SiteSelection | None = None,
    ) -> AsyncIterator[SiteOperation]:
        record, plugin = (
            selected_plugin.claim()
            if selected_plugin is not None
            else self.select(url, overrides, fallback_override, plugin_id, force_plugin)
        )
        logger: DownloadLogger | None = self.logger if diagnostics_enabled else None
        invoker = PluginInvoker(record.id, logger)
        try:
            context, operation_gateway, policy = self.build(
                url,
                record,
                plugin,
                invoker,
                overrides,
                download_policy_overrides,
                request_gateway,
            )
        except BaseException as primary:
            await self.cleanup_after_failure(invoker, plugin, primary, logger)
            raise
        try:
            yield SiteOperation(record, plugin, context, operation_gateway, policy, invoker)
        except BaseException as primary:
            await self.cleanup_after_failure(invoker, plugin, primary, logger)
            raise
        else:
            await invoker.cleanup_after_use(plugin)

    async def cleanup_after_failure(
        self,
        invoker: PluginInvoker,
        plugin: SitePlugin,
        _primary: BaseException,
        logger: DownloadLogger | None,
    ) -> None:
        try:
            await invoker.cleanup_after_use(plugin)
        except BaseException as cleanup_error:
            if logger is not None and isinstance(cleanup_error, Exception):
                await best_effort_diagnostic(
                    logger.core,
                    "site_operation_close_failed",
                    module="plugin",
                    plugin_id=invoker.plugin_id,
                    error=cleanup_error,
                    debug=True,
                )

    def python_log_namespaces(self, site_record: PluginRecord, *, include_processors: bool) -> tuple[str, ...]:
        records = [site_record]
        if include_processors:
            records.extend(
                record
                for plugin_id in self.config.image_processors.chain
                if (record := self.registry.records.get(plugin_id)) is not None and self.registry.enabled(record)
            )
        namespaces = (self.registry.module_namespace(record) for record in records)
        return tuple(namespace for namespace in namespaces if namespace is not None)

    @staticmethod
    def normalize_manifest(manifest: DownloadManifest, url: str) -> DownloadManifest:
        return replace(manifest, metadata={**manifest.metadata, "source_url": url})
