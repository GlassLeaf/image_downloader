from __future__ import annotations

import asyncio

from image_downloader.commands.dispatch import _error_payload
from image_downloader.exceptions import RequestError
from image_downloader.observability.events import EventBus, EventName, EventPayload
from image_downloader.observability.logging import safe_locator


def test_safe_locator_preserves_non_url_identity_without_url_reconstruction() -> None:
    assert safe_locator("image:42") == "locator:image:42"
    assert safe_locator("/placeholder/42") == "locator:/placeholder/42"
    assert safe_locator("https://cdn.example.test/image?token=secret") == "https://cdn.example.test/image?token=[REDACTED]"


def test_image_events_and_cli_failures_render_locator_without_treating_it_as_http_url() -> None:
    received: list[EventPayload] = []

    async def scenario() -> None:
        bus = EventBus()
        bus.on(EventName.BEFORE_FETCH, received.append)
        await bus.emit(EventName.BEFORE_FETCH, EventPayload(url="image:42"))

    asyncio.run(scenario())
    assert received == [EventPayload(url="locator:image:42")]

    error = RequestError("failed")
    error._image_failure_context = {  # type: ignore[attr-defined]
        "image_url": "image:42",
        "response_url": "https://cdn.example.test/image?token=secret",
    }
    payload = _error_payload(error, operation="download")
    assert payload["image_url"] == "locator:image:42"
    assert payload["response_url"] == "https://cdn.example.test/image?token=[REDACTED]"
