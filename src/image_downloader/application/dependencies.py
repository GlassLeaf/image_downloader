"""dependencies runtime responsibilities."""

from __future__ import annotations

from dataclasses import dataclass

from ..media.image_processor import ImageProcessor
from ..observability.events import EventBus
from ..observability.logging import (
    DownloadLogger,
)
from ..observability.notifications import (
    NotificationService,
)
from ..output.output_lock import OutputDirectoryLocks
from ..storage import FileSystem
from ..storage.cookies import CookieSnapshot, CookieStore
from ..storage.state import UpdateState
from ..transport.gateway import RequestGateway


@dataclass(frozen=True, slots=True)
class _RuntimeDependencies:
    """Service-owned infrastructure assembled by the composition root."""

    outputs: FileSystem
    logs: FileSystem
    state: UpdateState
    events: EventBus
    logger: DownloadLogger
    notifications: NotificationService
    cookie_store: CookieStore
    cookie_baseline: CookieSnapshot
    gateway: RequestGateway
    image_processor: ImageProcessor
    output_locks: OutputDirectoryLocks
