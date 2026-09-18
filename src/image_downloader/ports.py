"""Public local plugin API v3 protocols and immutable capability contexts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Protocol, TypeGuard, cast, runtime_checkable

from .immutable import freeze_json
from .models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    RequestResponse,
    RequestSpec,
    UpdateSnapshot,
)


@runtime_checkable
class RequestPort(Protocol):
    async def execute(self, spec: RequestSpec) -> RequestResponse: ...


@runtime_checkable
class SecretProvider(Protocol):
    def get(self, name: str) -> str: ...


class PluginExecutionContext:
    """Capability-reduced v3 context supplied to a selected site plugin."""

    __slots__ = ("_config", "_app_settings", "_manifest", "_catalog", "_secrets", "_requests")

    def __init__(
        self,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
        manifest: Mapping[str, object],
        catalog: Mapping[str, object] | None,
        secrets: SecretProvider,
        requests: RequestPort,
    ) -> None:
        self._config = _readonly_mapping(config)
        self._app_settings = _readonly_mapping(app_settings)
        self._manifest = _readonly_mapping(manifest)
        self._catalog = _readonly_mapping(catalog) if catalog is not None else None
        self._secrets = secrets
        self._requests = requests

    @property
    def config(self) -> Mapping[str, object]:
        return self._config

    @property
    def app_settings(self) -> Mapping[str, object]:
        return self._app_settings

    @property
    def manifest(self) -> Mapping[str, object]:
        return self._manifest

    @property
    def catalog(self) -> Mapping[str, object] | None:
        return self._catalog

    @property
    def secrets(self) -> SecretProvider:
        return self._secrets

    @property
    def requests(self) -> RequestPort:
        return self._requests


class TransformContext:
    """Processor/site transform metadata without network or secret capability."""

    __slots__ = (
        "_image_id",
        "_index",
        "_config",
        "_app_settings",
        "_plugin_manifest",
        "_catalog",
        "_site_manifest",
        "_site_catalog",
        "_manifest",
        "_chapter",
    )

    def __init__(
        self,
        image: ImageResource,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
        plugin_manifest: Mapping[str, object],
        catalog: Mapping[str, object] | None,
        manifest: DownloadManifest,
        chapter: Chapter,
        *,
        site_manifest: Mapping[str, object] | None = None,
        site_catalog: Mapping[str, object] | None = None,
    ) -> None:
        self._image_id = image.image_id
        self._index = image.index
        self._config = _readonly_mapping(config)
        self._app_settings = _readonly_mapping(app_settings)
        self._plugin_manifest = _readonly_mapping(plugin_manifest)
        self._catalog = _readonly_mapping(catalog) if catalog is not None else None
        self._site_manifest = _readonly_mapping(site_manifest) if site_manifest is not None else None
        self._site_catalog = _readonly_mapping(site_catalog) if site_catalog is not None else None
        self._manifest = manifest
        self._chapter = chapter

    @property
    def image_id(self) -> str | None:
        return self._image_id

    @property
    def index(self) -> int:
        return self._index

    @property
    def config(self) -> Mapping[str, object]:
        return self._config

    @property
    def app_settings(self) -> Mapping[str, object]:
        return self._app_settings

    @property
    def plugin_manifest(self) -> Mapping[str, object]:
        return self._plugin_manifest

    @property
    def catalog(self) -> Mapping[str, object] | None:
        return self._catalog

    @property
    def site_manifest(self) -> Mapping[str, object] | None:
        return self._site_manifest

    @property
    def site_catalog(self) -> Mapping[str, object] | None:
        return self._site_catalog

    @property
    def manifest(self) -> DownloadManifest:
        return self._manifest

    @property
    def chapter(self) -> Chapter:
        return self._chapter


def _readonly_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], freeze_json(value))


@runtime_checkable
class AuthFlow(Protocol):
    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool: ...
    async def apply(self, request: RequestSpec) -> RequestSpec: ...
    async def refresh(self, failed: RequestSpec, response: RequestResponse) -> RequestSpec | None: ...


@runtime_checkable
class OriginScopedAuthFlow(AuthFlow, Protocol):
    """Optional AuthFlow extension permitting credentials on additional origins."""

    allowed_origins: Collection[str]


@runtime_checkable
class SitePlugin(Protocol):
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None: ...

    def matches(self, url: str) -> bool: ...
    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest: ...
    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec: ...
    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None: ...
    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None: ...
    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact: ...


@runtime_checkable
class ConfigurableSitePlugin(Protocol):
    """Optional site-selection extension for host-specific private configuration.

    The hook runs before ``validate_config`` and must therefore be synchronous,
    side-effect free, and tolerant of an invalid private configuration.  It has
    no access to secrets, filesystem, or request capabilities.
    """

    def matches_with_config(
        self, url: str, config: Mapping[str, object], app_settings: Mapping[str, object]
    ) -> bool: ...


@runtime_checkable
class UpdateProvider(Protocol):
    async def check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot: ...


@runtime_checkable
class ImageProcessor(Protocol):
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None: ...

    async def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact: ...


def is_auth_flow(value: object) -> TypeGuard[AuthFlow]:
    return isinstance(value, AuthFlow)


def is_origin_scoped_auth_flow(value: AuthFlow) -> TypeGuard[OriginScopedAuthFlow]:
    return isinstance(value, OriginScopedAuthFlow)


def is_site_plugin(value: object) -> TypeGuard[SitePlugin]:
    return isinstance(value, SitePlugin)


def is_image_processor(value: object) -> TypeGuard[ImageProcessor]:
    return isinstance(value, ImageProcessor)
