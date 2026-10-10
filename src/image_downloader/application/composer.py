"""Compose the service and its infrastructure."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..configuration.models import AppConfig
from ..configuration.paths import resolve_paths
from ..media.image_processor import ImageProcessor
from ..observability.events import EventBus
from ..observability.logging import (
    ConsoleSink,
    DebugFileSink,
    DownloadLogger,
    LogSink,
)
from ..observability.notifications import (
    NotificationService,
    default_notification_senders,
    validate_notification_delivery,
)
from ..output.output_lock import OutputDirectoryLocks
from ..plugins.builtin import GenericHtmlPlugin
from ..plugins.plugin_manifest import PluginVerificationOverride, effective_verification_mode
from ..plugins.runtime import PluginRuntime
from ..storage import FileSystem
from ..storage.cookies import CookieStore
from ..storage.path_safety import existing_directory
from ..storage.state import UpdateState
from ..transport.gateway import RequestGateway
from .construction import _ConstructionGuard
from .dependencies import _RuntimeDependencies
from .service import DownloadService


class RuntimeComposer:
    def __init__(
        self,
        config: AppConfig,
        *,
        config_root: Path,
        plugin_root: Path,
        output_root: Path | None = None,
        plugin_verification_override: PluginVerificationOverride | None = None,
    ) -> None:
        config_root = existing_directory(config_root, "configuration root", required=True)
        plugin_root = existing_directory(plugin_root, "plugin root")
        output_root = existing_directory(output_root, "output_root") if output_root is not None else None
        self.config = config
        self.config_root = config_root
        self.plugin_root = plugin_root
        self.output_root = output_root
        self.plugin_verification_override = plugin_verification_override

    def compose(self) -> DownloadService:
        validate_notification_delivery(self.config.notification)
        return self._compose_service(self._build_dependencies)

    def _compose_for_inspection(self) -> DownloadService:
        """Compose the CLI-only sinkless dependencies for a display-only operation."""
        return self._compose_service(lambda: self._build_dependencies(inspection=True))

    def _compose_for_workflow_plan(self) -> DownloadService:
        """Compose a sinkless service that never persists its cookie session."""
        return self._compose_service(lambda: self._build_dependencies(inspection=True, planning=True))

    def _compose_service(self, build_dependencies: Callable[[], _RuntimeDependencies]) -> DownloadService:
        with _ConstructionGuard() as guard:
            registry = self.compose_registry()
            guard.callback("plugins", registry.close)
            dependencies = build_dependencies()
            guard.async_callback("logging", dependencies.logger.close)
            guard.async_callback("http", dependencies.gateway.close)
            guard.callback("image", dependencies.image_processor.close)
            service = DownloadService(self.config, registry, dependencies)
            guard.release()
            return service

    def _build_dependencies(self, *, inspection: bool = False, planning: bool = False) -> _RuntimeDependencies:
        with _ConstructionGuard() as guard:
            dependencies = self._assemble_dependencies(guard, inspection=inspection, planning=planning)
            guard.release()
            return dependencies

    def _assemble_dependencies(
        self, guard: _ConstructionGuard, *, inspection: bool, planning: bool
    ) -> _RuntimeDependencies:
        paths = resolve_paths(self.config)
        outputs = FileSystem(self.output_root or paths["downloads"])
        logs = FileSystem(paths["logs"])
        state = UpdateState(FileSystem(paths["state"]))
        logger_sinks: list[LogSink] = []
        sink_cleanups = []
        if not inspection:
            sink = DebugFileSink(filesystem=logs, relative_path=Path("debug.log"))
            sink_cleanups.append(guard.async_callback("logging", sink.close))
            logger_sinks.append(sink)
        if not inspection and self.config.logging.console.enabled:
            console = ConsoleSink()
            sink_cleanups.append(guard.async_callback("logging", console.close))
            logger_sinks.append(console)
        logger = DownloadLogger(logger_sinks)
        guard.async_callback("logging", logger.close)
        for cleanup in sink_cleanups:
            guard.discard(cleanup)
        logger.set_chapter_summary_console(not inspection and self.config.logging.console.enabled)
        events = EventBus(logger)
        rendered_config = self.config.model_dump(by_alias=True, warnings=False)
        logger.configure_safety(rendered_config["logging"], outputs.root)
        events.configure_safety(rendered_config["logging"], str(outputs.root))
        notifications = NotificationService(
            self.config.notification.model_copy(update={"enabled": False}) if planning else self.config.notification,
            logger,
            events,
            default_notification_senders(self.config.notification),
        )
        cookie_store = CookieStore(FileSystem(paths["cookie"]))
        gateway = RequestGateway(self.config, cookie_store.load(), logger=logger)
        guard.async_callback("http", gateway.close)
        cookie_baseline = cookie_store.snapshot(gateway.client.cookies.jar)
        image_processor = ImageProcessor()
        guard.callback("image", image_processor.close)
        output_locks = OutputDirectoryLocks(
            state.filesystem,
            timeout_seconds=self.config.output.lock_timeout_seconds,
            logger=logger,
        )
        return _RuntimeDependencies(
            outputs=outputs,
            logs=logs,
            state=state,
            events=events,
            logger=logger,
            notifications=notifications,
            cookie_store=cookie_store,
            cookie_baseline=cookie_baseline,
            gateway=gateway,
            image_processor=image_processor,
            output_locks=output_locks,
            persist_cookies_on_close=not planning,
        )

    def compose_registry(self) -> PluginRuntime:
        """Build the local plugin snapshot without opening runtime data stores."""
        mode = effective_verification_mode(self.config.security.plugin_verification, self.plugin_verification_override)
        with _ConstructionGuard() as guard:
            registry = PluginRuntime(self.config, self.plugin_root, mode=mode)
            guard.callback("plugins", registry.close)
            registry.register_builtin(GenericHtmlPlugin)
            registry.prepare()
            guard.release()
            return registry
