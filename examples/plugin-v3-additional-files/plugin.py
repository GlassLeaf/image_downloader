"""Seven additional-file stages, using only the public v3 plugin API."""

from __future__ import annotations

import json

from image_downloader import (
    AdditionalFileHookContext,
    AdditionalFileHookPoint,
    AdditionalFileReceiveResult,
    AdditionalFileSaveResult,
    AdditionalFileSpec,
    DownloadManifest,
    ImageFetchRequest,
    ImageResource,
    PluginExecutionContext,
    RequestSpec,
)
from image_downloader.exceptions import AuthenticationError

from .base import BaseGallery, local_origin


class GalleryPlugin(BaseGallery):
    slug = "additional-files"

    def __init__(self) -> None:
        self.layout: str | None = None
        self.variants: dict[int, str] = {}
        self.saved: dict[str, tuple[str, bytes]] = {}

    def additional_file_hook_points(self, context: PluginExecutionContext) -> tuple[AdditionalFileHookPoint, ...]:
        return tuple(AdditionalFileHookPoint)

    async def additional_files(
        self, hook: AdditionalFileHookContext, context: PluginExecutionContext
    ) -> tuple[AdditionalFileSpec, ...]:
        point = hook.point
        origin = local_origin(context.config["origin"])
        if point == AdditionalFileHookPoint.BEFORE_MANIFEST:
            return (
                AdditionalFileSpec(
                    "bootstrap",
                    "metadata/bootstrap.json",
                    request=RequestSpec(f"{origin}/samples/{self.slug}/bootstrap"),
                ),
            )
        if point == AdditionalFileHookPoint.BEFORE_IMAGE_REQUEST:
            assert hook.image is not None
            return (
                AdditionalFileSpec(
                    "variant",
                    f"metadata/{hook.image.image_id}/variant.json",
                    request=RequestSpec(f"{origin}/samples/{self.slug}/metadata"),
                ),
            )
        if point == AdditionalFileHookPoint.AFTER_DOWNLOAD:
            assert hook.chapter_result is not None
            data = {
                "images": len(hook.chapter_result.outcomes),
                "saved_callbacks": sum(status == "saved" for status, _ in self.saved.values()),
                "skipped_callbacks": sum(status == "skipped" for status, _ in self.saved.values()),
            }
            return (AdditionalFileSpec("summary", "metadata/summary.json", data=json.dumps(data).encode()),)
        # Public facts only: never persist request headers, cookies or credentials.
        data: dict[str, object] = {"point": point.value}
        if hook.chapter is not None:
            data["chapter"] = hook.chapter.chapter_id
        if hook.response is not None:
            data["status"] = hook.response.status
        if hook.artifact is not None:
            data.update(content_type=hook.artifact.content_type, byte_count=len(hook.artifact.data))
        if hook.image_outcome is not None:
            data["outcome"] = hook.image_outcome.kind.value
        prefix = f"metadata/{hook.image.image_id}" if hook.image is not None else "metadata"
        return (AdditionalFileSpec(point.name, f"{prefix}/{point.value}.json", data=json.dumps(data).encode()),)

    async def additional_file_received(
        self, result: AdditionalFileReceiveResult, context: PluginExecutionContext
    ) -> None:
        if result.data is None:
            return
        if result.file_id == "bootstrap":
            value = json.loads(result.data).get("layout")
            if value == "standard":
                self.layout = value
        elif result.file_id == "variant" and result.hook.image is not None:
            value = json.loads(result.data).get("variant")
            if value == "full":
                self.variants[id(result.hook.image)] = value

    async def additional_file_saved(self, result: AdditionalFileSaveResult, context: PluginExecutionContext) -> None:
        if result.path is not None and result.data is not None:
            # On skip this contains the EXISTING file, not the just-received bytes.
            self.saved[result.path] = (result.status, result.data)

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        if self.layout is None:
            raise AuthenticationError("required supplemental layout is unavailable")
        return await super().inspect(url, context)

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> ImageFetchRequest:
        variant = self.variants.get(id(image))
        if variant is None:
            raise AuthenticationError("required per-image metadata is unavailable")
        return ImageFetchRequest(RequestSpec(image.url, headers={"X-Variant": variant}), {"variant": variant})

    def cleanup_after_use(self) -> None:
        self.layout = None
        self.variants.clear()
        self.saved.clear()
