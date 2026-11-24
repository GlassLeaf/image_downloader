"""Pure validation of plugin results, retaining plugin and hook error identity."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import urlparse

from ..exceptions import PluginError
from ..immutable import freeze_plugin_data
from ..models import (
    AuthLease,
    AuthMode,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageFetchRequest,
    ImageResource,
    ImageSaveOptions,
    RequestSpec,
    UpdateCandidate,
    UpdateSnapshot,
    _validate_publication_fields,
)

_HTTP_METHOD = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def plugin_error(plugin_id: str, hook: str, message: str) -> PluginError:
    return PluginError(f"{message} (plugin={plugin_id}, hook={hook})")


def validate_auth_data(plugin_id: str, hook: str, lease: AuthLease) -> None:
    if lease.public_data is not None:
        validate_plugin_data(plugin_id, hook, lease.public_data)


def validate_plugin_data(plugin_id: str, hook: str, data: Mapping[str, object]) -> None:
    try:
        freeze_plugin_data(data)
    except (TypeError, ValueError, RecursionError) as exc:
        raise plugin_error(plugin_id, hook, "invalid structured plugin data") from exc


def validate_manifest(plugin_id: str, hook: str, value: object) -> None:
    if not isinstance(value, DownloadManifest):
        raise plugin_error(plugin_id, hook, "inspect must return DownloadManifest")
    validate_publication(plugin_id, hook, value)
    validate_plugin_data(plugin_id, hook, value.plugin_data)
    if not isinstance(value.title, str):
        raise plugin_error(plugin_id, hook, "inspect returned an invalid title")
    optional_text = (value.content_id, value.author, value.access, value.revision)
    if any(item is not None and not isinstance(item, str) for item in optional_text):
        raise plugin_error(plugin_id, hook, "inspect returned invalid manifest metadata")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.metadata.items()):
        raise plugin_error(plugin_id, hook, "inspect returned invalid manifest metadata")
    if value.allow_empty_chapters is not None and not isinstance(value.allow_empty_chapters, bool):
        raise plugin_error(plugin_id, hook, "inspect returned invalid allow_empty_chapters")
    for chapter_index, chapter in enumerate(value.chapters):
        if not isinstance(chapter, Chapter):
            raise plugin_error(plugin_id, hook, f"inspect returned an invalid chapter at chapters[{chapter_index}]")
        validate_chapter(plugin_id, hook, chapter, chapter_index)


def validate_chapter(plugin_id: str, hook: str, chapter: Chapter, chapter_index: int) -> None:
    validate_publication(plugin_id, hook, chapter)
    validate_plugin_data(plugin_id, hook, chapter.plugin_data)
    if isinstance(chapter.number, bool) or not isinstance(chapter.number, int) or chapter.number < 0:
        raise plugin_error(plugin_id, hook, f"inspect returned an invalid chapter number at chapters[{chapter_index}]")
    if not isinstance(chapter.title, str) or not isinstance(chapter.subtitle, str):
        raise plugin_error(plugin_id, hook, f"inspect returned invalid chapter text at chapters[{chapter_index}]")
    if chapter.chapter_id is not None and not isinstance(chapter.chapter_id, str):
        raise plugin_error(plugin_id, hook, f"inspect returned an invalid chapter ID at chapters[{chapter_index}]")
    if chapter.allow_empty_images is not None and not isinstance(chapter.allow_empty_images, bool):
        raise plugin_error(plugin_id, hook, "inspect returned invalid allow_empty_images")
    for image_index, image in enumerate(chapter.images):
        if not isinstance(image, ImageResource):
            path = f"chapters[{chapter_index}].images[{image_index}]"
            raise plugin_error(plugin_id, hook, f"inspect returned an invalid image at {path}")
        validate_image(plugin_id, hook, image, chapter_index, image_index)


def validate_publication(plugin_id: str, hook: str, value: Chapter | DownloadManifest) -> None:
    try:
        _validate_publication_fields(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise plugin_error(plugin_id, hook, "inspect returned invalid publication fields") from exc


def validate_image(plugin_id: str, hook: str, image: ImageResource, chapter_index: int, image_index: int) -> None:
    validate_plugin_data(plugin_id, hook, image.plugin_data)
    path = f"chapters[{chapter_index}].images[{image_index}]"
    validate_locator(plugin_id, hook, image.url, f"inspect returned an invalid image locator at {path}")
    if isinstance(image.index, bool) or not isinstance(image.index, int) or image.index < 0:
        raise plugin_error(plugin_id, hook, f"inspect returned an invalid image index at {path}")
    if image.referer is not None:
        validate_http_url(plugin_id, hook, image.referer, f"inspect returned an invalid referer at {path}")
    validate_string_mapping(plugin_id, hook, image.headers, f"inspect returned invalid image headers at {path}")
    validate_string_mapping(plugin_id, hook, image.metadata, f"inspect returned invalid image metadata at {path}")
    if not isinstance(image.save_options, ImageSaveOptions):
        raise plugin_error(plugin_id, hook, f"inspect returned invalid save options at {path}")
    validate_save_options(plugin_id, hook, image.save_options, path)
    if image.image_id is not None and not isinstance(image.image_id, str):
        raise plugin_error(plugin_id, hook, f"inspect returned an invalid image ID at {path}")
    if image.original_filename is not None and (
        not isinstance(image.original_filename, str)
        or not image.original_filename.strip()
        or "/" in image.original_filename
        or "\\" in image.original_filename
    ):
        raise plugin_error(plugin_id, hook, f"inspect returned an invalid original filename at {path}")


def validate_update_snapshot(plugin_id: str, hook: str, value: object) -> None:
    if not isinstance(value, UpdateSnapshot):
        raise plugin_error(plugin_id, hook, "check_updates must return UpdateSnapshot")
    validate_http_url(plugin_id, hook, value.source_url, "check_updates returned an invalid source URL")
    if not isinstance(value.checked_at, datetime):
        raise plugin_error(plugin_id, hook, "check_updates returned an invalid checked_at value")
    for index, candidate in enumerate(value.candidates):
        if not isinstance(candidate, UpdateCandidate):
            raise plugin_error(plugin_id, hook, f"check_updates returned an invalid candidate at candidates[{index}]")
        validate_http_url(
            plugin_id,
            hook,
            candidate.url,
            f"check_updates returned an invalid candidate URL at candidates[{index}]",
        )
        if candidate.content_id is not None and not isinstance(candidate.content_id, str):
            raise plugin_error(plugin_id, hook, f"check_updates returned an invalid content ID at candidates[{index}]")
        if candidate.revision is not None and not isinstance(candidate.revision, str):
            raise plugin_error(plugin_id, hook, f"check_updates returned an invalid revision at candidates[{index}]")


def validate_request(plugin_id: str, hook: str, value: object, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, RequestSpec):
        expected = "RequestSpec or None" if allow_none else "RequestSpec"
        raise plugin_error(plugin_id, hook, f"{hook} must return {expected}")
    validate_http_url(plugin_id, hook, value.url, f"{hook} returned an invalid request URL")
    if not isinstance(value.method, str) or _HTTP_METHOD.fullmatch(value.method) is None:
        raise plugin_error(plugin_id, hook, f"{hook} returned an invalid HTTP method")
    validate_string_mapping(plugin_id, hook, value.headers, f"{hook} returned invalid request headers")
    validate_string_mapping(plugin_id, hook, value.cookies, f"{hook} returned invalid request cookies")
    validate_string_mapping(plugin_id, hook, value.query, f"{hook} returned invalid request query")
    validate_string_mapping(plugin_id, hook, value.form, f"{hook} returned invalid request form")
    if value.referer is not None:
        validate_http_url(plugin_id, hook, value.referer, f"{hook} returned an invalid referer")
    if not isinstance(value.auth, AuthMode) or not isinstance(value.retry_non_idempotent, bool):
        raise plugin_error(plugin_id, hook, f"{hook} returned invalid request flags")


def validate_image_fetch_request(plugin_id: str, hook: str, value: object, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, ImageFetchRequest):
        validate_request(plugin_id, hook, value.request, allow_none=False)
        validate_string_mapping(plugin_id, hook, value.plugin_data, f"{hook} returned invalid plugin data")
        return
    if isinstance(value, RequestSpec):
        validate_request(plugin_id, hook, value, allow_none=False)
        return
    expected = "RequestSpec, ImageFetchRequest, or None" if allow_none else "RequestSpec or ImageFetchRequest"
    raise plugin_error(plugin_id, hook, f"{hook} must return {expected}")


def validate_artifact(plugin_id: str, hook: str, value: object) -> None:
    if not isinstance(value, ImageArtifact):
        raise plugin_error(plugin_id, hook, f"{hook} must return ImageArtifact")
    if not isinstance(value.data, bytes) or not isinstance(value.content_type, str):
        raise plugin_error(plugin_id, hook, f"{hook} returned invalid image data")
    validate_locator(plugin_id, hook, value.source_url, f"{hook} returned an invalid source locator")
    if value.image_id is not None and not isinstance(value.image_id, str):
        raise plugin_error(plugin_id, hook, f"{hook} returned an invalid image ID")
    if value.extension is not None and not isinstance(value.extension, str):
        raise plugin_error(plugin_id, hook, f"{hook} returned an invalid extension")
    if any(not isinstance(item, str) for item in value.history):
        raise plugin_error(plugin_id, hook, f"{hook} returned an invalid history")


def validate_save_options(plugin_id: str, hook: str, value: ImageSaveOptions, path: str) -> None:
    if any(item is not None and not isinstance(item, str) for item in (value.format, value.extension)):
        raise plugin_error(plugin_id, hook, f"inspect returned invalid save options at {path}")
    if any(
        item is not None and (isinstance(item, bool) or not isinstance(item, int))
        for item in (value.quality, value.compress_level)
    ):
        raise plugin_error(plugin_id, hook, f"inspect returned invalid save options at {path}")
    if any(
        item is not None and not isinstance(item, bool) for item in (value.optimize, value.progressive, value.lossless)
    ) or not isinstance(value.exif, bool):
        raise plugin_error(plugin_id, hook, f"inspect returned invalid save options at {path}")
    if (
        value.format is not None
        and value.format.upper() == "ORIGINAL"
        and (
            value.extension is not None
            or any(
                item is not None
                for item in (
                    value.quality,
                    value.optimize,
                    value.progressive,
                    value.lossless,
                    value.compress_level,
                )
            )
            or value.exif
        )
    ):
        raise plugin_error(plugin_id, hook, f"inspect returned ORIGINAL save options with encoder fields at {path}")


def validate_http_url(plugin_id: str, hook: str, value: object, message: str) -> None:
    if not isinstance(value, str):
        raise plugin_error(plugin_id, hook, message)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise plugin_error(plugin_id, hook, message)


def validate_locator(plugin_id: str, hook: str, value: object, message: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise plugin_error(plugin_id, hook, message)


def validate_string_mapping(plugin_id: str, hook: str, value: object, message: str) -> None:
    if not isinstance(value, Mapping):
        raise plugin_error(plugin_id, hook, message)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise plugin_error(plugin_id, hook, message)
