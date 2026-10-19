"""download CLI command implementation."""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..application.requests import DownloadRequest, UpdateCheckRequest
from ..configuration.paths import resolve_paths
from ..models import UpdateChangeKind
from .constants import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS
from .download_payload import download_payload
from .inspect import inspect_command
from .outcome import CommandOutcome
from .prepared import DownloadArguments, PreparedCommand
from .setup import (
    _config_for,
    _fallback,
    _output_root,
    _persist_initial_user_config,
    _plugin_root,
)


class DownloadCommandHandler:
    async def handle(self, command: PreparedCommand, outcome: CommandOutcome) -> int:
        args = command.arguments
        assert isinstance(args, DownloadArguments)
        if args.inspect_only:
            return await inspect_command(command, outcome)
        outcome.expect_shutdown()
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
        status = EXIT_FAILURE
        async with outcome.closing(service.close):
            if args.list_updated_urls:
                with outcome.diagnostics():
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

                def print_updates() -> None:
                    for change in visible:
                        print(change.url)
                    print(f"updated: {len(visible)}, removed: {removed}", file=sys.stderr)

                status = outcome.set_result(
                    {"updated_urls": [change.url for change in visible], "removed": removed},
                    EXIT_SUCCESS,
                    text=print_updates,
                )
            else:
                with outcome.diagnostics():
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
                status = (
                    EXIT_SUCCESS
                    if not download_result.failures
                    else EXIT_PARTIAL
                    if download_result.saved_files or download_result.skipped_files
                    else EXIT_FAILURE
                )
                outcome.set_result(
                    download_payload(download_result, output_root) if outcome.json_output else {}, status
                )
        return status
