"""Resolve development sources without installing or modifying their manifests."""

from __future__ import annotations

import ast
import hashlib
import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from typing import Any, cast

from ..immutable import freeze_json
from ..plugins.lifecycle import PluginClassLoader, PluginRecord
from ..plugins.plugin_files import canonical_jcs
from ..plugins.plugin_manifest import PluginManifest, author_config, read_manifest
from ..ports import is_auth_flow
from .output import RawOutput, read_document

SITE_HOOKS = {
    "validate_config",
    "matches",
    "inspect",
    "create_image_request",
    "recover_image_request",
    "auth_flow",
    "transform_image",
}
OPTIONAL_HOOKS = {
    "matches_with_config",
    "check_updates",
    "output_format_values",
    "cleanup_after_use",
    "additional_file_hook_points",
    "additional_files",
    "additional_file_received",
    "additional_file_saved",
}
OFFICIAL_HOOKS = SITE_HOOKS | OPTIONAL_HOOKS | {"transform"}


def workspace_for(target: Path) -> Path:
    path = target.resolve()
    directory = path if path.is_dir() else path.parent
    for candidate in (directory, *directory.parents):
        if (candidate / "plugin-dev.yaml").is_file():
            return candidate
    return directory.parent if (directory / "manifest.json").exists() else directory


def resolve_target(target: Path, *, class_name: str | None = None, kind: str | None = None) -> PluginRecord:
    target = target.resolve(strict=True)
    if target.is_dir() and (target / "plugin").is_dir() and (target / "plugin-dev.yaml").exists():
        target /= "plugin"
    elif target.is_dir() and (target / "plugin-dev.yaml").exists():
        configured = read_document(target / "plugin-dev.yaml").get("target")
        if configured:
            target = (target / configured).resolve(strict=True)
    if target.is_dir() or target.name == "manifest.json":
        directory = target if target.is_dir() else target.parent
        manifest = read_manifest(directory, mode="bypass-all")
        value = dict(manifest.value)
        defaults = author_config(manifest)
    else:
        directory = target.parent
        classes = [
            node for node in ast.parse(target.read_text(encoding="utf-8-sig")).body if isinstance(node, ast.ClassDef)
        ]
        candidates = [
            node.name
            for node in classes
            if any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name in {"inspect", "transform"}
                for child in node.body
            )
        ]
        if class_name is None:
            if len(candidates) != 1:
                raise ValueError(f"specify --class; candidate classes: {candidates or [node.name for node in classes]}")
            class_name = candidates[0]
        selected = next((node for node in classes if node.name == class_name), None)
        if selected is None:
            raise ValueError(f"class not found: {class_name}")
        inferred = (
            "site_plugin"
            if any(getattr(node, "name", "") == "inspect" for node in selected.body)
            else ("image_processor_plugin")
        )
        value = {
            "id": "dev.local." + hashlib.sha256(str(target).encode()).hexdigest()[:12],
            "kind": kind or inferred,
            "entry": {"file": target.name, "class": class_name},
            "match_priority": 0,
        }
        defaults = {}
        if (directory / "plugin.yaml").is_file():
            defaults = read_document(directory / "plugin.yaml").get("config", {})
    if class_name:
        value["entry"] = {**value["entry"], "class": class_name}
    if kind:
        value["kind"] = kind
    # A development record intentionally binds to editable source, not an obsolete signed tree.
    value = {key: item for key, item in value.items() if key not in {"file_tree", "file_tree_sha256"}}
    digest = hashlib.sha256(canonical_jcs(value)).hexdigest()
    dev_manifest = PluginManifest(cast(Mapping[str, Any], freeze_json(value)), None, digest, directory)
    return PluginRecord(dev_manifest, defaults, None)


def _trace_method(callback: Callable[..., Any], name: str, output: RawOutput) -> Callable[..., Any]:
    def start(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        output.event("call_started", method=name, instance=id(args[0]) if args else None, args=args[1:], kwargs=kwargs)

    def finish(value: Any) -> Any:
        output.event("call_finished", method=name, result=value)
        if name.endswith(".auth_flow") and value is not None and is_auth_flow(value):
            return traced_flow(value, output)
        return value

    if inspect.iscoroutinefunction(callback):

        @wraps(callback)
        async def async_call(*args: Any, **kwargs: Any) -> Any:
            start(args, kwargs)
            try:
                return finish(await callback(*args, **kwargs))
            except BaseException as exc:
                output.event("call_failed", method=name, error=exc)
                raise

        return async_call

    @wraps(callback)
    def sync_call(*args: Any, **kwargs: Any) -> Any:
        start(args, kwargs)
        try:
            return finish(callback(*args, **kwargs))
        except BaseException as exc:
            output.event("call_failed", method=name, error=exc)
            raise

    return sync_call


def traced_flow(flow: Any, output: RawOutput) -> Any:
    class Flow:
        def is_auth_failure(self, request: Any, response: Any) -> Any:
            return flow.is_auth_failure(request, response)

        async def apply(self, request: Any) -> Any:
            return await flow.apply(request)

        async def refresh(self, failed: Any, response: Any) -> Any:
            return await flow.refresh(failed, response)

    attributes = {
        name: _trace_method(getattr(Flow, name), "auth." + name, output)
        for name in ("is_auth_failure", "apply", "refresh")
    }
    if hasattr(flow, "allowed_origins"):
        attributes["allowed_origins"] = flow.allowed_origins
    return type("TracedAuthFlow", (Flow,), attributes)()


class DevelopmentLoader(PluginClassLoader):
    def __init__(self, output: RawOutput) -> None:
        super().__init__()
        self.output = output

    def _import_class(self, record: PluginRecord) -> tuple[type[object], str]:
        plugin_class, namespace = super()._import_class(record)
        methods = {
            name: _trace_method(getattr(plugin_class, name), record.id + "." + name, self.output)
            for name in OFFICIAL_HOOKS
            if inspect.isfunction(getattr(plugin_class, name, None))
        }
        return type(plugin_class.__name__, (plugin_class,), methods), namespace


def describe_methods(instance: object) -> list[dict[str, Any]]:
    result = []
    for name in dir(instance):
        if name.startswith("__"):
            continue
        callback = getattr(instance, name)
        if callable(callback):
            try:
                signature = str(inspect.signature(callback))
            except (TypeError, ValueError):
                signature = "(signature unavailable)"
            result.append(
                {
                    "name": name,
                    "signature": signature,
                    "async": inspect.iscoroutinefunction(callback),
                    "official": name in OFFICIAL_HOOKS,
                }
            )
    return result
