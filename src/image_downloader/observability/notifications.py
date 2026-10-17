from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import contextmanager
from functools import partial

from image_downloader.observability.notification_delivery import (
    EmailNotificationSender,
    NotificationSender,
    _smtp_diagnostic_host,
    _smtp_refusal_diagnostic,
)

from ..configuration.models import Notification, NotificationCategory, NotificationMethod
from ..models import WorkflowItemResult
from .events import EventBus, EventName, EventPayload
from .logging import DownloadLogger, safe_exception_name
from .notification_content import render_notification

NOTIFICATION_SOURCES: Mapping[EventName, NotificationCategory] = {
    EventName.FETCH_FAILED: "fetch_error",
    EventName.REQUEST_FAILED: "fetch_error",
    EventName.SAVE_FAILED: "save_error",
    EventName.AUTH_FAILED: "auth_error",
    EventName.PARSE_FAILED: "parse_error",
    EventName.IMAGE_PROCESS_FAILED: "process_error",
    EventName.PLUGIN_FAILED: "plugin_error",
    EventName.CONFIG_FAILED: "config_error",
    EventName.STORAGE_FAILED: "storage_error",
    EventName.RUNTIME_FAILED: "runtime_error",
    EventName.UPDATE_FAILED: "update_error",
    EventName.COOKIE_STORE_ACCESS: "auth_cookie_store_access",
    EventName.CREDENTIAL_STORE_ACCESS: "auth_credential_store_access",
    EventName.LOGIN_SUCCESS: "auth_login_success",
    EventName.SESSION_REFRESH_SUCCESS: "auth_session_refresh_success",
    EventName.DOWNLOAD_SUCCESS: "download_success",
    EventName.DOWNLOAD_PARTIAL_SUCCESS: "download_partial_success",
}


class NotificationService:
    """Collect configured events and deliver one summary after an operation."""

    def __init__(
        self,
        config: Notification,
        logger: DownloadLogger,
        events: EventBus | None,
        senders: Mapping[NotificationMethod, NotificationSender],
    ) -> None:
        self.config = config
        self.logger = logger
        self.events = events
        self.senders = senders
        self._records: list[tuple[NotificationCategory, EventPayload]] = []
        self._defer_download = False
        if events is not None:
            for source in NOTIFICATION_SOURCES:
                events.on(source, partial(self._collect, source))

    def _collect(self, source: EventName, payload: EventPayload) -> None:
        category = NOTIFICATION_SOURCES[source]
        if self._defer_download and category not in {
            "auth_cookie_store_access",
            "auth_credential_store_access",
            "auth_login_success",
            "auth_session_refresh_success",
        }:
            return
        if not self.config.enabled or category not in self.config.notify_on:
            return
        self._records.append((category, payload))

    @contextmanager
    def defer_download(self):
        """Keep lifecycle events observable while deferring attempt notifications."""
        previous = self._defer_download
        self._defer_download = True
        try:
            yield
        finally:
            self._defer_download = previous

    def _collect_final(self, source: EventName, payload: EventPayload) -> None:
        sanitizer = self.events if self.events is not None else EventBus()
        payload = sanitizer._sanitize(
            payload,
            url_is_locator=source
            in {
                EventName.FETCH_FAILED,
                EventName.SAVE_FAILED,
                EventName.IMAGE_PROCESS_FAILED,
            },
        )
        self._collect(source, payload)

    async def workflow_item(self, item: WorkflowItemResult) -> None:
        """Notify the final aggregate without replaying events to other observers."""
        if item.status == "removed":
            return
        failure_codes = {failure.code for failure in item.download.failures} if item.download else set()
        if item.error is not None and item.error.code not in failure_codes:
            info = item.error
            source = {
                "authentication_error": EventName.AUTH_FAILED,
                "secret_not_found": EventName.AUTH_FAILED,
                "plugin_error": EventName.PLUGIN_FAILED,
                "unsupported_site_feature": EventName.PLUGIN_FAILED,
                "configuration_error": EventName.CONFIG_FAILED,
                "storage_error": EventName.STORAGE_FAILED,
                "storage_safety_error": EventName.STORAGE_FAILED,
                "interprocess_lock_error": EventName.STORAGE_FAILED,
                "output_allocation_error": EventName.STORAGE_FAILED,
                "existing_file_conflict": EventName.SAVE_FAILED,
                "unexpected_runtime_error": EventName.RUNTIME_FAILED,
                "image_processing_error": EventName.IMAGE_PROCESS_FAILED,
                "image_decode_error": EventName.IMAGE_PROCESS_FAILED,
                "image_content_type_error": EventName.IMAGE_PROCESS_FAILED,
                "image_mime_mismatch": EventName.IMAGE_PROCESS_FAILED,
                "unsupported_image_format": EventName.IMAGE_PROCESS_FAILED,
                "image_dimension_limit_error": EventName.IMAGE_PROCESS_FAILED,
                "image_processor_closed": EventName.IMAGE_PROCESS_FAILED,
                "image_worker_error": EventName.IMAGE_PROCESS_FAILED,
            }.get(info.code, EventName.REQUEST_FAILED)
            self._collect_final(
                source,
                EventPayload(
                    url=item.url,
                    error_code=info.code,
                    error_reason=info.reason,
                    error_class=info.exception,
                    response_url=info.response_url,
                    http_status=info.http_status,
                    operation="workflow",
                ),
            )
        if item.download is not None:
            for chapter in item.download.chapters:
                for outcome in chapter.outcomes:
                    failure = outcome.failure
                    if failure is None:
                        continue
                    source = {
                        "fetch": EventName.FETCH_FAILED,
                        "process": EventName.IMAGE_PROCESS_FAILED,
                        "save": EventName.SAVE_FAILED,
                    }[failure.kind]
                    self._collect_final(
                        source,
                        EventPayload(
                            url=outcome.image.url,
                            response_url=failure.response_url,
                            path=outcome.path,
                            stage={"fetch": "image_fetch", "process": "image_processing", "save": "image_save"}[
                                failure.kind
                            ],
                            chapter_id=str(chapter.chapter.number),
                            image_index=outcome.image.index,
                            error_code=failure.code,
                            error_reason=failure.reason,
                            error_class=failure.exception_type,
                            http_status=failure.http_status,
                            transport=failure.transport,
                            operation="workflow",
                        ),
                    )
        if item.status == "success":
            self._collect_final(EventName.DOWNLOAD_SUCCESS, EventPayload(url=item.url))
        elif item.download is not None and (item.download.saved_files or item.download.skipped_files):
            self._collect_final(EventName.DOWNLOAD_PARTIAL_SUCCESS, EventPayload(url=item.url))
        await self.flush(source_url=item.url)

    async def flush(self, *, source_url: str, log_path: str | None = None) -> None:
        if not self._records:
            return
        grouped: dict[NotificationCategory, list[EventPayload]] = {}
        for category, payload in self._records:
            grouped.setdefault(category, []).append(payload)
        self._records.clear()
        title, detail = render_notification(
            grouped,
            safe_source_url=self.logger.safe_url(source_url),
            has_log_path=bool(log_path),
        )
        for method in self._methods_for(grouped):
            await self._deliver(method, title, detail)

    def _methods_for(
        self,
        grouped: Mapping[NotificationCategory, list[EventPayload]],
    ) -> list[NotificationMethod]:
        selected: list[NotificationMethod] = []
        for category in grouped:
            configured = self.config.routes.get(category, self.config.methods)
            for method in configured:
                if method not in selected:
                    selected.append(method)
        return selected

    async def _deliver(
        self,
        channel: NotificationMethod,
        title: str,
        message: str,
    ) -> bool:
        sender = self.senders.get(channel)
        smtp_host = "unknown"
        try:
            if sender is None:
                raise RuntimeError(f"notification sender is not configured: {channel}")
            if isinstance(sender, EmailNotificationSender):
                smtp_host = sender.config.smtp_host
            result = await sender.send(title, message)
        except Exception as exc:
            diagnostic = _smtp_refusal_diagnostic(exc)
            error_code = None
            if diagnostic is None:
                await self.logger.core("notification_failed", module="notification", error=exc)
            else:
                error_code, count, domains = diagnostic
                smtp_host = _smtp_diagnostic_host(smtp_host)
                await self.logger.log(
                    "event=notification_failed module=notification "
                    f"action={error_code} count={count} smtp_host={smtp_host} "
                    f"refused_domains={json.dumps(domains, separators=(',', ':'))} error={safe_exception_name(exc)}",
                    module="notification",
                )
            if self.events is not None:
                await self.events.emit(
                    EventName.NOTIFICATION_FAILED,
                    EventPayload(
                        channel=channel,
                        error=str(exc),
                        error_class=type(exc).__name__,
                        error_code=error_code,
                    ),
                )
            return False
        if result:
            await self.logger.core("notification_sent", module="notification")
            if self.events is not None:
                await self.events.emit(
                    EventName.NOTIFICATION_SENT,
                    EventPayload(channel=channel),
                )
            return True
        await self.logger.core("notification_failed", module="notification")
        if self.events is not None:
            await self.events.emit(EventName.NOTIFICATION_FAILED, EventPayload(channel=channel))
        return False
