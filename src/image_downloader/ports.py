"""Public local plugin API v3 protocols and immutable capability contexts."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, cast, overload, runtime_checkable
from urllib.parse import urlsplit

from .configuration.endpoint_origins import OriginPattern
from .exceptions import AuthenticationError, UnsupportedSiteFeatureError
from .immutable import freeze_json
from .models import (
    AdditionalFileHookContext,
    AdditionalFileHookPoint,
    AdditionalFileReceiveResult,
    AdditionalFileSaveResult,
    AdditionalFileSpec,
    AuthDecision,
    AuthLease,
    AuthSnapshot,
    AuthStatus,
    AuthVerification,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageFetchRequest,
    ImageResource,
    ImageTransportMetadata,
    PluginDataView,
    RequestResponse,
    RequestSpec,
    UpdateSnapshot,
    freeze_mapping,
)


@runtime_checkable
class RequestPort(Protocol):
    async def execute(self, spec: RequestSpec) -> RequestResponse: ...


@runtime_checkable
class AuthPort(Protocol):
    async def ensure_ready(self, *, check: bool = False) -> None: ...
    def invalidate(self, response: RequestResponse) -> bool: ...
    def snapshot(self) -> AuthSnapshot: ...


@dataclass(frozen=True, slots=True, init=False)
class Endpoint:
    origins: tuple[str, ...]

    def __init__(self, origin: str | tuple[str, ...]) -> None:
        values = (origin,) if isinstance(origin, str) else origin
        if not values:
            raise ValueError("endpoint requires at least one origin")
        object.__setattr__(self, "origins", tuple(dict.fromkeys(OriginPattern.parse(item).origin for item in values)))

    @property
    def origin(self) -> str:
        if len(self.origins) != 1 or "*" in self.origins[0]:
            raise ValueError("endpoint has multiple or wildcard origins; select a concrete origin with for_origin()")
        return self.origins[0]

    def for_origin(self, origin: str) -> Endpoint:
        target = OriginPattern.parse(origin, allow_wildcard=False)
        if not any(OriginPattern.parse(item).matches(target) for item in self.origins):
            raise ValueError("origin does not match this endpoint")
        return Endpoint(target.origin)

    def request(self, path: str, **kwargs: Any) -> RequestSpec:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise ValueError("endpoint request path must start with a single slash and stay on the declared origin")
        return RequestSpec(self.origin + path, **kwargs)


@dataclass(frozen=True, slots=True)
class AuthContext:
    config: Mapping[str, object]
    secrets: SecretProvider
    endpoints: Mapping[str, Endpoint]
    requests: RequestPort

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", freeze_json(self.config))
        object.__setattr__(self, "endpoints", freeze_mapping(self.endpoints))


@runtime_checkable
class SecretProvider(Protocol):
    def get(self, name: str) -> str: ...


@runtime_checkable
class CookieProvider(Protocol):
    def get_cookie_snapshot(self) -> dict[str, dict[str, dict[str, str]]]:
        """Return a detached snapshot of non-expired cookies by domain/path/name."""
        ...


class PluginExecutionContext:
    """Capability-reduced v3 context supplied to a selected site plugin."""

    __slots__ = (
        "_config",
        "_app_settings",
        "_manifest",
        "_catalog",
        "_secrets",
        "_requests",
        "_cookie_provider",
        "_auth",
        "_endpoints",
        "_plugin_data",
    )

    def __init__(
        self,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
        manifest: Mapping[str, object],
        catalog: Mapping[str, object] | None,
        secrets: SecretProvider,
        requests: RequestPort,
        *,
        cookie_provider: CookieProvider | None = None,
        auth: AuthPort | None = None,
        endpoints: Mapping[str, Endpoint] | None = None,
        plugin_data: PluginDataView | None = None,
    ) -> None:
        self._config = _readonly_mapping(config)
        self._app_settings = _readonly_mapping(app_settings)
        self._manifest = _readonly_mapping(manifest)
        self._catalog = _readonly_mapping(catalog) if catalog is not None else None
        self._secrets = secrets
        self._requests = requests
        self._cookie_provider = cookie_provider
        self._auth = auth
        self._endpoints = freeze_mapping(endpoints)
        if plugin_data is not None and not isinstance(plugin_data, PluginDataView):
            raise TypeError("plugin_data must be PluginDataView or None")
        self._plugin_data = plugin_data if plugin_data is not None else PluginDataView()

    def _with_plugin_data(self, view: PluginDataView) -> PluginExecutionContext:
        context = copy(self)
        context._plugin_data = view
        return context

    @property
    def plugin_data(self) -> PluginDataView:
        return self._plugin_data

    @property
    def plugin_manifest(self) -> Mapping[str, object]:
        return self._manifest

    @overload
    def get_cookies(self, *, domain: None = None, path: None = None) -> dict[str, dict[str, dict[str, str]]]: ...

    @overload
    def get_cookies(self, *, domain: str, path: None = None) -> dict[str, dict[str, str]]: ...

    @overload
    def get_cookies(self, *, domain: None = None, path: str) -> dict[str, dict[str, str]]: ...

    @overload
    def get_cookies(self, *, domain: str, path: str) -> dict[str, str]: ...

    @overload
    def get_cookies(
        self, *, domain: str | None = None, path: str | None = None
    ) -> dict[str, dict[str, dict[str, str]]] | dict[str, dict[str, str]] | dict[str, str]: ...

    def get_cookies(
        self, *, domain: str | None = None, path: str | None = None
    ) -> dict[str, dict[str, dict[str, str]]] | dict[str, dict[str, str]] | dict[str, str]:
        """Read current cookies using exact stored domain/path matches.

        Omitted dimensions remain dictionary levels; specified dimensions are
        removed. Values are detached from the shared jar, including all nested
        dictionaries. No network request or persistence is performed.
        """
        if domain is not None and not isinstance(domain, str):
            raise TypeError("domain must be a string or None")
        if path is not None and not isinstance(path, str):
            raise TypeError("path must be a string or None")
        if self._cookie_provider is None:
            raise UnsupportedSiteFeatureError("cookie access is unavailable in this context")
        snapshot = self._cookie_provider.get_cookie_snapshot()
        if domain is not None:
            paths = snapshot.get(domain, {})
            if path is not None:
                return dict(paths.get(path, {}))
            return {key: dict(values) for key, values in paths.items()}
        if path is not None:
            return {key: dict(paths[path]) for key, paths in snapshot.items() if path in paths}
        return {key: {p: dict(values) for p, values in paths.items()} for key, paths in snapshot.items()}

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

    @property
    def auth(self) -> AuthPort:
        if self._auth is None:
            raise UnsupportedSiteFeatureError("authentication control is unavailable in this context")
        return self._auth

    @property
    def endpoints(self) -> Mapping[str, Endpoint]:
        return self._endpoints


class PluginFormatContext:
    """Stable, capability-reduced context for output-format value providers."""

    __slots__ = (
        "_plugin_id",
        "_plugin_kind",
        "_operation_url",
        "_config",
        "_app_settings",
        "_manifest",
        "_catalog",
        "_initialized",
    )

    def __init__(
        self,
        plugin_id: str,
        plugin_kind: str,
        operation_url: str,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
        manifest: Mapping[str, object],
        catalog: Mapping[str, object] | None,
    ) -> None:
        if not all(isinstance(value, str) for value in (plugin_id, plugin_kind, operation_url)):
            raise TypeError("plugin_id, plugin_kind, and operation_url must be strings")
        self._plugin_id = plugin_id
        self._plugin_kind = plugin_kind
        self._operation_url = operation_url
        self._config = _readonly_mapping(config)
        self._app_settings = _readonly_mapping(app_settings)
        self._manifest = _readonly_mapping(manifest)
        self._catalog = _readonly_mapping(catalog) if catalog is not None else None
        self._initialized = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_initialized", False):
            raise AttributeError("PluginFormatContext is immutable")
        object.__setattr__(self, name, value)

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def plugin_kind(self) -> str:
        return self._plugin_kind

    @property
    def operation_url(self) -> str:
        return self._operation_url

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


class TransformContext:
    """Processor/site transform metadata without network or secret capability."""

    __slots__ = (
        "_image_id",
        "_index",
        "_image_metadata",
        "_transport_metadata",
        "_config",
        "_app_settings",
        "_plugin_manifest",
        "_catalog",
        "_site_manifest",
        "_site_catalog",
        "_manifest",
        "_chapter",
        "_plugin_data",
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
        image_metadata: Mapping[str, str] | None = None,
        transport_metadata: ImageTransportMetadata | None = None,
    ) -> None:
        self._image_id = image.image_id
        self._index = image.index
        self._image_metadata = _readonly_mapping(image.metadata if image_metadata is None else image_metadata)
        if transport_metadata is not None and not isinstance(transport_metadata, ImageTransportMetadata):
            raise TypeError("transport_metadata must be ImageTransportMetadata or None")
        self._transport_metadata = transport_metadata
        self._config = _readonly_mapping(config)
        self._app_settings = _readonly_mapping(app_settings)
        self._plugin_manifest = _readonly_mapping(plugin_manifest)
        self._catalog = _readonly_mapping(catalog) if catalog is not None else None
        self._site_manifest = _readonly_mapping(site_manifest) if site_manifest is not None else None
        self._site_catalog = _readonly_mapping(site_catalog) if site_catalog is not None else None
        self._manifest = manifest
        self._chapter = chapter
        self._plugin_data = PluginDataView.from_resources(manifest, chapter, image)

    @property
    def plugin_data(self) -> PluginDataView:
        return self._plugin_data

    @property
    def image_id(self) -> str | None:
        return self._image_id

    @property
    def index(self) -> int:
        return self._index

    @property
    def image_metadata(self) -> Mapping[str, str]:
        """Non-secret per-image metadata supplied by ``inspect()``."""
        return cast(Mapping[str, str], self._image_metadata)

    @property
    def transport_metadata(self) -> ImageTransportMetadata | None:
        """Image-fetch data, raw or redacted according to the transform recipient."""
        return self._transport_metadata

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
    async def initialize(self, auth_context: AuthContext) -> AuthLease: ...
    def apply(self, request: RequestSpec) -> RequestSpec: ...
    def classify_response(self, request: RequestSpec, response: RequestResponse) -> AuthDecision: ...
    async def verify(self, auth_context: AuthContext) -> AuthVerification: ...
    async def refresh(self, auth_context: AuthContext) -> AuthLease: ...


class BaseAuthFlow(ABC):
    """Defaults for API 3 authentication; initialize must establish usable credentials."""

    @abstractmethod
    async def initialize(self, auth_context: AuthContext) -> AuthLease:
        raise NotImplementedError

    def apply(self, request: RequestSpec) -> RequestSpec:
        return request

    def classify_response(self, request: RequestSpec, response: RequestResponse) -> AuthDecision:
        if response.status == 401:
            return AuthDecision.AUTH_FAILURE
        if response.status == 403:
            return AuthDecision.UNKNOWN
        return AuthDecision.NOT_AUTH_FAILURE

    async def verify(self, auth_context: AuthContext) -> AuthVerification:
        return AuthVerification(AuthStatus.UNKNOWN)

    async def refresh(self, auth_context: AuthContext) -> AuthLease:
        raise AuthenticationError("authentication refresh is not supported")


@runtime_checkable
class OutputFormatValueProvider(Protocol):
    """Optional provider of stable, non-secret output-format values."""

    def output_format_values(self, context: PluginFormatContext) -> Mapping[str, str]: ...


@runtime_checkable
class SitePlugin(Protocol):
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None: ...

    def matches(self, url: str) -> bool: ...
    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest: ...
    async def create_image_request(
        self, image: ImageResource, context: PluginExecutionContext
    ) -> RequestSpec | ImageFetchRequest: ...
    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | ImageFetchRequest | None: ...
    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None: ...
    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact: ...


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
class AdditionalFileProvider(Protocol):
    """Optional pair; callbacks are independently optional."""

    def additional_file_hook_points(self, context: PluginExecutionContext) -> tuple[AdditionalFileHookPoint, ...]: ...

    async def additional_files(
        self, hook: AdditionalFileHookContext, context: PluginExecutionContext
    ) -> tuple[AdditionalFileSpec, ...]: ...


@runtime_checkable
class AdditionalFileReceiver(Protocol):
    async def additional_file_received(
        self, result: AdditionalFileReceiveResult, context: PluginExecutionContext
    ) -> None: ...


@runtime_checkable
class AdditionalFileSaveObserver(Protocol):
    async def additional_file_saved(
        self, result: AdditionalFileSaveResult, context: PluginExecutionContext
    ) -> None: ...


@runtime_checkable
class ImageProcessor(Protocol):
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None: ...

    def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact: ...


def is_auth_flow(value: object) -> TypeGuard[AuthFlow]:
    return (
        isinstance(value, AuthFlow)
        and not hasattr(value, "is_auth_failure")
        and not hasattr(value, "allowed_origins")
        and not inspect.iscoroutinefunction(value.apply)
        and not inspect.iscoroutinefunction(value.classify_response)
        and all(inspect.iscoroutinefunction(getattr(value, name)) for name in ("initialize", "verify", "refresh"))
        and len(inspect.signature(value.refresh).parameters) == 1
    )


def is_site_plugin(value: object) -> TypeGuard[SitePlugin]:
    return isinstance(value, SitePlugin)


def is_image_processor(value: object) -> TypeGuard[ImageProcessor]:
    return isinstance(value, ImageProcessor)
