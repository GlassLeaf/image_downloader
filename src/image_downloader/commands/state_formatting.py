"""Human-readable offline state, run and retention reports."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from ..application.workflow_reporting import history_outcome
from ..application.workflow_selection import FIRST_RUN_NOTE, selection_explanation


def counts_text(counts: Mapping[str, object]) -> str:
    return " ".join(f"{name}={value}" for name, value in counts.items())


def print_state(payload: Mapping[str, Any]) -> None:
    if payload.get("not_found"):
        print("workflow state or run not found", file=sys.stderr)
        return
    if "workflows" in payload:
        if not payload["workflows"]:
            print("no workflow state or history found")
        for view in payload["workflows"]:
            _print_feed(view, detailed=payload["action"] == "show")
        if payload["action"] == "list":
            print("Run results shown above are limited to the latest saved run per feed.")
            print("View earlier saved runs: py -m image_downloader state workflow history [URL]")
    elif "runs" in payload:
        if not payload["runs"]:
            print("no saved workflow runs found")
        for run in payload["runs"]:
            _print_run(run, detailed=False)
    elif "run" in payload:
        _print_run(payload["run"], detailed=True)
    else:
        _print_prune(payload["prune"])
    if payload["action"] in {"list", "show", "history", "run"}:
        print(FIRST_RUN_NOTE)


def _print_feed(view: Mapping[str, Any], *, detailed: bool) -> None:
    print(f"feed [{view['plugin_id'] or 'unresolved'}]: {view['feed_key']}")
    if view["source_url"]:
        print(f"  source: {view['source_url']}")
    if view["state_available"]:
        print(f"  current state (checked {view['checked_at']}): {counts_text(view['summary'])}")
        if detailed:
            for item in view["items"]:
                print(f"    {'completed' if item['completed'] else 'unfinished'}: {item['url']}")
                identity = item["content_id"] if item["content_id"] is not None else "not provided"
                revision = item["revision"] if item["revision"] is not None else "not provided"
                print(f"      content_id: {identity}; revision: {revision}")
    else:
        print("  current state: not available")
    run = view["latest_run"]
    if run is None:
        print("  latest saved run: none")
    else:
        print(f"  latest saved run: {run['run_id']} (exit {run['exit_code']}, {history_outcome(run['details'])})")
        if detailed:
            print("  past execution result:")
            _print_run(run, detailed=True)
        else:
            _print_selection(run["details"], detailed=False)


def _print_error(error: Mapping[str, Any] | None, *, prefix: str) -> None:
    if error is None:
        return
    print(f"{prefix}error [{error['code']}]: {error['reason']}")
    for field in ("response_url", "http_status", "output_path"):
        if error.get(field) is not None:
            print(f"{prefix}  {field}: {error[field]}")


def _print_run(run: Mapping[str, Any], *, detailed: bool) -> None:
    details = run["details"]
    print(
        f"run {run['run_id']} [{run['plugin_id'] or 'unresolved'}]: "
        f"{history_outcome(details)} (exit {run['exit_code']}, ended {run['ended_at']})"
    )
    print(f"  source: {run['source_url']}")
    _print_selection(details, detailed=detailed)
    print(f"  URL results: {counts_text(details['summary'])}")
    if detailed:
        print(f"  started: {run['started_at']}; scope: {details['download_scope']}")
        timeout = details["workflow_retry_timeout"]
        print(
            f"  retries: {details['workflow_retries']}; delay: {details['workflow_retry_delay']} seconds; "
            f"timeout: {str(timeout) + ' seconds' if timeout is not None else 'unlimited'}"
        )
        for item in details["items"]:
            _print_item(item)
        for round_result in details["rounds"]:
            _print_round(round_result)
    _print_error(details["stop_error"], prefix="  stop ")


def _print_selection(details: Mapping[str, Any], *, detailed: bool) -> None:
    lines = selection_explanation(details)
    if detailed:
        for line in lines:
            print("  " + line)
    else:
        print("  " + " | ".join(lines))


def _print_item(item: Mapping[str, Any]) -> None:
    print(f"  {item['status']}: {item['url']} (reasons: {', '.join(item['reasons'])})")
    print("    " + counts_text({name: item[name] for name in ("saved", "skipped", "failures")}))
    _print_error(item["error"], prefix="    ")
    for attempt in item["attempts"]:
        counts = counts_text({name: attempt[name] for name in ("saved", "skipped", "failures")})
        print(f"    attempt round {attempt['round_number']}: {attempt['status']} {counts}")
        _print_error(attempt["error"], prefix="      ")
        for group in attempt["failure_groups"]:
            print(
                f"      image failures: kind={group['kind']} "
                f"code={group['code'] or 'unspecified'} count={group['count']}"
            )
            for field in ("response_url", "http_status", "output_path"):
                if group[field] is not None:
                    print(f"        {field}: {group[field]}")


def _print_round(round_result: Mapping[str, Any]) -> None:
    print(f"  round {round_result['round_number']}: {round_result['status']}")
    if round_result["checked_at"] is None:
        print("    update check result: not established")
    else:
        print(f"    checked: {round_result['checked_at']}; candidates: {round_result['candidate_count']}")
    print(f"    URL attempts: {counts_text(round_result['attempt_summary'])}")
    for label, field in (("selected", "selected_urls"), ("removed", "removed_urls")):
        for url in round_result[field]:
            print(f"    {label}: {url}")
    for change in round_result["changes"]:
        print(f"    change {change['kind']}: {change['url']}")
    _print_error(round_result["error"], prefix="    ")


def _print_prune(pruned: Mapping[str, Any]) -> None:
    print(f"history prune: {'preview' if pruned['dry_run'] else 'completed'}")
    print(f"  deleted runs: {len(pruned['deleted_run_ids'])}; expired runs: {pruned['expired_count']}")
    print(f"  UTF-8 bytes: {pruned['before_bytes']} -> {pruned['after_bytes']}")
    print(f"  size limit exceeded: {'yes' if pruned['over_limit'] else 'no'}")
    for run_id in pruned["deleted_run_ids"]:
        print(f"  {'would delete' if pruned['dry_run'] else 'deleted'}: {run_id}")
