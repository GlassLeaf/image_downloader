"""Read-only manifest and image-request inspection CLI command."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from contextlib import redirect_stdout
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..immutable import thaw_json
from ..models import EffectiveRequestPreview, ManifestInspectionResult, RequestSpec
from .constants import EXIT_PARTIAL, EXIT_SUCCESS
from .setup import _config_for, _fallback, _plugin_root, _runtime_overrides
from .validation import _reject_command_options


class InspectCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        return await inspect_command(args)


async def inspect_command(args: argparse.Namespace) -> int:
    """Run inspection for the canonical command and the download alias."""
    _reject_command_options(
        args,
        (
            "selection_priority",
            "host",
            "plugin_download_policy",
            "plugin_download_policy_file",
            "list_updated_urls",
            "no_console_log",
            "existing_file",
            "image_format",
            "export_cookies",
            "import_cookies",
            "import_browser_cookies",
        ),
        "inspect",
    )
    hostname = urlparse(args.url).hostname
    overrides = _runtime_overrides(args)
    plugin_id = args.plugin_id or args.force_plugin_id
    force_plugin = args.force_plugin_id is not None
    config, config_root, _, _ = _config_for(
        args,
        hostname,
        allow_root_setup=False,
        rewrite_user_layers=False,
    )
    # Registry discovery/import can execute plugin module top-level code. Keep a
    # plugin's accidental stdout from corrupting the one result object as well.
    with redirect_stdout(sys.stderr):
        service = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
            plugin_verification_override=args.plugin_verification_override,
        )._compose_for_inspection()
    try:
        # Plugin output must not mix with the one structured command result.
        with redirect_stdout(sys.stderr):
            result = await service.inspect(
                args.url,
                plugin_overrides=overrides,
                fallback_override=_fallback(args),
                plugin_id=plugin_id,
                force_plugin=force_plugin,
                resolve_image_requests=not args.manifest_only,
            )
        payload = _inspection_json(result, getattr(args, "inspection_data", "url"))
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_PARTIAL if result.failures else EXIT_SUCCESS
    finally:
        with redirect_stdout(sys.stderr):
            await service.close()


def _request_json(request: RequestSpec) -> dict[str, object]:
    return {
        "url": request.url,
        "method": request.method,
        "headers": dict(request.headers),
        "cookies": dict(request.cookies),
        "referer": request.referer,
        "query": dict(request.query),
        "form": dict(request.form),
        "json": thaw_json(request.json),
        "auth_required": request.auth_required,
        "retry_non_idempotent": request.retry_non_idempotent,
    }


def _effective_request_json(request: EffectiveRequestPreview) -> dict[str, object]:
    return {
        "method": request.method,
        "url": request.url,
        "headers": [{"name": header.name, "value": header.value} for header in request.headers],
        "cookies": [{"name": cookie.name, "value": cookie.value} for cookie in request.cookies],
        "body": {
            "encoding": "base64",
            "size_bytes": len(request.body),
            "data": base64.b64encode(request.body).decode("ascii"),
        },
    }


def _manifest_summary(result: ManifestInspectionResult) -> dict[str, int]:
    return {
        "chapter_count": len(result.manifest.chapters),
        "image_count": sum(len(chapter.images) for chapter in result.manifest.chapters),
    }


def _save_options_json(options: object) -> dict[str, object]:
    return {
        "format": options.format,
        "extension": options.extension,
        "quality": options.quality,
        "optimize": options.optimize,
        "progressive": options.progressive,
        "lossless": options.lossless,
        "compress_level": options.compress_level,
        "exif": options.exif,
    }


def _full_manifest_json(result: ManifestInspectionResult) -> dict[str, object]:
    manifest = result.manifest
    return {
        "title": manifest.title,
        "content_id": manifest.content_id,
        "author": manifest.author,
        "access": manifest.access,
        "revision": manifest.revision,
        "metadata": dict(manifest.metadata),
        "chapters": [
            {
                "number": chapter.number,
                "title": chapter.title,
                "subtitle": chapter.subtitle,
                "chapter_id": chapter.chapter_id,
                "images": [
                    {
                        "url": image.url,
                        "index": image.index,
                        "referer": image.referer,
                        "headers": dict(image.headers),
                        "save_options": _save_options_json(image.save_options),
                        "image_id": image.image_id,
                        "metadata": dict(image.metadata),
                    }
                    for image in chapter.images
                ],
            }
            for chapter in manifest.chapters
        ],
    }


def _failure_json(result: ManifestInspectionResult, index: int) -> dict[str, str]:
    failure = result.image_requests[index].failure
    assert failure is not None
    return {
        "code": failure.code,
        "reason": failure.reason,
        "exception": failure.exception,
        "phase": failure.phase,
    }


def _inspection_json(result: ManifestInspectionResult, level: str) -> dict[str, object]:
    if level not in {"url", "http", "all"}:
        raise ValueError("inspection data level must be url, http, or all")
    payload: dict[str, object] = {
        "source_url": result.source_url,
        "plugin_id": result.plugin_id,
        "inspection_data": level,
        "request_resolution": "resolved" if result.request_resolution_performed else "manifest_only",
        "manifest": _full_manifest_json(result) if level == "all" else _manifest_summary(result),
    }
    requests: list[dict[str, object]] = []
    for index, resolution in enumerate(result.image_requests):
        item: dict[str, object] = {
            "chapter_position": resolution.chapter_position,
            "image_position": resolution.image_position,
            "status": resolution.status.value,
        }
        if level == "url":
            if resolution.effective_request is not None:
                item["effective_url"] = resolution.effective_request.url
        else:
            if resolution.request is not None:
                item["request"] = _request_json(resolution.request)
                item["uses_image_fetch_request"] = resolution.uses_image_fetch_request
            if resolution.effective_request is not None:
                item["effective_request"] = _effective_request_json(resolution.effective_request)
            if level == "all" and resolution.request is not None:
                item["plugin_data"] = dict(resolution.plugin_data)
        if resolution.failure is not None:
            item["failure"] = _failure_json(result, index)
        requests.append(item)
    payload["image_requests"] = requests
    return payload
