"""Pure formatting of sanitized notification payloads."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from ..configuration.models import NotificationCategory
from .events import EventPayload

_IMAGE_NOTIFICATION_LABELS: Mapping[NotificationCategory, str] = {
    "fetch_error": "image_fetch_failed",
    "process_error": "image_processing_failed",
    "save_error": "image_save_failed",
}


_NOTIFICATION_EXAMPLE_LIMIT = 5


_NOTIFICATION_URL_LIMIT = 256


_NOTIFICATION_BODY_LIMIT = 4096


def _payload_sort_key(payload: EventPayload) -> tuple[int, int, str, str]:
    return (
        int(payload.chapter_id) if payload.chapter_id is not None else -1,
        payload.image_index if payload.image_index is not None else -1,
        payload.stage or "",
        payload.url or "",
    )


def _short_url(value: str) -> str:
    return value if len(value) <= _NOTIFICATION_URL_LIMIT else value[:_NOTIFICATION_URL_LIMIT] + "…"


def _render_image_example(position: int, payload: EventPayload) -> list[str]:
    label = f"example {position}"
    if payload.chapter_id is not None:
        label += f": chapter={payload.chapter_id}"
    if payload.image_index is not None:
        label += f" image={payload.image_index}"
    lines = [label]
    if payload.url is not None:
        lines.append("image_url: " + _short_url(payload.url))
    lines.append("stage: " + (payload.stage or "image_processing"))
    if payload.response_url is not None:
        lines.append("response_url: " + _short_url(payload.response_url))
    if payload.http_status is not None:
        lines.append(f"http_status: {payload.http_status}")
    transport = payload.transport
    if transport is None and payload.stage in {"image_processing", "image_save"} and payload.http_status is not None:
        transport = "completed"
    if transport is not None:
        lines.append("transport: " + transport)
    if payload.path is not None:
        lines.append("output_path: " + payload.path)
    if payload.error_code is not None:
        lines.append("reason_code: " + payload.error_code)
    if payload.error_reason is not None:
        lines.append("reason: " + payload.error_reason)
    if payload.error_class is not None:
        lines.append("exception: " + payload.error_class)
    return lines


def render_notification(
    grouped: Mapping[NotificationCategory, list[EventPayload]],
    *,
    safe_source_url: str,
    has_log_path: bool = False,
) -> tuple[str, str]:
    title = "Image downloader notification"
    lines = [f"URL: {safe_source_url}"]
    examples: list[EventPayload] = []
    for category in sorted(grouped):
        payloads = sorted(grouped[category], key=_payload_sort_key)
        label = _IMAGE_NOTIFICATION_LABELS.get(category, category)
        lines.append(f"{label}: count={len(payloads)}")
        if category in _IMAGE_NOTIFICATION_LABELS:
            reason_counts = Counter(payload.error_code or "unknown_image_failure" for payload in payloads)
            for code, count in sorted(reason_counts.items()):
                lines.append(f"reason_code: {code} count={count}")
            examples.extend(payload for payload in payloads if payload.stage is not None)
        else:
            reason_counts = Counter(payload.error_code or "unknown_error" for payload in payloads)
            for code, count in sorted(reason_counts.items()):
                lines.append(f"reason_code: {code} count={count}")
            first = payloads[0]
            if first.operation is not None:
                lines.append("operation: " + first.operation)
            if first.error_reason is not None:
                lines.append("reason: " + first.error_reason)
            error_class = first.error_class
            if error_class:
                lines.append(f"exception: {error_class}")
    selected_examples = sorted(examples, key=_payload_sort_key)[:_NOTIFICATION_EXAMPLE_LIMIT]
    for position, payload in enumerate(selected_examples, start=1):
        lines.extend(_render_image_example(position, payload))
    if has_log_path:
        lines.append("Log: [REDACTED]")
    detail = "\n".join(lines)
    if len(detail) > _NOTIFICATION_BODY_LIMIT:
        detail = detail[: _NOTIFICATION_BODY_LIMIT - 1] + "…"
    return title, detail
