from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_workflow import FEED, Gallery, attach_transport, compose

from image_downloader.application.workflow_reporting import history_details
from image_downloader.application.workflow_state import WorkflowStateService
from image_downloader.application.workflow_verification import verify_workflow
from image_downloader.immutable import thaw_json
from image_downloader.models import (
    Chapter,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
    UpdateSnapshot,
    WorkflowAttemptResult,
    WorkflowItemResult,
    WorkflowResult,
    WorkflowRoundResult,
    WorkflowRunRecord,
)
from image_downloader.storage.workflow_history_schema import HistoryDetails

URL = "https://example.test/gallery?token=private"


def make_run(root, *, kind="saved", status="success", expected=1, attempted=True, filename=None, url=URL):
    now = datetime.now(UTC)
    images = tuple(ImageResource(f"opaque:{i}", i + 1) for i in range(expected))
    chapter = Chapter(1, "chapter", images=images)
    download = DownloadResult(
        url,
        DownloadManifest("gallery", (chapter,)),
        (
            ChapterResult(
                chapter,
                tuple(
                    ImageOutcome(image, ImageOutcomeKind(kind), str(root / (filename or f"{i}.png")))
                    for i, image in enumerate(images)
                ),
            ),
        ),
    )
    attempts = (WorkflowAttemptResult(0, status, download),) if attempted else ()
    item = WorkflowItemResult(url, ("added",), status, download if attempted else None, attempts=attempts)
    snapshot = UpdateSnapshot(FEED, (), now)
    result = WorkflowResult(
        FEED, "updated", snapshot, (), (item,), rounds=(WorkflowRoundResult(0, snapshot, selected_urls=(url,)),)
    )
    for i in range(expected):
        (root / (filename or f"{i}.png")).write_bytes(b"presence only, not image decoding")
    return WorkflowRunRecord(str(uuid4()), "test", "a" * 64, FEED, now, now, 0, history_details(result, root))


@pytest.mark.parametrize("kind,state", [("saved", "acquired"), ("skipped", "existing_files")])
def test_saved_and_skipped_files_pass(tmp_path, kind, state):
    run = make_run(tmp_path, kind=kind)
    HistoryDetails.model_validate(thaw_json(run.details))
    result = verify_workflow(run, tmp_path)
    assert result["status"] == "passed"
    assert result["items"][0]["status"] == state
    assert "private" not in str(run.details)


@pytest.mark.parametrize("status", ["partial", "failed", "unprocessed"])
def test_unsuccessful_attempt_is_not_acquired(tmp_path, status):
    result = verify_workflow(make_run(tmp_path, status=status), tmp_path)
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "not_acquired"


@pytest.mark.parametrize("change", ["delete", "empty"])
def test_successful_history_does_not_hide_missing_or_empty_file(tmp_path, change):
    run = make_run(tmp_path)
    path = tmp_path / "0.png"
    if change == "delete":
        path.unlink()
    else:
        path.write_bytes(b"")
    assert verify_workflow(run, tmp_path)["status"] == "failed"


def test_zero_images_and_not_attempted(tmp_path):
    assert verify_workflow(make_run(tmp_path, expected=0), tmp_path)["items"][0]["errors"][0]["code"] == "no_images"
    result = verify_workflow(make_run(tmp_path, attempted=False), tmp_path)
    assert result["status"] == "failed"
    assert result["items"][0]["errors"][0]["code"] == "selected_url_not_attempted"


def test_old_missing_and_incomplete_evidence(tmp_path):
    run = make_run(tmp_path)
    old = thaw_json(run.details)
    old.pop("verification")
    HistoryDetails.model_validate(old)
    result = verify_workflow(replace(run, details=old), tmp_path)
    assert result["status"] == "indeterminate"
    assert result["reference"][0]["status"] == "success"
    assert verify_workflow(None, tmp_path)["status"] == "indeterminate"
    details = thaw_json(run.details)
    details["verification"]["items"][0]["available"] = False
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "indeterminate"


def test_reselection_requires_later_attempt_and_removed_does_not_exempt(tmp_path):
    run = make_run(tmp_path)
    details = thaw_json(run.details)
    evidence = details["verification"]
    key = evidence["items"][0]["url_key"]
    evidence["rounds"].append(dict(evidence["rounds"][0], round_number=1))
    details["rounds"].append(dict(details["rounds"][0], round_number=1))
    details["items"][0]["status"] = "removed"
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "failed"
    evidence["items"][0]["round_number"] = 1
    evidence["items"][0]["url_key"] = key
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "passed"


def test_selection_empty_and_not_established_and_stopped(tmp_path):
    run = make_run(tmp_path)
    details = thaw_json(run.details)
    details["verification"]["rounds"][0]["selected_url_keys"] = []
    details["rounds"][0]["selected_urls"] = []
    assert verify_workflow(replace(run, details=details), tmp_path)["no_targets"]
    details["verification"]["rounds"][0]["selection_established"] = False
    details["rounds"][0]["checked_at"] = None
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "failed"
    details["cancelled"] = True
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "failed"


@pytest.mark.parametrize("path", ["../escape.png", "token=secret.png"])
def test_unsafe_or_redacted_recording_path_is_unavailable(tmp_path, path):
    run = make_run(tmp_path)
    details = thaw_json(run.details)
    # Tampered persisted paths must also fail closed at the verifier boundary.
    details["verification"]["items"][0]["files"][0]["path"] = path
    result = verify_workflow(replace(run, details=details), tmp_path)
    assert result["status"] != "passed"
    assert "secret" not in str(result)


def test_actual_workflow_roundtrip_and_query_collision(tmp_path):
    async def scenario():
        gallery = Gallery()
        from image_downloader.models import UpdateCandidate

        gallery.candidates = (UpdateCandidate(URL), UpdateCandidate(URL.replace("private", "other")))
        original_inspect = gallery.inspect

        async def inspect(url, context):
            manifest = await original_inspect(url, context)
            return replace(manifest, title="one" if url == URL else "two")

        gallery.inspect = inspect
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            assert result.history_saved
            run = WorkflowStateService(service.config).get_run(result.run_id)
            evidence = run.details["verification"]
            assert len({i["url_key"] for i in evidence["items"]}) == 2
            assert len({i["url"] for i in evidence["items"]}) == 1
            verified = verify_workflow(run, service.outputs.root)
            assert verified["status"] == "passed"
            assert verified["summary"]["selected"] == 2

    asyncio.run(scenario())


def test_sensitive_recording_path_is_not_persisted(tmp_path):
    run = make_run(tmp_path, filename="token=secret.png")
    item = run.details["verification"]["items"][0]
    assert not item["available"]
    assert not item["files"]
    assert "secret" not in str(run.details)
    assert verify_workflow(run, tmp_path)["status"] == "indeterminate"


@pytest.mark.parametrize("flag", ["cancelled", "timed_out"])
def test_stopped_run_still_reports_acquired_files(tmp_path, flag):
    run = make_run(tmp_path)
    details = thaw_json(run.details)
    details[flag] = True
    result = verify_workflow(replace(run, details=details), tmp_path)
    assert result["status"] == "failed"
    assert result["summary"]["acquired"] == 1
    assert result["errors"][0]["code"] == "workflow_stopped"


def test_missing_outcomes_are_not_acquired(tmp_path):
    run = make_run(tmp_path, expected=2)
    details = thaw_json(run.details)
    details["verification"]["items"][0]["files"].pop()
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "failed"


def test_missing_parent_directory_and_unsafe_link(tmp_path):
    run = make_run(tmp_path)
    details = thaw_json(run.details)
    details["verification"]["items"][0]["files"][0]["path"] = "missing/0.png"
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "failed"
    try:
        (tmp_path / "linked.png").symlink_to(tmp_path / "0.png")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    details["verification"]["items"][0]["files"][0]["path"] = "linked.png"
    assert verify_workflow(replace(run, details=details), tmp_path)["status"] == "indeterminate"


def test_retry_retains_successful_files_and_finishes_failed_image(tmp_path):
    import httpx

    from image_downloader.application.log_verification import verify_logs
    from image_downloader.models import UpdateCandidate

    async def scenario():
        gallery = Gallery()
        gallery.candidates = (UpdateCandidate("https://example.test/a"),)

        async def inspect(url, context):
            return DownloadManifest(
                "gallery",
                (
                    Chapter(
                        1,
                        "chapter",
                        images=(
                            ImageResource("https://example.test/one.png", 1),
                            ImageResource("https://example.test/two.png", 2),
                        ),
                    ),
                ),
            )

        gallery.inspect = inspect
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await attach_transport(service)
            success_transport = service.gateway.client._transport
            seen = []

            async def response(request):
                seen.append(request.url.path)
                if request.url.path == "/two.png" and seen.count("/two.png") == 1:
                    return httpx.Response(500, request=request)
                return await success_transport.handle_async_request(request)

            service.gateway.client._transport = httpx.MockTransport(response)
            result = await service.workflow(FEED, workflow_retries=1, workflow_retry_delay=0)
            run = WorkflowStateService(service.config).get_run(result.run_id)
            verified = verify_workflow(run, service.outputs.root)
            assert verified["status"] == "passed"
            assert verified["items"][0]["saved"] == 2
            assert seen.count("/one.png") == 1
            assert seen.count("/two.png") == 2
            assert verify_logs(service.outputs.root)["status"] == "failed"

    asyncio.run(scenario())


def test_union_includes_later_added_and_earlier_removed_urls(tmp_path):
    first = make_run(tmp_path)
    second = make_run(tmp_path, filename="second.png", url="https://example.test/second")
    details = thaw_json(first.details)
    later = thaw_json(second.details)
    later["verification"]["rounds"][0]["round_number"] = 1
    later["rounds"][0]["round_number"] = 1
    later["verification"]["items"][0]["round_number"] = 1
    details["items"][0]["status"] = "removed"
    details["items"].extend(later["items"])
    details["rounds"].extend(later["rounds"])
    details["verification"]["rounds"].extend(later["verification"]["rounds"])
    details["verification"]["items"].extend(later["verification"]["items"])
    run = replace(first, details=details)
    verified = verify_workflow(run, tmp_path)
    assert verified["summary"]["selected"] == 2
    assert verified["summary"]["acquired"] == 2
    (tmp_path / "0.png").unlink()
    verified = verify_workflow(run, tmp_path)
    assert verified["summary"]["not_acquired"] == 1
    assert verified["summary"]["acquired"] == 1
