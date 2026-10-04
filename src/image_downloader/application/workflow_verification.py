"""Compare a saved workflow selection with final attempts and current files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ..exceptions import ConfigurationError, StorageSafetyError
from ..models import WorkflowRunRecord
from ..privacy.log_safety import safe_url
from ..storage.filesystem import FileSystem
from ..storage.path_safety import canonical_path
from .log_verification import VerificationFileChangedError, issue, stable_file


def _files(root: Path, files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missing: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    try:
        canonical_path(root, "verification output root")
    except ConfigurationError:
        return missing, [issue("unsafe_or_unreadable_output_root")]
    filesystem = FileSystem(root)
    for file in files:
        relative = Path(file["path"])
        try:
            # Validate before probing, including paths through absent directories.
            filesystem._parts(relative)
            with stable_file(filesystem, relative) as stream:
                if not stream.read(1):
                    missing.append(issue("empty_file", path=file["path"]))
        except FileNotFoundError:
            missing.append(issue("missing_file", path=file["path"]))
        except VerificationFileChangedError:
            unknown.append(issue("file_changed_during_verification", path=file["path"]))
        except (OSError, StorageSafetyError):
            # Missing directories are missing artifacts; links and other unsafe nodes are unknown.
            current = root
            absent = False
            try:
                filesystem._parts(relative)
                for component in relative.parts:
                    current /= component
                    state = current.lstat()
                    if filesystem._is_unsafe(state):
                        break
            except FileNotFoundError:
                absent = True
            except (OSError, StorageSafetyError):
                pass
            (missing if absent else unknown).append(
                issue(
                    "missing_file" if absent else "unsafe_unreadable_or_changed_file",
                    path=file["path"],
                )
            )
    return missing, unknown


def verify_workflow(run: WorkflowRunRecord | None, root: Path) -> dict[str, Any]:
    unknown: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    if run is None:
        unknown.append(issue("workflow_history_unavailable"))
        return _result(results, errors, unknown)
    details = cast(dict[str, Any], run.details)
    evidence = details.get("verification")
    if not evidence:
        unknown.append(issue("verification_evidence_unavailable"))
        reference = [
            {"url": safe_url(item["url"]), "status": item["status"], "saved": item["saved"], "skipped": item["skipped"]}
            for item in details["items"]
        ]
        return dict(_result(results, errors, unknown), reference=reference)
    selected: dict[str, int] = {}
    rounds = evidence["rounds"]
    # The evidence must cover the recorded rounds and selection counts, not merely be nonempty.
    history_rounds = details["rounds"]
    if [r["round_number"] for r in rounds] != [r["round_number"] for r in history_rounds] or any(
        len(r["selected_url_keys"]) != len(h["selected_urls"])
        or r["selection_established"] != (h["checked_at"] is not None)
        for r, h in zip(rounds, history_rounds, strict=True)
    ):
        unknown.append(issue("inconsistent_selection_evidence"))
    if not rounds or not any(r["selection_established"] for r in rounds):
        errors.append(issue("selection_not_established"))
    for round_ in rounds:
        for key in round_["selected_url_keys"]:
            selected[key] = max(selected.get(key, 0), round_["round_number"])
    items: dict[str, dict[str, Any]] = {}
    for item in evidence["items"]:
        if item["url_key"] in items:
            unknown.append(issue("duplicate_url_evidence"))
        items[item["url_key"]] = item
    results = [_verify_target(root, key, number, items.get(key)) for key, number in selected.items()]
    if details["cancelled"] or details["timed_out"] or details["stop_error"] is not None:
        errors.append(issue("workflow_stopped"))
    return _result(results, errors, unknown)


def _verify_target(root: Path, key: str, number: int, item: dict[str, Any] | None) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    if item is None:
        uncertain.append(issue("url_evidence_unavailable"))
    elif item["round_number"] is None or item["round_number"] < number:
        failures.append(issue("selected_url_not_attempted"))
    elif item["status"] != "success":
        failures.append(issue("download_" + item["status"]))
    elif item["expected_images"] == 0:
        failures.append(issue("no_images"))
    elif not item["available"] or item["expected_images"] is None:
        uncertain.append(issue("file_evidence_unavailable"))
    elif len(item["files"]) != item["expected_images"]:
        failures.append(issue("incomplete_image_outcomes"))
    # Inspect every recorded file, including files saved in a failed/partial attempt.
    if item is not None:
        missing, unreadable = _files(root, item["files"])
        failures.extend(missing)
        uncertain.extend(unreadable)
    state = "indeterminate" if uncertain else "not_acquired" if failures else "acquired"
    if state == "acquired" and item and all(f["kind"] == "skipped" for f in item["files"]):
        state = "existing_files"
    return {
        "url_key": key,
        "url": safe_url(item["url"]) if item else None,
        "selected_round": number,
        "status": state,
        "expected_images": item["expected_images"] if item else None,
        "saved": sum(f["kind"] == "saved" for f in item["files"]) if item else 0,
        "skipped": sum(f["kind"] == "skipped" for f in item["files"]) if item else 0,
        "errors": failures,
        "indeterminate": uncertain,
    }


def _result(items: list[dict[str, Any]], errors: list[dict[str, Any]], unknown: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        s: sum(i["status"] == s for i in items) for s in ("acquired", "existing_files", "not_acquired", "indeterminate")
    }
    status = (
        "indeterminate"
        if unknown or counts["indeterminate"]
        else "failed"
        if errors or counts["not_acquired"]
        else "passed"
    )
    return {
        "status": status,
        "summary": dict(counts, selected=len(items)),
        "items": items,
        "errors": errors,
        "indeterminate": unknown,
        "no_targets": not items and status == "passed",
    }
