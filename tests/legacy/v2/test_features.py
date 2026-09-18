from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from image_downloader.application.updates import UpdateState, filter_updated
from image_downloader.auth.tokens import Token, TokenProvider, token_from_headers, token_from_html
from image_downloader.config import DEFAULT_CONFIG, deep_merge, resolve_paths
from image_downloader.media.image_processor import ImageProcessor
from image_downloader.models import ImageSaveOptions, UpdatedUrl, UpdateResult
from image_downloader.storage import FileSystem


def test_profile_paths_and_traversal_guard(tmp_path: Path) -> None:
    paths = resolve_paths(DEFAULT_CONFIG, base_dir=tmp_path)
    assert paths["downloads"] == (tmp_path / "profiles" / "default" / "downloads").resolve()
    try:
        resolve_paths(deep_merge(DEFAULT_CONFIG, {"output": {"root": ".."}}), base_dir=tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal was accepted")


def test_token_refresh_is_shared_and_extractors_work() -> None:
    calls = 0

    async def refresh() -> Token:
        nonlocal calls
        calls += 1
        return Token("secret", datetime.now(UTC) + timedelta(minutes=5))

    async def scenario() -> None:
        provider = TokenProvider(refresh)
        assert await asyncio.gather(provider.get(), provider.get()) == ["secret", "secret"]

    asyncio.run(scenario())
    assert calls == 1
    assert token_from_html('<input name="csrf" value="abc">') == "abc"
    assert token_from_headers({"x-csrf-token": "def"}) == "def"


def test_update_state_returns_only_new_urls(tmp_path: Path) -> None:
    state = UpdateState(tmp_path / "state.json")
    first = UpdateResult("source", "sample", [UpdatedUrl("one")], datetime.now(UTC))
    assert len(filter_updated(first, state).updated_urls) == 1
    assert len(filter_updated(first, state).updated_urls) == 0


def test_update_state_can_use_root_confined_filesystem(tmp_path: Path) -> None:
    state = UpdateState(filesystem=FileSystem(tmp_path / "state"))
    first = UpdateResult("source", "sample", [UpdatedUrl("one")], datetime.now(UTC))
    assert len(filter_updated(first, state).updated_urls) == 1
    assert (tmp_path / "state" / "updates.json").exists()


def test_image_extension_follows_content_type_then_plugin_override() -> None:
    source = io.BytesIO()
    Image.new("RGB", (1, 1), "blue").save(source, format="PNG")
    data, extension = ImageProcessor().process(
        source.getvalue(), source_url="https://example.test/image", content_type="image/png"
    )
    assert extension == ".png"
    _, extension = ImageProcessor().process(
        source.getvalue(), source_url="https://example.test/misleading.jpg", content_type="image/jpeg"
    )
    assert extension == ".png"
    converted, extension = ImageProcessor().process(
        source.getvalue(),
        source_url="https://example.test/image.png",
        content_type="image/png",
        options=ImageSaveOptions(format="JPEG", quality=80),
    )
    assert extension == ".jpeg"
    assert converted.startswith(b"\xff\xd8")
