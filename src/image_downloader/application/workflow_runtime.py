"""Explicit capabilities used by workflow execution and planning."""

from __future__ import annotations

from dataclasses import dataclass

from ..configuration.models import AppConfig
from ..observability.events import EventBus
from ..observability.logging import DownloadLogger
from ..observability.notifications import NotificationService
from ..storage.cookies import CookieStore
from ..storage.filesystem import FileSystem
from ..storage.state import UpdateState
from ..transport.gateway import RequestGateway
from .download_executor import DownloadExecutor
from .site_operation import SiteOperationFactory
from .update_executor import UpdateExecutor


@dataclass(frozen=True, slots=True)
class WorkflowRuntime:
    """Workflow capabilities, independent of the public service and its lock."""

    config: AppConfig
    operations: SiteOperationFactory
    download: DownloadExecutor
    updates: UpdateExecutor
    outputs: FileSystem
    state: UpdateState
    events: EventBus
    logger: DownloadLogger
    notifications: NotificationService
    cookie_store: CookieStore
    gateway: RequestGateway
