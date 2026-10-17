"""download CLI command implementation."""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext, redirect_stdout
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..application.requests import DownloadRequest, UpdateCheckRequest
from ..application.shutdown import close_service
from ..configuration.paths import resolve_paths
from ..models import UpdateChangeKind
from .constants import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS
from .download_payload import download_payload
from .inspect import inspect_command
from .prepared import DownloadArguments, PreparedCommand
from .setup import (
    _config_for,
    _fallback,
    _output_root,
    _persist_initial_user_config,
    _plugin_root,
)


class DownloadCommandHandler:
    async def handle(self, command: PreparedCommand) -> int:
        args = command.arguments
        assert isinstance(args, DownloadArguments)
        if args.inspect_only:
            return await inspect_command(command)
        hostname = urlparse(args.url).hostname
        overrides = command.plugin_config
        download_policy_overrides = command.download_policy
        plugin_id = args.plugin_id or args.force_plugin_id
        force_plugin = args.force_plugin_id is not None
        config, config_root, _, source = _config_for(
            args,
            hostname,
            rewrite_user_layers=True,
        )
        _persist_initial_user_config(source)
        custom_output_root = None if args.list_updated_urls else _output_root(args)
        output_root = custom_output_root or resolve_paths(config)["downloads"]
        service = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
            output_root=custom_output_root,
            plugin_verification_override=args.plugin_verification_override,
        ).compose()
        diagnostics_output = redirect_stdout(sys.stderr) if args.json_output else nullcontext()
        status = EXIT_FAILURE
        primary: BaseException | None = None
        try:
            if args.list_updated_urls:
                with diagnostics_output:
                    update_result = await service.check_updates(
                        UpdateCheckRequest(
                            url=args.url,
                            plugin_overrides=overrides,
                            fallback_override=_fallback(args),
                            plugin_id=plugin_id,
                            force_plugin=force_plugin,
                            plugin_download_policy_overrides=download_policy_overrides,
                        )
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
                status = EXIT_SUCCESS
            else:
                with diagnostics_output:
                    download_result = await service.download(
                        DownloadRequest(
                            url=args.url,
                            plugin_overrides=overrides,
                            fallback_override=_fallback(args),
                            plugin_id=plugin_id,
                            force_plugin=force_plugin,
                            plugin_download_policy_overrides=download_policy_overrides,
                            force_image_format=args.force_image_format,
                        )
                    )
                if args.json_output:
                    print(
                        json.dumps(
                            download_payload(download_result, output_root),
                            ensure_ascii=False,
                        )
                    )
                status = (
                    EXIT_SUCCESS
                    if not download_result.failures
                    else EXIT_PARTIAL
                    if download_result.saved_files or download_result.skipped_files
                    else EXIT_FAILURE
                )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            with diagnostics_output:
                await close_service(service, primary=primary)
        return status
