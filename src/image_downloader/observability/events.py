from __future__ import annotations

import inspect
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..exceptions import error_reason_for_code
from .logging import DownloadLogger, safe_exception_name, safe_relative_path, safe_url

_SAFE_FAILURE_STAGE = re.compile(r"image_(?:fetch|processing|save)")
_SAFE_FAILURE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_OPERATION = re.compile(r"(?:download|update|doctor|plugin|config|cookie)")
_SAFE_TRANSPORT = re.compile(
    r"(?:completed|response_received|response_limit_exceeded|redirect_rejected|failed)"
)


class EventName(StrEnum):
    BEFORE_AUTH = "on_before_auth"
    AUTH_FAILED = "on_auth_failed"
    AUTH_SUCCESS = "on_auth_success"
    AFTER_AUTH = "on_after_auth"
    COOKIE_STORE_ACCESS = "on_cookie_store_access"
    CREDENTIAL_STORE_ACCESS = "on_credential_store_access"
    LOGIN_SUCCESS = "on_login_success"
    SESSION_REFRESH_SUCCESS = "on_session_refresh_success"
    BEFORE_DOWNLOAD = "on_before_download"
    AFTER_DOWNLOAD = "on_after_download"
    DOWNLOAD_COMPLETE = "on_download_complete"
    DOWNLOAD_FAILED = "on_download_failed"
    DOWNLOAD_SUCCESS = "on_download_success"
    DOWNLOAD_PARTIAL_SUCCESS = "on_download_partial_success"
    BEFORE_FETCH = "on_before_fetch"
    FETCH_SUCCESS = "on_fetch_success"
    FETCH_FAILED = "on_fetch_failed"
    PARSE_STARTED = "on_parse_started"
    PARSE_SUCCESS = "on_parse_success"
    REQUEST_STARTED = "on_request_started"
    REQUEST_SUCCESS = "on_request_success"
    REQUEST_FAILED = "on_request_failed"
    IMAGE_PROCESS = "on_image_process"
    IMAGE_PROCESS_STARTED = "on_image_process_started"
    IMAGE_PROCESS_SUCCESS = "on_image_process_success"
    IMAGE_PROCESS_FAILED = "on_image_process_failed"
    BEFORE_SAVE = "on_before_save"
    SAVE_SUCCESS = "on_save_success"
    SAVE_FAILED = "on_save_failed"
    PARSE_FAILED = "on_parse_failed"
    PLUGIN_FAILED = "on_plugin_failed"
    CONFIG_FAILED = "on_config_failed"
    STORAGE_FAILED = "on_storage_failed"
    RUNTIME_FAILED = "on_runtime_failed"
    UPDATE_FAILED = "on_update_failed"
    UPDATE_CHECK_STARTED = "on_update_check_started"
    UPDATED_URL_FOUND = "on_updated_url_found"
    UPDATE_CHECK_FINISHED = "on_update_check_finished"
    NOTIFICATION_SENT = "on_notification_sent"
    NOTIFICATION_FAILED = "on_notification_failed"


EVENTS = tuple(event.value for event in EventName)


@dataclass(frozen=True, slots=True)
class EventPayload:
    url: str | None = None
    response_url: str | None = None
    path: str | None = None
    stage: str | None = None
    chapter_id: str | None = None
    image_index: int | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_reason: str | None = None
    error: str | None = None
    error_class: str | None = None
    operation: str | None = None
    transport: str | None = None
    channel: str | None = None


EventHandler = Callable[[EventPayload], object | Awaitable[object]]


class EventBus:
    def __init__(self, logger: DownloadLogger | None = None) -> None:
        self._handlers: dict[EventName, list[EventHandler]] = defaultdict(list)
        self._safe_query_parameters: set[str] = set()
        self._safe_fragment_parameters: set[str] = set()
        self._output_root: Path | None = None
        self._logger = logger

    def configure_safety(self, logging: dict[str, Any], output_root: str | None = None) -> None:
        self._safe_query_parameters = {str(value).lower() for value in logging.get("safe_query_parameters", [])}
        self._safe_fragment_parameters = {str(value).lower() for value in logging.get("safe_fragment_parameters", [])}
        self._output_root = Path(output_root) if output_root else None

    def on(self, event: EventName, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    async def emit(self, event: EventName, payload: EventPayload | None = None) -> None:
        safe = self._sanitize(payload or EventPayload())
        for handler in tuple(self._handlers.get(event, ())):
            try:
                result = handler(safe)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                await self._log_observer_failure(event, exc)

    def _sanitize(self, payload: EventPayload) -> EventPayload:
        return EventPayload(
            url=(
                safe_url(
                    payload.url,
                    safe_query_parameters=self._safe_query_parameters,
                    safe_fragment_parameters=self._safe_fragment_parameters,
                )
                if payload.url is not None
                else None
            ),
            response_url=(
                safe_url(
                    payload.response_url,
                    safe_query_parameters=self._safe_query_parameters,
                    safe_fragment_parameters=self._safe_fragment_parameters,
                )
                if payload.response_url is not None
                else None
            ),
            path=safe_relative_path(payload.path, self._output_root) if payload.path is not None else None,
            stage=payload.stage if payload.stage is not None and _SAFE_FAILURE_STAGE.fullmatch(payload.stage) else None,
            chapter_id=payload.chapter_id if payload.chapter_id is not None and payload.chapter_id.isdigit() else None,
            image_index=payload.image_index if payload.image_index is not None and payload.image_index >= 1 else None,
            http_status=(
                payload.http_status if payload.http_status is not None and 100 <= payload.http_status <= 599 else None
            ),
            error_code=(
                payload.error_code
                if payload.error_code is not None and _SAFE_FAILURE_CODE.fullmatch(payload.error_code)
                else None
            ),
            error_reason=error_reason_for_code(payload.error_code),
            error="[REDACTED]" if payload.error is not None else None,
            error_class=safe_exception_name(payload.error_class) if payload.error_class is not None else None,
            operation=(
                payload.operation
                if payload.operation is not None and _SAFE_OPERATION.fullmatch(payload.operation)
                else None
            ),
            transport=(
                payload.transport
                if payload.transport is not None and _SAFE_TRANSPORT.fullmatch(payload.transport)
                else None
            ),
            channel=payload.channel,
        )

    async def _log_observer_failure(self, event: EventName, error: Exception) -> None:
        if self._logger is None:
            return
        try:
            await self._logger.core(
                "event_observer_failed",
                module="runtime",
                action=event.value.removeprefix("on_"),
                error=error,
                debug=True,
            )
        except Exception:
            # Diagnostics must preserve the observer isolation guarantee.
            return
