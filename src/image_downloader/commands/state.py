"""Offline workflow state/history display and explicit retention cleanup."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from ..application.workflow_recording import SIZE_WARNING
from ..application.workflow_state import WorkflowStateService
from ..exceptions import ConfigurationError
from ..models import WorkflowStateView
from ..storage.workflow_history import run_payload
from .setup import _config_for
from .state_formatting import print_state
from .validation import _reject_command_options


def state_payload(view: WorkflowStateView) -> dict[str, object]:
    return {
        "plugin_id": view.plugin_id,
        "feed_key": view.feed_key,
        "source_url": view.source_url,
        "state_available": view.checked_at is not None,
        "checked_at": view.checked_at.isoformat() if view.checked_at else None,
        "items": [{**asdict(i.candidate), "completed": i.completed} for i in view.items],
        "summary": {
            "candidates": len(view.items),
            "completed": sum(i.completed for i in view.items),
            "unfinished": sum(not i.completed for i in view.items),
        },
        "latest_run": run_payload(view.latest_run) if view.latest_run else None,
    }


class StateCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        _reject_explicit_state_options(args)
        _reject_command_options(
            args,
            (
                "force_plugin_id",
                "plugin_config",
                "plugin_config_file",
                "plugin_download_policy",
                "plugin_download_policy_file",
                "fallback_generic",
                "host",
                "no_console_log",
                "list_updated_urls",
                "inspect_only",
                "manifest_only",
                "export_cookies",
                "import_cookies",
                "import_browser_cookies",
                "existing_file",
                "image_format",
                "force_image_format",
                "output_dir",
                "directory_format",
                "inspection_data",
                "selection_priority",
                "plugin_root",
                "plugin_verification_override",
                "yes",
            ),
            "state",
        )
        tokens = args.command_args
        if not tokens or tokens[0] != "workflow":
            raise ConfigurationError("state requires workflow list|show|history|run|prune")
        action = tokens[1] if len(tokens) > 1 else "list"
        values = tokens[2:]
        if action not in {"list", "show", "history", "run", "prune"}:
            raise ConfigurationError("unknown workflow state action")
        if (
            (action in {"show", "run"} and len(values) != 1)
            or (action == "history" and len(values) > 1)
            or (action in {"list", "prune"} and values)
        ):
            raise ConfigurationError("invalid workflow state arguments")
        if args.dry_run and action != "prune":
            raise ConfigurationError("--dry-run is only valid for workflow or state workflow prune")
        limit = getattr(args, "limit", None)
        if limit is not None and (action != "history" or limit < 1):
            raise ConfigurationError("--limit requires history and a positive integer")
        if action in {"run", "prune"} and args.plugin_id is not None:
            raise ConfigurationError("--plugin is not valid for run or prune")
        config, _, _, _ = _config_for(args, None, rewrite_user_layers=False)
        service = WorkflowStateService(config)
        payload: dict[str, object] = {"operation": "state", "resource": "workflow", "action": action}
        status = 0
        if action in {"list", "show"}:
            if action == "show":
                views = await asyncio.to_thread(service.get_workflow, values[0], plugin_id=args.plugin_id)
            else:
                views = await asyncio.to_thread(service.list_workflows, plugin_id=args.plugin_id)
            payload["workflows"] = [state_payload(v) for v in views]
            status = 1 if action == "show" and not views else 0
        elif action == "history":
            runs = await asyncio.to_thread(service.list_runs, *values, plugin_id=args.plugin_id, limit=limit or 20)
            payload["runs"] = [run_payload(r) for r in runs]
            if values and not runs:
                found = await asyncio.to_thread(service.get_workflow, values[0], plugin_id=args.plugin_id)
                status = 0 if found else 1
        elif action == "run":
            run = await asyncio.to_thread(service.get_run, values[0])
            payload["run"] = run_payload(run) if run else None
            status = 0 if run else 1
        else:
            # A started atomic cleanup settles before cancellation propagates.
            from functools import partial

            from ..storage.workflow import finish_transaction

            pruned = await finish_transaction(partial(service.prune_history, dry_run=args.dry_run))
            payload["prune"] = asdict(pruned)
            if pruned.over_limit:
                print(f"warning: {SIZE_WARNING}", file=sys.stderr)
        if status:
            payload["not_found"] = True
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print_state(payload)
        return status


def _reject_explicit_state_options(args: argparse.Namespace) -> None:
    # Explicit default values (e.g. --selection-priority 0) still represent forbidden operations.
    allowed = {"--config", "--profile", "--data-root", "--json", "--plugin", "--limit", "--dry-run"}
    for option in sorted(getattr(args, "_explicit_options", ())):
        if option not in allowed:
            raise ConfigurationError(f"{option} is not valid for the state command")
