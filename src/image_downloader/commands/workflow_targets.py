"""Interactive URL state administration; approvals never hold storage locks."""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
from collections.abc import Collection
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..application.requests import WorkflowPlanRequest
from ..application.workflow_state import WorkflowStateService
from ..configuration.models import AppConfig
from ..models import (
    WorkflowTargetChange,
    WorkflowTargetChangePlan,
    WorkflowTargetRecord,
    WorkflowTargetSelector,
    WorkflowTargetStatus,
)
from ..privacy.log_safety import safe_url
from ..storage.transactions import settle_operation
from ..storage.workflow_targets import change_result
from .outcome import CommandOutcome
from .prepared import StateArguments
from .setup import _config_for, _plugin_root


def target_payload(row: WorkflowTargetRecord) -> dict[str, object]:
    return {
        "plugin_id": row.plugin_id,
        "feed_key": row.feed_key,
        **asdict(row.candidate),
        "url": safe_url(row.candidate.url),
        "status": row.status,
        "completed": row.completed,
    }


def _read_answer() -> str:
    # asyncio.run's first SIGINT cancels a task, which cannot unblock synchronous
    # stdin. Raise immediately during the prompt, then restore Runner's handler.
    main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.getsignal(signal.SIGINT) if main_thread else None
    try:
        if main_thread:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        return input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise asyncio.CancelledError() from None
    finally:
        if main_thread:
            signal.signal(signal.SIGINT, previous)


def confirm_changes(plan: WorkflowTargetChangePlan) -> frozenset[str]:
    groups: dict[str, list[WorkflowTargetChange]] = {}
    for change in plan.changes:
        if change.requires_write:
            groups.setdefault(change.after.candidate.url, []).append(change)
    approved: set[str] = set()
    all_yes = False
    for url, changes in groups.items():
        if all_yes:
            approved.add(url)
            continue
        print(f"Change {safe_url(url)} ({len(changes)} registrations):", file=sys.stderr)
        for change in changes:
            before = change.before.status if change.before is not None else "unregistered"
            row = change.after
            print(
                f"  [{row.plugin_id}] feed={row.feed_key}: {before} -> {row.status} "
                f"(content_id={row.candidate.content_id}, revision={row.candidate.revision})",
                file=sys.stderr,
            )
        while True:
            print("Apply? [yes/no/all-yes/all-no] ", end="", file=sys.stderr, flush=True)
            answer = _read_answer()
            if answer in {"yes", "no", "all-yes", "all-no"}:
                break
            print("Enter yes, no, all-yes or all-no.", file=sys.stderr)
        if answer == "all-no":
            break
        if answer in {"yes", "all-yes"}:
            approved.add(url)
        all_yes = answer == "all-yes"
    return frozenset(approved)


def _print_payload(payload: dict[str, Any]) -> None:
    if "targets" in payload:
        if not payload["targets"]:
            print("no workflow targets found")
        for row in payload["targets"]:
            print(f"{row['status']}: {row['url']} [{row['plugin_id']}] feed={row['feed_key']}")
        return
    for change in payload["changes"]:
        before, after = change["before"], change["after"]
        status = before["status"] if before is not None else "unregistered"
        print(f"{change['decision']}: {after['url']} {status} -> {after['status']}")
    print(" ".join(f"{name}={value}" for name, value in payload["summary"].items()), file=sys.stderr)
    if payload.get("not_found"):
        print("no matching workflow targets found", file=sys.stderr)


async def _refresh_plan(
    args: StateArguments,
    config: AppConfig,
    config_root: Path,
    selector: WorkflowTargetSelector,
    status: WorkflowTargetStatus | None,
    outcome: CommandOutcome,
) -> WorkflowTargetChangePlan | None:
    assert args.source_url is not None
    outcome.expect_shutdown()
    service = RuntimeComposer(
        config, config_root=config_root, plugin_root=_plugin_root(args, config)
    )._compose_for_workflow_plan()
    plan = None
    async with outcome.closing(service.close, handled=True):
        plan = await service.plan_workflow_targets(
            WorkflowPlanRequest(
                url=args.source_url, plugin_id=args.plugin_id, download_scope=args.download_scope or "updated"
            ),
            selector,
            status,
        )
        outcome.set_result({}, 0)  # Keep discovery success separate from a subsequent close failure.
    return plan if outcome.primary is None else None


def _changes_payload(
    plan: WorkflowTargetChangePlan,
    approved: Collection[str],
    *,
    dry_run: bool = False,
    cancelled: bool = False,
) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for change in plan.changes:
        decision = "unchanged"
        if change.requires_write:
            if cancelled:
                decision = "cancelled"
            elif dry_run:
                decision = "preview"
            else:
                decision = "approved" if change.after.candidate.url in approved else "skipped"
        changes.append(
            {
                "before": target_payload(change.before) if change.before is not None else None,
                "after": target_payload(change.after),
                "decision": decision,
            }
        )
    return changes


async def workflow_targets_command(args: StateArguments, outcome: CommandOutcome) -> int:
    action = args.command_args[1]
    selector = WorkflowTargetSelector(
        url=args.command_args[2] if len(args.command_args) > 2 else None,
        url_regex=args.url_regex,
        all_targets=args.all_targets,
        source_url=args.source_url,
        plugin_id=args.plugin_id,
        from_status=args.from_status,
    )
    config, config_root, _, _ = _config_for(
        args,
        urlparse(args.source_url).hostname if args.refresh and args.source_url is not None else None,
        rewrite_user_layers=False,
    )
    state = WorkflowStateService(config)
    payload: dict = {"operation": "state", "resource": "workflow", "action": action}
    status: WorkflowTargetStatus | None = "pending" if action == "reset" else args.status
    if args.refresh:
        plan = await _refresh_plan(args, config, config_root, selector, status, outcome)
        if plan is None:
            return outcome.exit_code
        rows = tuple(change.after for change in plan.changes)
    elif action == "targets":
        rows = await asyncio.to_thread(state.list_targets, selector)
        plan = None
    else:
        plan = await asyncio.to_thread(state.plan_changes, selector, status)
        rows = tuple(change.after for change in plan.changes)
    if action == "targets":
        payload["targets"] = [target_payload(row) for row in rows]
        return outcome.set_result(payload, 0, text=lambda: _print_payload(payload))
    assert plan is not None
    approved: frozenset[str] = frozenset()
    try:
        if not args.dry_run:
            approved = confirm_changes(plan)
    except asyncio.CancelledError as error:
        payload.update(
            cancelled=True,
            changes=_changes_payload(plan, (), cancelled=True),
            summary=asdict(change_result(plan)),
        )
        outcome.set_result(payload, 130, text=lambda: _print_payload(payload))
        outcome.fail(error)
        return 130
    cancellation = None
    if args.dry_run:
        result = change_result(plan, dry_run=True)
    else:
        settled = await settle_operation(asyncio.to_thread(partial(state.apply_changes, plan, approved)))
        if settled.error is not None:
            raise settled.error
        assert settled.value is not None
        result, cancellation = settled.value, settled.cancellation
    payload.update(
        changes=_changes_payload(plan, approved, dry_run=args.dry_run),
        summary=asdict(result),
    )
    if not plan.changes:
        payload["not_found"] = True
    if cancellation is not None:
        payload["cancelled"] = True
        outcome.set_result(payload, 130, text=lambda: _print_payload(payload))
        outcome.fail(cancellation)
        return 130
    return outcome.set_result(payload, 0 if plan.changes else 1, text=lambda: _print_payload(payload))
