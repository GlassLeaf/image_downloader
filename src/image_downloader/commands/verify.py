"""Offline verification CLI; never composes a download runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from ..application.log_verification import issue, verify_logs
from ..application.workflow_state import WorkflowStateService
from ..application.workflow_verification import verify_workflow
from ..exceptions import UpdateStateError
from .setup import _config_for


class VerifyCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        resource = args.verify_resource
        target = Path(args.target) if resource == "logs" else args.output_dir
        logs = await asyncio.to_thread(verify_logs, target)
        payload: dict[str, Any] = {"operation": "verify", "resource": resource, "logs": logs}
        states = [logs["status"]]
        if resource == "workflow":
            config, _, _, _ = _config_for(args, None, rewrite_user_layers=False)
            try:
                run = await asyncio.to_thread(WorkflowStateService(config).get_run, args.target)
                workflow = await asyncio.to_thread(verify_workflow, run, target)
            except (OSError, UpdateStateError):
                workflow = verify_workflow(None, target)
                workflow["indeterminate"] = [issue("workflow_history_unreadable")]
            payload.update(run_id=args.target, workflow=workflow)
            states.append(workflow["status"])
        status = "indeterminate" if "indeterminate" in states else "failed" if "failed" in states else "passed"
        payload["status"] = status
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            _print(payload)
        return {"passed": 0, "failed": 1, "indeterminate": 5}[status]


def _print(payload: dict[str, Any]) -> None:
    print(f"verify: {payload['status']}")
    print("Checks recorded log errors and current file presence; image contents and integrity are not verified.")
    workflow = payload.get("workflow")
    if workflow is not None:
        print(f"workflow: {payload['run_id']} ({workflow['status']})")
        print("  " + " ".join(f"{key}={value}" for key, value in workflow["summary"].items()))
        if workflow["no_targets"]:
            print("  no download targets selected")
        for item in workflow["items"]:
            print(
                f"  {item['status']}: {item['url'] or item['url_key']} saved={item['saved']} skipped={item['skipped']}"
            )
            _print_issues(item["errors"] + item["indeterminate"])
        _print_issues(workflow["errors"] + workflow["indeterminate"])
        for item in workflow.get("reference", []):
            print(f"  reference only: {item['url']} {item['status']} saved={item['saved']} skipped={item['skipped']}")
    logs = payload["logs"]
    print("logs: all history under the supplied target, including other runs")
    print("  " + " ".join(f"{key}={value}" for key, value in logs["summary"].items()))
    if logs["status"] == "passed":
        print("  no recorded log errors")
    for file in logs["files"]:
        _print_issues(file["errors"] + file["indeterminate"])
    _print_issues(logs["indeterminate"])


def _print_issues(issues: list[dict[str, Any]]) -> None:
    for item in issues:
        location = str(item["path"] or "-")
        if item["line"] is not None:
            location += ":" + str(item["line"])
        print(f"    {location}: {item['code']}: {item['message']}")
