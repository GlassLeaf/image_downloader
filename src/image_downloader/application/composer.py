"""Compose the service and its infrastructure."""

from __future__ import annotations

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
from ..plugins.runtime import PluginRuntime
from ..storage import FileSystem
from ..storage.cookies import CookieStore
from ..storage.path_safety import existing_directory
from ..storage.state import UpdateState
from ..transport.gateway import RequestGateway
from .dependencies import _RuntimeDependencies
from .service import DownloadService


class RuntimeComposer:
    def __init__(
        self,
        config: AppConfig,
        *,
        config_root: Path,
        plugin_root: Path,
    ) -> None:
        config_root = existing_directory(config_root, "configuration root", required=True)
        plugin_root = existing_directory(plugin_root, "plugin root")
        self.config, self.config_root, self.plugin_root = config, config_root, plugin_root

    def compose(self) -> DownloadService:
        validate_notification_delivery(self.config.notification)
        registry = self.compose_registry()
        return DownloadService(self.config, registry, self._build_dependencies())

    def _build_dependencies(self) -> _RuntimeDependencies:
        paths = resolve_paths(self.config)
        outputs = FileSystem(paths["downloads"])
        logs = FileSystem(paths["logs"])
        state = UpdateState(FileSystem(paths["state"]))
        logger_sinks: list[LogSink] = [DebugFileSink(filesystem=logs, relative_path=Path("debug.log"))]
        if self.config.logging.console.enabled:
            logger_sinks.append(ConsoleSink())
        logger = DownloadLogger(logger_sinks)
        logger.set_chapter_summary_console(self.config.logging.console.enabled)
        events = EventBus(logger)
        rendered_config = self.config.model_dump(by_alias=True, warnings=False)
        logger.configure_safety(rendered_config["logging"], outputs.root)
        events.configure_safety(rendered_config["logging"], str(outputs.root))
        notifications = NotificationService(
            self.config.notification,
            logger,
            events,
            default_notification_senders(self.config.notification),
        )
        cookie_store = CookieStore(FileSystem(paths["cookie"]))
        gateway = RequestGateway(self.config, cookie_store.load(), logger=logger)
        cookie_baseline = cookie_store.snapshot(gateway.client.cookies.jar)
        image_processor = ImageProcessor()
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
        )

    def compose_registry(self) -> PluginRuntime:
        """Build the local plugin snapshot without opening runtime data stores."""
        mode = self.config.security.plugin_verification
        registry = PluginRuntime(self.config, self.plugin_root, mode=mode)
        registry.register_builtin(GenericHtmlPlugin)
        registry.prepare()
        return registry
