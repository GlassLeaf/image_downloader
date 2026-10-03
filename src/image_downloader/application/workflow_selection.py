"""Explain initial selection using existing result/history facts, without new state."""

from collections.abc import Mapping
from typing import Any

from ..models import WorkflowResult

FIRST_RUN_NOTE = "First-run status is not recorded; added candidates do not necessarily indicate a first run."


def workflow_selection(result: WorkflowResult) -> tuple[str, ...]:
    """Project only selection facts; image results are unnecessary for this display."""
    return selection_explanation(
        {
            "download_scope": result.download_scope,
            "items": [{"url": i.url, "reasons": i.reasons} for i in result.items],
            "rounds": [
                {
                    "round_number": r.round_number,
                    "checked_at": r.snapshot.checked_at if r.snapshot else None,
                    "candidate_count": len(r.snapshot.candidates) if r.snapshot else 0,
                    "selected_urls": r.selected_urls,
                    "changes": [{"kind": c.kind.value, "url": c.url} for c in r.changes],
                }
                for r in result.rounds
            ],
        }
    )


def selection_explanation(details: Mapping[str, Any]) -> tuple[str, ...]:
    """Use round zero, never infer its reasons from merged later-round item reasons."""
    initial = next((r for r in details["rounds"] if r["round_number"] == 0), None)
    if initial is None:
        return ("Selection: initial selection details were not recorded",)
    if initial["checked_at"] is None:
        return ("Selection: update check and target selection were not established",)
    count = initial["candidate_count"]
    selected = initial["selected_urls"]
    selected_urls = set(selected)
    changes = initial["changes"]
    lines = [f"Initial selection: candidates={count} selected_urls={len(selected)}"]
    removed = sum(c["kind"] == "removed" for c in changes)
    reselected = {u for r in details["rounds"] if r["round_number"] > 0 for u in r["selected_urls"]}
    # Merged reasons remain initial-round facts only for URLs not selected again.
    known_reasons = {
        reason
        for item in details["items"]
        if item["url"] in selected_urls and item["url"] not in reselected
        for reason in item["reasons"]
    }
    reasons = _selection_reasons(details["download_scope"], count, selected, changes, known_reasons)
    lines.append("Selection: " + "; ".join(reasons))
    if removed:
        lines.append(f"Initial changes: removed_candidates={removed}; removed candidates are not downloaded")
    return tuple(lines)


def _selection_reasons(
    scope: str, count: int, selected: list[str] | tuple[str, ...], changes: list[Any], known_reasons: set[str]
) -> list[str]:
    if count == 0:
        return ["plugin returned no candidates; no downloads selected"]
    changed_urls = {c["url"] for c in changes if c["kind"] in {"added", "changed"}}
    if not selected:
        if scope == "updated" and not changed_urls:
            return ["current candidates are unchanged and previously completed; no downloads selected"]
        return ["selection reason was not recorded"]
    if scope == "all":
        return ["all current URLs selected by all scope"]
    selected_set = set(selected)
    reasons = []
    for kind, text in (
        ("added", "candidates absent from workflow comparison history selected"),
        ("changed", "candidates with changed URL or revision selected"),
    ):
        if any(c["kind"] == kind and c["url"] in selected_set for c in changes):
            reasons.append(text)
    if selected_set - changed_urls or "unfinished" in known_reasons:
        reasons.append("previously unfinished candidates selected")
    return reasons or ["selection reason was not recorded"]
