"""Manifest inspection using a detached request session."""

from __future__ import annotations

import asyncio

from ..configuration.models import AppConfig
from ..exceptions import (
    error_info_for,
)
from ..models import (
    ImageFetchRequest,
    ImageRequestResolution,
    ImageRequestResolutionFailure,
    ImageRequestResolutionStatus,
    ManifestInspectionResult,
)
from ..storage.cookies import CookieStore
from ..transport.gateway import RequestGateway, closing_gateway
from .requests import InspectRequest
from .site_operation import SiteOperationFactory


class InspectionExecutor:
    def __init__(
        self, config: AppConfig, cookie_store: CookieStore, gateway: RequestGateway, operations: SiteOperationFactory
    ) -> None:
        self.config, self.cookie_store, self.gateway, self.operations = config, cookie_store, gateway, operations

    async def execute(self, request: InspectRequest) -> ManifestInspectionResult:
        temporary_gateway = RequestGateway(
            self.config,
            self.cookie_store.clone_jar(self.gateway.client.cookies.jar),
        )
        async with closing_gateway(temporary_gateway):
            async with self.operations.open(
                request.url,
                request.plugin_overrides,
                request.fallback_override,
                request.plugin_id,
                request.force_plugin,
                request_gateway=temporary_gateway,
                diagnostics_enabled=False,
            ) as site:
                record, plugin, context = site.record, site.plugin, site.context
                operation_gateway, invoker = site.gateway, site.invoker
                manifest = self.operations.normalize_manifest(
                    await invoker.inspect(plugin, request.url, context), request.url
                )
                requests: list[ImageRequestResolution] = []
                if request.resolve_image_requests:
                    for chapter_position, chapter in enumerate(manifest.chapters, start=1):
                        for image_position, image in enumerate(chapter.images, start=1):
                            try:
                                value = await invoker.create_image_request(plugin, image, context)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                info = error_info_for(exc)
                                requests.append(
                                    ImageRequestResolution(
                                        chapter_position,
                                        image_position,
                                        ImageRequestResolutionStatus.FAILED,
                                        failure=ImageRequestResolutionFailure(
                                            info.code,
                                            info.reason,
                                            info.exception,
                                            "create_image_request",
                                        ),
                                    )
                                )
                            else:
                                image_request = value.request if isinstance(value, ImageFetchRequest) else value
                                plugin_data = value.plugin_data if isinstance(value, ImageFetchRequest) else {}
                                try:
                                    effective_request = await operation_gateway.preview(image_request)
                                except asyncio.CancelledError:
                                    raise
                                except Exception as exc:
                                    info = error_info_for(exc)
                                    requests.append(
                                        ImageRequestResolution(
                                            chapter_position,
                                            image_position,
                                            ImageRequestResolutionStatus.PARTIALLY_RESOLVED,
                                            image_request,
                                            plugin_data,
                                            isinstance(value, ImageFetchRequest),
                                            ImageRequestResolutionFailure(
                                                info.code,
                                                info.reason,
                                                info.exception,
                                                "effective_request",
                                            ),
                                        )
                                    )
                                else:
                                    requests.append(
                                        ImageRequestResolution(
                                            chapter_position,
                                            image_position,
                                            ImageRequestResolutionStatus.RESOLVED,
                                            image_request,
                                            plugin_data,
                                            isinstance(value, ImageFetchRequest),
                                            effective_request=effective_request,
                                        )
                                    )
                return ManifestInspectionResult(
                    request.url,
                    record.id,
                    manifest,
                    request.resolve_image_requests,
                    tuple(requests),
                )
