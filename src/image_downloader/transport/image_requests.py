"""Resolve and recover plugin image requests while collecting transport evidence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from ..models import ImageFetchRequest, ImageResource, ImageTransportMetadata, RequestResponse, RequestSpec
from ..plugins.plugin_invoker import PluginInvoker
from ..ports import PluginExecutionContext, SitePlugin
from .metadata import ImageFetchResult, _response_header_values, _spec_metadata, _TransportTrace


class ImageRequestTransport(Protocol):
    async def __call__(
        self,
        spec: RequestSpec,
        recover: Callable[[RequestSpec, RequestResponse], Awaitable[RequestSpec | None]] | None = None,
        trace: _TransportTrace | None = None,
    ) -> RequestResponse: ...


async def fetch_image(
    invoker: PluginInvoker,
    execute: ImageRequestTransport,
    plugin: SitePlugin,
    image: ImageResource,
    context: PluginExecutionContext,
) -> ImageFetchResult:
    resolution = _image_fetch_request(await invoker.create_image_request(plugin, image, context))
    trace = _TransportTrace()

    async def recover(failed: RequestSpec, response: RequestResponse) -> RequestSpec | None:
        nonlocal resolution
        replacement = await invoker.recover_image_request(plugin, image, failed, response, context)
        if replacement is None:
            return None
        resolution = _image_fetch_request(replacement, previous_data=resolution.plugin_data)
        return resolution.request

    response = await execute(resolution.request, recover=recover, trace=trace)
    initial_request = trace.initial_request or _spec_metadata(resolution.request)
    final_request = trace.final_request or initial_request
    response_url = trace.response_url or response.url
    response_headers = trace.response_headers or _response_header_values(response.headers)
    return ImageFetchResult(
        response,
        ImageTransportMetadata(
            initial_request,
            final_request,
            response_url,
            response_headers,
            resolution.plugin_data,
        ),
    )


def _image_fetch_request(
    value: RequestSpec | ImageFetchRequest,
    *,
    previous_data: Mapping[str, str] | None = None,
) -> ImageFetchRequest:
    if isinstance(value, ImageFetchRequest):
        return value
    return ImageFetchRequest(value, previous_data or {})
