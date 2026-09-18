"""download CLI command implementation."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext, redirect_stdout
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..models import UpdateChangeKind
from .constants import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS
from .setup import (
    _config_for,
    _fallback,
    _plugin_root,
    _runtime_overrides,
)
from .validation import _reject_command_options


class DownloadCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        _reject_command_options(args, ("selection_priority", "host"), "download")
        if any(
            getattr(args, name, None) is not None
            for name in ("export_cookies", "import_cookies", "import_browser_cookies")
        ):
            raise ValueError("cookie import or export cannot be combined with URL")
        hostname = urlparse(args.url).hostname
        config, config_root, _, _ = _config_for(args, hostname, allow_root_setup=True)
        service = RuntimeComposer(config, config_root=config_root, plugin_root=_plugin_root(args, config)).compose()
        overrides = _runtime_overrides(args)
        diagnostics_output = redirect_stdout(sys.stderr) if args.json_output else nullcontext()
        try:
            if args.list_updated_urls:
                with diagnostics_output:
                    update_result = await service.check_updates(
                        args.url,
                        plugin_overrides=overrides,
                        fallback_override=_fallback(args),
                    )
                visible = [change for change in update_result.changes if change.kind is not UpdateChangeKind.REMOVED]
                removed = sum(change.kind is UpdateChangeKind.REMOVED for change in update_result.changes)
                if args.json_output:
                    payload = {"updated_urls": [change.url for change in visible], "removed": removed}
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    for change in visible:
                        print(change.url)
                    print(f"updated: {len(visible)}, removed: {removed}", file=sys.stderr)
                return EXIT_SUCCESS
            with diagnostics_output:
                download_result = await service.run(
                    args.url,
                    plugin_overrides=overrides,
                    fallback_override=_fallback(args),
                )
            if args.json_output:
                print(
                    json.dumps(
                        {
                            "saved": download_result.saved_files,
                            "skipped": download_result.skipped_files,
                            "failures": [
                                {
                                    "kind": item.kind,
                                    "exception": item.exception_type,
                                    "message": item.message,
                                }
                                for item in download_result.failures
                            ],
                        },
                        ensure_ascii=False,
                    )
                )
            if not download_result.failures:
                return EXIT_SUCCESS
            return EXIT_PARTIAL if download_result.saved_files or download_result.skipped_files else EXIT_FAILURE
        finally:
            with diagnostics_output:
                await service.close()
