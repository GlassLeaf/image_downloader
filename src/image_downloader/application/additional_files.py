"""Operation-owned optional file acquisition; raw data stays inside plugin callbacks."""

from __future__ import annotations

import inspect
import re
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal, cast

from ..exceptions import ErrorInfo, PluginError, StorageSafetyError, error_info_for
from ..models import (
    AdditionalFileHookContext,
    AdditionalFileHookPoint,
    AdditionalFileOutcome,
    AdditionalFileReceiveResult,
    AdditionalFileSaveResult,
    AdditionalFileSpec,
    DownloadManifest,
    DownloadResult,
    RequestSpec,
)
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.logging import safe_relative_path, safe_url
from ..output.output_allocator import OutputAllocation, OutputAllocator, OutputFormatContext
from ..ports import AdditionalFileProvider, PluginExecutionContext, SitePlugin

if TYPE_CHECKING:
    from .service import DownloadService
    from .workflow_retry import ImageLedger

_Phase = Literal["declaration", "hook", "receive", "save", "read", "received_callback", "saved_callback"]
_Status = Literal["received", "saved", "skipped", "failed", "completed", "no_target"]


@dataclass(frozen=True, slots=True)
class _FileTarget:
    file_id: str
    relative_path: str


class AdditionalFiles:
    def __init__(
        self,
        service: DownloadService,
        plugin: SitePlugin,
        context: PluginExecutionContext,
        allocator: OutputAllocator,
        plugin_id: str,
        url: str,
        plugin_values: Mapping[str, Mapping[str, str]],
        ledger: ImageLedger | None = None,
    ) -> None:
        self.service, self.plugin, self.context = service, plugin, context
        self.allocator, self.plugin_id = allocator, plugin_id
        self.url, self.plugin_values, self.ledger = url, plugin_values, ledger
        self.operation_id = ledger.operation_id if ledger else uuid.uuid4().hex
        self.attempt_number = ledger.attempt_number if ledger else 1
        self.points: tuple[AdditionalFileHookPoint, ...] = ()
        self.outcomes: list[AdditionalFileOutcome] = []
        self.pending: list[tuple[AdditionalFileHookContext, _FileTarget, Path]] = []
        self.temporary: tempfile.TemporaryDirectory | None = None

    def close(self) -> None:
        if self.temporary is not None:
            self.temporary.cleanup()

    def hook(self, point: AdditionalFileHookPoint, **values: Any) -> AdditionalFileHookContext:
        return AdditionalFileHookContext(
            point, self.operation_id, self.attempt_number, self.url, uuid.uuid4().hex, **values
        )

    async def initialize(self) -> None:
        hook = self.hook(AdditionalFileHookPoint.BEFORE_MANIFEST)
        try:
            declaration = getattr(self.plugin, "additional_file_hook_points", None)
            provider = getattr(self.plugin, "additional_files", None)
            if declaration is None and provider is None:
                return
            if not callable(declaration) or not callable(provider):
                raise PluginError("additional file methods must be paired callables")
            points = declaration(self.context)
            if inspect.isawaitable(points):
                if inspect.iscoroutine(points):
                    points.close()
                raise PluginError("additional file declaration must be synchronous")
            if not isinstance(points, tuple) or any(not isinstance(p, AdditionalFileHookPoint) for p in points):
                raise PluginError("invalid additional file hook points")
            if len(set(points)) != len(points):
                raise PluginError("duplicate additional file hook points")
            self.points = points
        except Exception as exc:
            await self.record(hook, "", "declaration", "failed", error=exc)

    async def record(
        self,
        hook: AdditionalFileHookContext,
        file_id: str,
        phase: _Phase,
        status: _Status,
        *,
        path: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        info = self.safe_error(error) if error else None
        outcome = AdditionalFileOutcome(
            file_id,
            hook.point,
            hook.operation_id,
            hook.invocation_id,
            hook.attempt_number,
            hook.chapter.number if hook.chapter else None,
            hook.image.index if hook.image else None,
            phase,
            status,
            path,
            info,
        )
        self.outcomes.append(outcome)
        if self.ledger is not None:
            self.ledger.record_additional(outcome, hook)
        await best_effort_diagnostic(
            self.service.logger.core,
            "additional_file_" + status,
            module="plugin",
            plugin_id=self.plugin_id,
            action=phase,
            debug=True,
        )

    def safe_error(self, exc: BaseException) -> ErrorInfo:
        info = error_info_for(exc)
        return replace(
            info,
            response_url=safe_url(info.response_url) if info.response_url else None,
            output_path=safe_relative_path(info.output_path, self.allocator.filesystem.root)
            if info.output_path
            else None,
        )

    def validate(self, spec: object) -> AdditionalFileSpec:
        if not isinstance(spec, AdditionalFileSpec):
            raise PluginError("additional file hook must return AdditionalFileSpec values")
        if not isinstance(spec.file_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", spec.file_id):
            raise PluginError("additional file ID must be a non-secret identifier")
        if not isinstance(spec.relative_path, str) or not spec.relative_path:
            raise StorageSafetyError("additional file path must be relative")
        windows = PureWindowsPath(spec.relative_path)
        parts = spec.relative_path.replace("\\", "/").split("/")
        if windows.drive or windows.root or any(p in {"", ".", ".."} for p in parts):
            raise StorageSafetyError("additional file path must stay within its chapter")
        self.allocator.filesystem.path(Path(*parts))
        if (spec.request is None) == (spec.data is None):
            raise PluginError("additional file requires exactly one of request or data")
        if spec.request is not None and not isinstance(spec.request, RequestSpec):
            raise PluginError("additional file request must be RequestSpec")
        if spec.data is not None and not isinstance(spec.data, bytes):
            raise PluginError("additional file content must be bytes")
        return spec

    async def callback(
        self,
        name: Literal["additional_file_received", "additional_file_saved"],
        result: AdditionalFileReceiveResult | AdditionalFileSaveResult,
    ) -> None:
        phase: _Phase = "received_callback" if name.endswith("received") else "saved_callback"
        try:
            callback = getattr(self.plugin, name, None)
            if callback is None:
                return
            if not callable(callback):
                raise PluginError("additional file callback must be callable")
            value = callback(result, self.context)
            if not inspect.isawaitable(value):
                raise PluginError("additional file callback must be async")
            if await value is not None:
                raise PluginError("additional file callback must return None")
            await self.record(result.hook, result.file_id, phase, "completed")
        except Exception as exc:
            await self.record(result.hook, result.file_id, phase, "failed", error=exc)

    async def run(self, point: AdditionalFileHookPoint, **values: Any) -> None:
        if point not in self.points:
            return
        hook = self.hook(point, **values)
        try:
            specs = await cast(AdditionalFileProvider, self.plugin).additional_files(hook, self.context)
            if not isinstance(specs, tuple):
                raise PluginError("additional file hook must return a tuple")
        except Exception as exc:
            await self.record(hook, "", "hook", "failed", error=exc)
            return
        for spec in specs:
            await self.process(hook, spec)

    async def process(self, hook: AdditionalFileHookContext, value: object) -> None:
        file_id = ""
        try:
            if isinstance(value, AdditionalFileSpec) and isinstance(value.file_id, str):
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value.file_id):
                    file_id = value.file_id
            spec = self.validate(value)
        except Exception as exc:
            await self.record(hook, file_id, "hook", "failed", error=exc)
            return
        target = _FileTarget(file_id, spec.relative_path)
        response = None
        try:
            response = await self.context.requests.execute(spec.request) if spec.request else None
            data = self.check_size(response.body if response else spec.data)
            result = AdditionalFileReceiveResult(hook, file_id, "received", data, response)
            await self.record(hook, file_id, "receive", "received")
        except Exception as exc:
            result = AdditionalFileReceiveResult(hook, file_id, "failed", error=self.safe_error(exc))
            await self.record(hook, file_id, "receive", "failed", error=exc)
        await self.callback("additional_file_received", result)
        if result.status == "failed":
            return
        assert result.data is not None
        if hook.point == AdditionalFileHookPoint.BEFORE_MANIFEST:
            try:
                if self.temporary is None:
                    self.temporary = tempfile.TemporaryDirectory(prefix="image-downloader-additional-")
                path = Path(self.temporary.name) / uuid.uuid4().hex
                path.write_bytes(result.data)
                self.pending.append((hook, target, path))
            except Exception as exc:
                await self.record(hook, file_id, "save", "failed", error=exc)
                await self.callback(
                    "additional_file_saved",
                    AdditionalFileSaveResult(hook, file_id, "failed", error=self.safe_error(exc)),
                )
        else:
            await self.save(hook, target, result.data)

    def check_size(self, data: bytes | None) -> bytes:
        if not isinstance(data, bytes) or len(data) > self.service.config.network.max_response_bytes:
            raise StorageSafetyError("additional file exceeds the configured size limit")
        return data

    async def flush_pending(self, manifest: DownloadManifest) -> None:
        for hook, spec, path in self.pending:
            if not manifest.chapters:
                await self.record(hook, spec.file_id, "save", "no_target")
            for chapter in manifest.chapters:
                target = replace(hook, manifest=manifest, chapter=chapter)
                try:
                    data = path.read_bytes()
                except Exception as exc:
                    await self.record(target, spec.file_id, "save", "failed", error=exc)
                    await self.callback(
                        "additional_file_saved",
                        AdditionalFileSaveResult(target, spec.file_id, "failed", error=self.safe_error(exc)),
                    )
                else:
                    await self.save(target, spec, data)
        self.pending.clear()

    def directory(self, hook: AdditionalFileHookContext) -> Path:
        assert hook.manifest is not None and hook.chapter is not None
        return self.allocator.chapter_directory(
            OutputFormatContext(hook.manifest, hook.chapter, self.url, self.plugin_id, plugin_values=self.plugin_values)
        )

    async def write(self, hook: AdditionalFileHookContext, spec: _FileTarget, data: bytes) -> OutputAllocation:
        directory = self.directory(hook)
        relative = directory / Path(*spec.relative_path.replace("\\", "/").split("/"))
        # Confine to a chapter first, then apply managed-output locking/allocation.
        self.allocator.filesystem.ensure_directory(relative.parent)
        async with self.service.output_locks.hold(self.allocator.filesystem.path(directory)):
            await self.allocator.refresh_directory(relative.parent)
            allocation = await self.allocator.allocate_relative(relative)
            if allocation.should_write:
                try:
                    self.allocator.filesystem.write_bytes_atomic(allocation.relative_path, data)
                    await allocation.commit()
                except BaseException:
                    await allocation.abort()
                    raise
            return allocation

    async def save(self, hook: AdditionalFileHookContext, spec: _FileTarget, data: bytes) -> None:
        try:
            allocation = await self.write(hook, spec, data)
            path = str(self.allocator.filesystem.path(allocation.relative_path))
            status: Literal["saved", "skipped"] = "saved" if allocation.should_write else "skipped"
            await self.record(
                hook, spec.file_id, "save", status, path=safe_relative_path(path, self.allocator.filesystem.root)
            )
        except Exception as exc:
            await self.record(hook, spec.file_id, "save", "failed", error=exc)
            await self.callback(
                "additional_file_saved",
                AdditionalFileSaveResult(hook, spec.file_id, "failed", error=self.safe_error(exc)),
            )
            return
        read_error: ErrorInfo | None = None
        stored: bytes | None
        try:
            stored = (
                data
                if allocation.should_write
                else self.allocator.filesystem.read_bytes_bounded(
                    allocation.relative_path, self.service.config.network.max_response_bytes
                )
            )
            if not allocation.should_write:
                await self.record(hook, spec.file_id, "read", "completed")
        except Exception as exc:
            stored, read_error = None, self.safe_error(exc)
            await self.record(hook, spec.file_id, "read", "failed", error=exc)
        await self.callback(
            "additional_file_saved",
            AdditionalFileSaveResult(hook, spec.file_id, status, path, stored, read_error=read_error),
        )


def additional_file_payload(result: DownloadResult | None, root: Path) -> list[dict[str, object]]:
    """Only the sanitized public outcomes, never plugin callback bodies."""
    return (
        [
            dict(asdict(item), path=safe_relative_path(item.path, root) if item.path else None)
            for item in result.additional_files
        ]
        if result
        else []
    )
