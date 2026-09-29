"""v3 sample: use non-secret image metadata in request and site transforms."""

from __future__ import annotations

import json
from collections.abc import Mapping
from io import BytesIO
from urllib.parse import urlparse

from PIL import Image

from image_downloader import (
    AuthFlow,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageFetchRequest,
    ImageResource,
    PluginError,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)

_ORIENTATION_TO_ROTATION = {"1": 0, "3": 180, "6": -90, "8": 90}
_VARIANTS = frozenset({"full", "preview"})


def _text(value: object, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise PluginError(message)
    return value


def _json_object(body: bytes, message: str) -> Mapping[str, object]:
    try:
        value = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError(message) from exc
    if not isinstance(value, Mapping):
        raise PluginError(message)
    return value


def _variant(value: object) -> str:
    if not isinstance(value, str) or value not in _VARIANTS:
        raise PluginError("metadata bridge image variant is invalid")
    return value


def _orientation(value: object) -> str:
    if not isinstance(value, str) or value not in _ORIENTATION_TO_ROTATION:
        raise PluginError("metadata bridge image orientation is invalid")
    return value


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PluginError("metadata bridge URL is invalid")
    return f"{parsed.scheme}://{parsed.netloc}"


def _work_id(url: str) -> str:
    work_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not work_id:
        raise PluginError("metadata bridge work ID is missing")
    return work_id


class MetadataBridgeGalleryPlugin:
    """Use inspection-time metadata without exposing any secret to a processor."""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("metadata bridge gallery does not accept configuration")

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "metadata-bridge.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        origin = _origin(url)
        payload = _json_object(
            (await context.requests.execute(RequestSpec(f"{origin}/api/works/{_work_id(url)}"))).body,
            "metadata bridge work response is invalid",
        )
        raw_images = payload.get("images")
        if not isinstance(raw_images, list):
            raise PluginError("metadata bridge images are missing")

        images: list[ImageResource] = []
        for index, raw_image in enumerate(raw_images, 1):
            if not isinstance(raw_image, Mapping):
                raise PluginError("metadata bridge image is invalid")
            image_id = _text(raw_image.get("id"), "metadata bridge image ID is missing")
            variant = _variant(raw_image.get("variant"))
            # The API provides this numeric value, so convert it to the string-only
            # ImageResource.metadata contract before publishing the manifest.
            orientation = _orientation(str(raw_image.get("orientation")))
            images.append(
                ImageResource(
                    f"{origin}/images/{image_id}",
                    index=index,
                    referer=url,
                    image_id=image_id,
                    metadata={"variant": variant, "orientation": orientation},
                )
            )

        title = _text(payload.get("title"), "metadata bridge title is missing")
        return DownloadManifest(
            title,
            (Chapter(1, title, images=tuple(images)),),
            content_id=_text(payload.get("id"), "metadata bridge work ID is missing"),
        )

    async def create_image_request(
        self, image: ImageResource, context: PluginExecutionContext
    ) -> ImageFetchRequest:
        del context
        image_id = _text(image.image_id, "metadata bridge image ID is missing")
        variant = _variant(image.metadata.get("variant"))
        orientation = _orientation(image.metadata.get("orientation"))
        origin = _origin(image.referer or image.url)
        return ImageFetchRequest(
            RequestSpec(
                f"{origin}/api/images/{image_id}/download",
                query={"variant": variant},
                referer=image.referer,
            ),
            # This is request-time transform data.  It is intentionally small and
            # non-secret; it reaches processors only through the explicit allow-list.
            {"variant": variant, "orientation": orientation},
        )

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> None:
        del image, failed, response, context
        return None

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        orientation = _orientation(context.image_metadata.get("orientation"))
        rotation = _ORIENTATION_TO_ROTATION[orientation]
        if rotation == 0:
            return ImageArtifact(
                artifact.data,
                artifact.content_type,
                artifact.source_url,
                image_id=artifact.image_id,
                extension=artifact.extension,
                history=(*artifact.history, "site-orientation-normalized"),
            )
        try:
            with Image.open(BytesIO(artifact.data)) as source:
                transformed = source.rotate(rotation, expand=True)
                destination = BytesIO()
                transformed.save(destination, format="PNG")
        except (OSError, ValueError) as exc:
            raise ValueError("metadata bridge could not rotate the downloaded image") from exc
        return ImageArtifact(
            destination.getvalue(),
            "image/png",
            artifact.source_url,
            image_id=artifact.image_id,
            extension="png",
            history=(*artifact.history, "site-orientation-normalized"),
        )
