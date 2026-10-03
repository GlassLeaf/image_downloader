"""Optional-file hook example; integrate into your packaged site plugin."""

from __future__ import annotations

import json

from image_downloader import (
    AdditionalFileHookContext,
    AdditionalFileHookPoint,
    AdditionalFileReceiveResult,
    AdditionalFileSaveResult,
    AdditionalFileSpec,
    ImageFetchRequest,
    ImageResource,
    PluginExecutionContext,
    RequestSpec,
)
from image_downloader.exceptions import AuthenticationError
from image_downloader.plugins.builtin import GenericHtmlPlugin


class GalleryPlugin(GenericHtmlPlugin):
    def __init__(self) -> None:
        self.keys: dict[int, str] = {}
        self.saved: dict[str, bytes] = {}

    def additional_file_hook_points(self, context: PluginExecutionContext) -> tuple[AdditionalFileHookPoint, ...]:
        return (AdditionalFileHookPoint.BEFORE_IMAGE_REQUEST, AdditionalFileHookPoint.AFTER_DOWNLOAD)

    async def additional_files(
        self, hook: AdditionalFileHookContext, context: PluginExecutionContext
    ) -> tuple[AdditionalFileSpec, ...]:
        if hook.point == AdditionalFileHookPoint.BEFORE_IMAGE_REQUEST:
            assert hook.image is not None
            return (
                AdditionalFileSpec(
                    "metadata",
                    f"metadata/{hook.image.index}.json",
                    request=RequestSpec(str(context.config["metadata_url"]), query={"image": hook.image.url}),
                ),
            )
        assert hook.chapter_result is not None
        data = json.dumps({"images": len(hook.chapter_result.outcomes)}).encode("utf-8")
        return (AdditionalFileSpec("summary", "metadata/images.json", data=data),)

    async def additional_file_received(
        self, result: AdditionalFileReceiveResult, context: PluginExecutionContext
    ) -> None:
        if result.file_id == "metadata" and result.data is not None and result.hook.image is not None:
            self.keys[id(result.hook.image)] = str(json.loads(result.data)["key"])

    async def additional_file_saved(self, result: AdditionalFileSaveResult, context: PluginExecutionContext) -> None:
        if result.path is not None and result.data is not None:
            self.saved[result.path] = result.data

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> ImageFetchRequest:
        key = self.keys.get(id(image))
        if key is None:
            raise AuthenticationError("required per-image key is unavailable")
        return ImageFetchRequest(RequestSpec(image.url, headers={"X-Image-Key": key}), {"key": key})

    def cleanup_after_use(self) -> None:
        self.keys.clear()
        self.saved.clear()
