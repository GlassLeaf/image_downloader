"""Development composition uses the application's real operation executors."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any, cast

import httpx

from ..application.dependencies import _RuntimeDependencies
from ..application.requests import DownloadRequest, InspectRequest
from ..application.service import DownloadService
from ..configuration.merging import deep_merge
from ..configuration.models import AppConfig
from ..configuration.paths import default_data_root, resolve_paths
from ..credentials.keyring import MasterKeyStore
from ..credentials.plugin_secrets import RuntimeSecrets
from ..credentials.profile_store import ProfileCredentialStore
from ..credentials.vault import CredentialVault
from ..exceptions import SecretNotFoundError
from ..media.image_processor import ImageProcessor
from ..models import (
    AdditionalFileHookPoint,
    AdditionalFileSpec,
    Chapter,
    DownloadManifest,
    ImageResource,
)
from ..observability.events import EventBus
from ..observability.logging import DownloadLogger
from ..observability.notifications import NotificationService
from ..output.output_lock import OutputDirectoryLocks
from ..plugins.builtin import GenericHtmlPlugin
from ..plugins.lifecycle import PluginRegistry
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import PluginExecutionContext, PluginFormatContext, TransformContext, is_auth_flow
from ..storage.cookies import CookieStore
from ..storage.filesystem import FileSystem
from ..storage.state import UpdateState
from ..transport.gateway import RequestGateway
from .inputs import DevelopmentInputError, decode_input
from .loading import OFFICIAL_HOOKS, DevelopmentLoader, describe_methods, resolve_target, workspace_for
from .output import RawOutput, read_document
from .transport import FixtureTransport, TraceTransport


class DevelopmentKeys(MasterKeyStore):
    """Keys belong to this disposable run, never the user's system key store."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def get(self, vault_id: str, key_id: str) -> bytes | None:
        path = self.directory / f"{vault_id}.{key_id}.key"
        return path.read_bytes() if path.exists() else None

    def create(self, vault_id: str, key_id: str, key: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / f"{vault_id}.{key_id}.key").open("xb") as file:
            file.write(key)

    def delete(self, vault_id: str, key_id: str) -> None:
        (self.directory / f"{vault_id}.{key_id}.key").unlink(missing_ok=True)


class DevelopmentCredentials(ProfileCredentialStore):
    def __init__(self, vault: CredentialVault, profile: str, source: ProfileCredentialStore | None) -> None:
        super().__init__(vault, profile)
        self.source = source

    def get_plugin(self, plugin_id: str, reference: str) -> str:
        try:
            return super().get_plugin(plugin_id, reference)
        except SecretNotFoundError:
            if self.source is None:
                raise
            return self.source.get_plugin(plugin_id, reference)


class DevelopmentLogger(DownloadLogger):
    def __init__(self, output: RawOutput) -> None:
        super().__init__([])
        self.output = output

    async def core(self, event: str, **kwargs: Any) -> None:
        self.output.event("runtime_" + event, **kwargs)

    async def log(self, message: str, *, chapter_id: str | None = None, module: str = "library") -> None:
        self.output.event("runtime_log", message=message, chapter_id=chapter_id, module=module)

    def register_chapter(self, *args: Any, **kwargs: Any) -> None:
        self.output.event("chapter_registered", args=args, kwargs=kwargs)

    async def error_detail(self, error: Exception, **kwargs: Any) -> None:
        self.output.event("runtime_error", error=error, **kwargs)

    async def chapter_header(self, chapter_id: str, **kwargs: Any) -> None:
        self.output.event("chapter_header", chapter_id=chapter_id, **kwargs)

    async def chapter_download(self, chapter_id: str, url: str) -> None:
        self.output.event("chapter_download", chapter_id=chapter_id, url=url)

    async def chapter_save(self, chapter_id: str, path: str | Path) -> None:
        self.output.event("chapter_save", chapter_id=chapter_id, path=path)

    async def chapter_error_group(self, chapter_id: str, category: str, records: Any) -> None:
        self.output.event("chapter_error_group", chapter_id=chapter_id, category=category, records=records)


def validate_return(method: str, value: Any, invoker: PluginInvoker) -> None:
    if method == "inspect":
        invoker._validate_manifest(method, value)
    elif method == "check_updates":
        invoker._validate_update_snapshot(method, value)
    elif method in {"create_image_request", "recover_image_request"}:
        invoker._validate_image_fetch_request(method, value, allow_none=method == "recover_image_request")
    elif method in {"transform", "transform_image"}:
        invoker._validate_artifact(method, value)
    elif method in {"matches", "matches_with_config"}:
        if not isinstance(value, bool):
            raise invoker._error(method, "matcher must return bool")
    elif method == "auth_flow":
        if value is not None and not is_auth_flow(value):
            raise invoker._error(method, "auth_flow must return AuthFlow or None")
    elif method == "output_format_values":

        class Returned:
            def output_format_values(self, context: Any) -> Any:
                return value

        invoker.output_format_values(Returned(), cast(PluginFormatContext, None))
    else:
        validate_optional_return(method, value, invoker)


def validate_optional_return(method: str, value: Any, invoker: PluginInvoker) -> None:
    if method == "additional_file_hook_points":
        if (
            not isinstance(value, tuple)
            or any(not isinstance(point, AdditionalFileHookPoint) for point in value)
            or len(set(value)) != len(value)
        ):
            raise invoker._error(method, "expected a tuple of unique AdditionalFileHookPoint values")
    elif method == "additional_files":
        if not isinstance(value, tuple) or any(not isinstance(item, AdditionalFileSpec) for item in value):
            raise invoker._error(method, "expected tuple[AdditionalFileSpec, ...]")
    elif method in {"validate_config", "cleanup_after_use", "additional_file_received", "additional_file_saved"}:
        if value is not None:
            raise invoker._error(method, "hook must return None")


def bind_input(
    signature: inspect.Signature, inputs: dict[str, Any], root: Path, refs: dict[str, Any]
) -> tuple[list[Any], dict[str, Any], inspect.BoundArguments]:
    try:
        decoded = decode_input(inputs, root=root, refs=refs)
        args, kwargs = list(decoded.get("args", [])), dict(decoded.get("kwargs", {}))
        return args, kwargs, signature.bind_partial(*args, **kwargs)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError, OSError) as exc:
        raise DevelopmentInputError(str(exc)) from exc


class DevelopmentSession:
    def __init__(self, options: dict[str, Any], output: RawOutput) -> None:
        self.options, self.output = options, output
        self.target = Path(options["target"]).resolve()
        self.workspace = workspace_for(self.target)
        self.settings = (
            read_document(self.workspace / "plugin-dev.yaml") if (self.workspace / "plugin-dev.yaml").exists() else {}
        )
        self.record = resolve_target(self.target, class_name=options.get("class_name"), kind=options.get("kind"))
        self.records = [self.record] + [resolve_target(Path(path)) for path in options.get("processors", [])]
        if len({record.id for record in self.records}) != len(self.records):
            raise DevelopmentInputError("selected plugin sources have duplicate IDs")
        self.refs: dict[str, Any] = {}
        self.instance: Any = None
        self.context: PluginExecutionContext | None = None
        self.service: DownloadService | None = None
        self.fixture = FixtureTransport(Path(options["fixture"])) if options.get("fixture") else None
        self.traces: list[TraceTransport] = []

    def _transport(self) -> httpx.AsyncBaseTransport:
        inner = (
            self.fixture
            if self.fixture is not None
            else httpx.AsyncHTTPTransport(http2=self.config.network.http2, proxy=self.config.network.proxy)
        )
        trace = TraceTransport(inner, self.output, record=self.options.get("record", False))
        self.traces.append(trace)
        return trace

    def _config(self) -> AppConfig:
        application = dict(self.settings.get("application", {}))
        if self.options.get("app_config"):
            application = deep_merge(application, read_document(Path(self.options["app_config"])) or {})
        application = deep_merge(
            application,
            {
                "storage": {"data_root": str(self.output.directory / "data")},
                "plugins": {"root": str(self.output.directory / "plugins")},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
                "security": {"plugin_verification": "off"},
            },
        )
        chain = [record.id for record in self.records if record.kind == "image_processor_plugin"]
        application["image_processors"] = {**application.get("image_processors", {}), "chain": chain}
        settings = application.setdefault("plugin_settings", {})
        for record in self.records:
            private = settings.setdefault(record.id, {})
            configured = dict(private.get("config", {}))
            if record.id == self.record.id:
                configured = deep_merge(configured, self.settings.get("config", {}))
                if self.options.get("config"):
                    supplied = read_document(Path(self.options["config"])) or {}
                    configured = deep_merge(configured, supplied.get("config", supplied))
            private["config"] = configured
            names = self.settings.get("secret_references", {}) if record.id == self.record.id else {}
            private["secrets"] = {**private.get("secrets", {}), **names}
            for name in self.secret_values() if record.id == self.record.id else ():
                private["secrets"].setdefault(name, name.upper())
        return AppConfig.model_validate(application)

    async def open(self) -> None:
        self.config = self._config()
        paths = resolve_paths(self.config)
        self.registry = PluginRuntime(
            self.config, self.output.directory / "plugins", mode="bypass-all", loader=DevelopmentLoader(self.output)
        )
        self.registry.registry = PluginRegistry(tuple(self.records))
        self.registry.register_builtin(GenericHtmlPlugin)
        self.logger = DevelopmentLogger(self.output)
        self.gateway = RequestGateway(self.config, transport_factory=self._transport, logger=self.logger)
        vault = CredentialVault(self.output.directory / "data", keys=DevelopmentKeys(self.output.directory / "keys"))
        source = None
        if self.options.get("profile"):
            source_root = Path(self.options.get("profile_data_root") or default_data_root()).resolve()
            source = CredentialVault(source_root).profile(self.options["profile"])
        self.credentials = DevelopmentCredentials(vault, self.config.profile.default, source)
        cookie_store = CookieStore(FileSystem(paths["cookie"]), store=self.credentials)
        if source is not None:
            cookie_root = source.vault.filesystem.path(source.vault.relative(source.profile, "cookies")).parent
            jar = CookieStore(FileSystem(cookie_root), store=source).load()
            self.gateway.client.cookies = httpx.Cookies(jar)
        self._load_cookies(self.settings.get("cookies", []))
        self._load_secrets()
        events = EventBus(self.logger)
        dependencies = _RuntimeDependencies(
            outputs=FileSystem(paths["downloads"]),
            logs=FileSystem(paths["logs"]),
            state=UpdateState(FileSystem(paths["state"])),
            events=events,
            logger=self.logger,
            notifications=NotificationService(self.config.notification, self.logger, events, {}),
            cookie_store=cookie_store,
            credentials=self.credentials,
            cookie_baseline={},
            gateway=self.gateway,
            image_processor=ImageProcessor(),
            output_locks=OutputDirectoryLocks(
                FileSystem(paths["state"]), timeout_seconds=self.config.output.lock_timeout_seconds, logger=self.logger
            ),
        )
        self.service = DownloadService(self.config, self.registry, dependencies)
        self.registry.validate_settings()
        self.output.event(
            "session_opened", plugin_id=self.record.id, config=self.registry.effective_config(self.record)
        )

    def _load_cookies(self, cookies: Any) -> None:
        for cookie in cookies:
            self.gateway.client.cookies.set(
                cookie["name"], str(cookie["value"]), domain=cookie["domain"], path=cookie.get("path", "/")
            )

    def _load_secrets(self) -> None:
        for name, value in self.secret_values().items():
            reference = self.config.plugin_settings[self.record.id].secrets[name]
            self.credentials.set_plugin(self.record.id, reference, str(value))

    def secret_values(self) -> dict[str, str]:
        return {**(self.fixture.secrets if self.fixture is not None else {}), **self.settings.get("secrets", {})}

    def ensure_instance(self) -> Any:
        if self.instance is None:
            self.instance = self.registry.loader.typed_instance(self.record)
            PluginInvoker(self.record.id).validate_config(
                self.instance, self.registry.effective_config(self.record), safe_app_settings(self.config)
            )
        return self.instance

    def execution_context(self) -> PluginExecutionContext:
        instance = self.ensure_instance()
        if self.context is None:
            private = self.config.plugin_settings[self.record.id]
            self.context = PluginExecutionContext(
                self.registry.effective_config(self.record),
                safe_app_settings(self.config),
                self.record.manifest.value,
                None,
                RuntimeSecrets(self.record.id, private.secrets, store=self.credentials),
                self.gateway,
                cookie_provider=self.gateway,
            )
            if self.record.kind == "site_plugin":
                self.context, _, _ = self.service._operations.build(  # type: ignore[union-attr]
                    self.options.get("url") or self.settings.get("sample_url", "https://example.test/"),
                    self.record,
                    instance,
                    PluginInvoker(self.record.id),
                    None,
                    None,
                )
        return self.context

    def defaults(self, method: str) -> dict[str, Any]:
        if method in {"validate_config", "matches", "matches_with_config", "cleanup_after_use"}:
            return {
                "config": self.registry.effective_config(self.record),
                "app_settings": safe_app_settings(self.config),
                "url": self.options.get("url") or self.settings.get("sample_url", "https://example.test/"),
            }
        context: Any = self.execution_context()
        if method in {"transform", "transform_image"}:
            image = ImageResource("https://example.test/image.png")
            chapter = Chapter(1, "Debug", images=(image,))
            context = TransformContext(
                image,
                context.config,
                context.app_settings,
                context.manifest,
                None,
                DownloadManifest("Debug", (chapter,)),
                chapter,
            )
        elif method == "output_format_values":
            context = PluginFormatContext(
                self.record.id,
                self.record.kind,
                self.options.get("url", "https://example.test/"),
                context.config,
                context.app_settings,
                context.manifest,
                None,
            )
        return {
            "context": context,
            "config": self.registry.effective_config(self.record),
            "app_settings": safe_app_settings(self.config),
            "url": self.options.get("url") or self.settings.get("sample_url", "https://example.test/"),
        }

    async def call(
        self, method: str, inputs: dict[str, Any] | None = None, *, root: Path | None = None, save_as: str = "last"
    ) -> Any:
        instance = self.ensure_instance()
        self.refs.update(plugin=instance)
        target = instance
        if "." in method:
            reference, method = method.rsplit(".", 1)
            target = self.refs[reference]
        callback = getattr(target, method, None)
        if not callable(callback):
            raise DevelopmentInputError(f"not a callable method: {method}")
        inputs = inputs or {}
        signature = inspect.signature(callback)
        if any(name.lstrip("_") == "context" for name in signature.parameters):
            self.refs["context"] = self.execution_context()
        args, kwargs, bound = bind_input(signature, inputs, root or self.workspace, self.refs)
        if target is instance and method in OFFICIAL_HOOKS:
            defaults = self.defaults(method)
            for name, parameter in signature.parameters.items():
                key = name.lstrip("_")
                if name not in bound.arguments and key in defaults:
                    if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                        args.append(defaults[key])
                    else:
                        kwargs[name] = defaults[key]
        try:
            signature.bind(*args, **kwargs)
        except TypeError as exc:
            raise DevelopmentInputError(str(exc)) from exc
        self.output.event("method_started", method=method, args=args, kwargs=kwargs)
        try:
            value = callback(*args, **kwargs)
            if inspect.isawaitable(value):
                if (
                    target is instance
                    and method in OFFICIAL_HOOKS
                    and method
                    in {
                        "validate_config",
                        "matches",
                        "matches_with_config",
                        "auth_flow",
                        "output_format_values",
                        "additional_file_hook_points",
                    }
                ):
                    if inspect.iscoroutine(value):
                        value.close()
                    raise TypeError(f"{method} must be synchronous")
                value = await value
            elif target is instance and method in {
                "inspect",
                "create_image_request",
                "recover_image_request",
                "transform_image",
                "transform",
                "check_updates",
                "additional_files",
                "additional_file_received",
                "additional_file_saved",
            }:
                raise TypeError(f"{method} must return an awaitable")
            self.output.event("method_returned", method=method, result=value)
            if target is instance and method in OFFICIAL_HOOKS:
                validate_return(method, value, PluginInvoker(self.record.id))
            self.refs[save_as] = value
            self.refs["last"] = value
            return value
        except BaseException as exc:
            self.output.event("method_failed", method=method, error=exc)
            raise

    async def simulate(self, url: str, mode: str) -> Any:
        if mode == "replay" and self.fixture is None:
            raise ValueError("replay requires --fixture")
        assert self.service is not None
        plugin_id = self.record.id if self.record.kind == "site_plugin" else None
        options: dict[str, Any] = {
            "url": url,
            "plugin_id": plugin_id,
            "force_plugin": bool(plugin_id and self.options.get("force_plugin", False)),
        }
        result: Any
        if mode == "preview":
            result = await self.service.inspect(InspectRequest(**options))
        else:
            result = await self.service.download(DownloadRequest(**options))
        if self.fixture:
            self.output.event("fixture_remaining", requests=self.fixture.remaining())
        self.output.event("simulation_finished", result=result)
        return result

    async def check(self) -> dict[str, Any]:
        instance = self.ensure_instance()
        source = self.record.manifest.directory / self.record.manifest.value["entry"]["file"]
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        pending = [
            {"line": node.lineno, "message": ast.get_source_segment(source.read_text(encoding="utf-8-sig"), node)}
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise)
            and node.exc is not None
            and (
                isinstance(node.exc, ast.Name)
                and node.exc.id == "NotImplementedError"
                or isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name)
                and node.exc.func.id == "NotImplementedError"
            )
        ]
        scenarios = []
        for scenario in self.settings.get("scenarios", []):
            path = (self.workspace / scenario).resolve()
            document = read_document(path)
            for step in document.get("steps", []):
                value = await self.call(step["method"], step, root=path.parent, save_as=step.get("save_as", "last"))
                if "expected" in step and self.output.value(value) != step["expected"]:
                    raise AssertionError(f"scenario expectation failed: {path}: {step['method']}")
            scenarios.append(str(path))
        return {
            "valid": not pending,
            "plugin_id": self.record.id,
            "unimplemented": pending,
            "methods": describe_methods(instance),
            "scenarios": scenarios,
        }

    async def close(self) -> None:
        primary: BaseException | None = None
        try:
            if self.instance is not None:
                instance, self.instance = self.instance, None
                await PluginInvoker(self.record.id).cleanup_after_use(instance)
        except BaseException as exc:
            primary = exc
            self.output.event("cleanup_failed", error=exc)
        finally:
            self.context = None
            self.refs.clear()
            if self.service is not None:
                await self.service.close()
                self.service = None
            elif hasattr(self, "gateway"):
                await self.gateway.close()
                self.registry.close()
        if primary:
            raise primary
