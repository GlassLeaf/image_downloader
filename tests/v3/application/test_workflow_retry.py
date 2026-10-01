from __future__ import annotations

import asyncio
import io
import threading
from collections import Counter
from dataclasses import replace

import httpx
import pytest
from PIL import Image
from test_workflow import FEED, A, B, Gallery, compose

from image_downloader import (
    AuthenticationError,
    Chapter,
    DownloadManifest,
    ImageDecodeError,
    ImageResource,
    ImageSaveOptions,
    PluginError,
    RequestSpec,
    UpdateCandidate,
    WorkflowRetryTimeoutError,
)
from image_downloader.application.workflow_retry import ImageLedger, retryable
from image_downloader.commands.workflow import _status, workflow_payload
from image_downloader.config import Notification
from image_downloader.models import ImageOutcome, ImageOutcomeKind
from image_downloader.observability.events import EventName
from image_downloader.output.output_allocator import OutputAllocation
from image_downloader.storage.workflow import WorkflowState


class RetryGallery(Gallery):
    candidates = (A,)

    def __init__(self):
        super().__init__()
        self.requested = []
        self.manifests = {}
        self.failure = None

    async def inspect(self, url, context):
        self.seen.append(url)
        if self.failure is not None and self.checks == 1:
            raise self.failure
        return self.manifests.get((self.checks, url), self.manifests.get((1, url), manifest()))

    async def create_image_request(self, image, context):
        self.requested.append((self.checks, image.url))
        return RequestSpec(image.url, auth_required=False)


def manifest(*images):
    if not images:
        images = (
            ImageResource(A.url + "/good.png", image_id="good", index=1),
            ImageResource(A.url + "/bad.png", image_id="bad", index=2),
        )
    return DownloadManifest("gallery", (Chapter(1, "chapter", images=images, chapter_id="chapter"),))


async def transport(service, gallery, *, permanent=False):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "PNG")
    await service.gateway.client.aclose()

    def respond(request):
        failed = str(request.url).endswith("/bad.png") and (gallery.checks == 1 or permanent)
        return httpx.Response(503 if failed else 200, content=buffer.getvalue(), headers={"content-type": "image/png"})

    service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))


@pytest.mark.parametrize("scope", ["all", "updated"])
@pytest.mark.parametrize("policy", ["overwrite", "skip", "rename", "error"])
def test_retry_keeps_successful_images_and_recovers(tmp_path, scope, policy):
    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery, network={"max_attempts": 1}, existing_file=policy) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, download_scope=scope, workflow_retry_delay=0)
            assert result.items[0].status == "success"
            assert _status(result) == 0
            assert gallery.requested == [(1, A.url + "/good.png"), (1, A.url + "/bad.png"), (2, A.url + "/bad.png")]
            assert [a.status for a in result.items[0].attempts] == ["partial", "success"]
            assert [i.retained for i in result.items[0].attempts[1].images] == [True, False]
            assert (await service.workflow(FEED)).items == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [AuthenticationError(), PluginError(), ImageDecodeError()])
def test_retry_operation_failures(tmp_path, error):
    async def scenario():
        gallery = RetryGallery()
        gallery.failure = error
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert [a.status for a in result.items[0].attempts] == ["failed", "success"]
            assert gallery.checks == 2

    asyncio.run(scenario())


def test_retry_limits_zero_and_multiple_rounds(tmp_path):
    async def scenario():
        for retries in (0, 2):
            gallery = RetryGallery()
            (tmp_path / str(retries)).mkdir()
            async with compose(tmp_path / str(retries), gallery, network={"max_attempts": 1}) as service:
                await transport(service, gallery, permanent=True)
                result = await service.workflow(FEED, workflow_retries=retries, workflow_retry_delay=0)
                assert len(result.rounds) == retries + 1
                assert result.items[0].status == "partial"
                assert _status(result) == 5
                assert Counter(url for _, url in gallery.requested)[A.url + "/good.png"] == 1

    asyncio.run(scenario())


def test_retry_refreshes_feed_and_changed_images(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        original = manifest()
        gallery.manifests[2, A.url] = manifest(
            replace(original.chapters[0].images[0], metadata={"revision": "2"}),
            original.chapters[0].images[1],
            ImageResource(A.url + "/new.png", index=3),
        )
        original_check = gallery.check_updates

        async def check(url, context):
            gallery.candidates = (A,) if gallery.checks == 0 else (UpdateCandidate(A.url, "a", "2"), B)
            return await original_check(url, context)

        gallery.check_updates = check
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert result.rounds[1].selected_urls == (A.url, B.url)
            assert all(item.status == "success" for item in result.items)
            assert (2, A.url + "/good.png") in gallery.requested
            assert (2, A.url + "/new.png") in gallery.requested

    asyncio.run(scenario())


def test_retry_removes_failed_url_without_download(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        original_check = gallery.check_updates

        async def check(url, context):
            if gallery.checks:
                gallery.candidates = ()
            return await original_check(url, context)

        gallery.check_updates = check
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert result.items[0].status == "removed"
            assert result.rounds[1].removed_urls == (A.url,)
            assert _status(result) == 0
            assert gallery.seen == [A.url]
            state = WorkflowState(service.state.filesystem)._read()
            assert not next(iter(next(iter(state["sources"].values())).values()))["records"]

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["wait", "check", "download"])
def test_retry_deadline_keeps_results_and_is_not_user_cancellation(tmp_path, phase):
    async def scenario():
        gallery = RetryGallery()
        original_check, original_inspect = gallery.check_updates, gallery.inspect

        async def check(url, context):
            if gallery.checks and phase == "check":
                await asyncio.Event().wait()
            return await original_check(url, context)

        async def inspect(url, context):
            if gallery.checks > 1 and phase == "download":
                await asyncio.Event().wait()
            return await original_inspect(url, context)

        gallery.check_updates, gallery.inspect = check, inspect
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            with pytest.raises(WorkflowRetryTimeoutError) as caught:
                await service.workflow(
                    FEED, workflow_retry_delay=600 if phase == "wait" else 0, workflow_retry_timeout=0.03
                )
            result = caught.value.workflow_result
            assert result.timed_out and not result.cancelled
            assert result.stop_error.code == "workflow_retry_timeout"
            assert _status(result) == 5
            assert len(result.items[0].download.saved_files) == 1
            assert result.rounds[-1].error.code == "workflow_retry_timeout"
            assert workflow_payload(result, tmp_path)["timed_out"] is True

    asyncio.run(scenario())


def test_ledger_matches_identity_and_ignores_manifest_revision():
    ledger = ImageLedger(A.url)
    old = manifest()
    ledger.begin(old, "plugin")
    for ip, image in enumerate(old.chapters[0].images):
        ledger.record(0, ip, ImageOutcome(image, ImageOutcomeKind.SAVED, "saved.png"))
    ledger.begin(replace(old, revision="new"), "plugin")
    assert len(ledger.retained) == 2
    ledger.begin(manifest(*reversed(old.chapters[0].images)), "plugin")
    assert len(ledger.retained) == 2
    ledger.begin(manifest(*old.chapters[0].images, old.chapters[0].images[0]), "plugin")
    assert ledger.retained == {(0, 1)}
    ledger.begin(old, "different-plugin")
    assert not ledger.retained


@pytest.mark.parametrize(
    "code,status,expected",
    [
        ("authentication_error", None, True),
        ("secret_not_found", None, False),
        ("plugin_error", None, True),
        ("unsupported_site_feature", None, False),
        ("image_decode_error", None, True),
        ("image_dimension_limit_error", None, False),
        ("http_status_error", 404, False),
        ("http_status_error", 403, True),
        ("http_status_error", 408, True),
        ("http_status_error", 503, True),
        ("storage_error", None, False),
        ("existing_file_conflict", None, False),
        ("redirect_policy_error", None, False),
        ("unexpected_runtime_error", None, False),
    ],
)
def test_retry_classification(code, status, expected):
    assert retryable(code, status) is expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workflow_retries": -1},
        {"workflow_retries": True},
        {"workflow_retries": 1.5},
        {"workflow_retry_delay": float("nan")},
        {"workflow_retry_delay": -1},
        {"workflow_retry_timeout": float("inf")},
        {"workflow_retry_timeout": 0},
    ],
)
def test_retry_api_validates_before_start(tmp_path, kwargs):
    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery) as service:
            with pytest.raises(ValueError):
                await service.workflow(FEED, **kwargs)
            assert not gallery.checks

    asyncio.run(scenario())


def test_cleanup_retry_preserves_saved_images(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))

        def cleanup():
            if gallery.seen and gallery.checks == 1:
                raise RuntimeError("cleanup failed")

        gallery.cleanup_after_use = cleanup
        async with compose(tmp_path, gallery) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert result.items[0].status == "success"
            assert gallery.requested == [(1, A.url + "/good.png")]
            assert result.items[0].attempts[1].images[0].retained

    asyncio.run(scenario())


def test_fail_fast_keeps_concurrent_success_and_retries_unprocessed_images(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(
            ImageResource(A.url + "/good.png", index=1),
            ImageResource(A.url + "/bad.png", index=2),
            ImageResource(A.url + "/third.png", index=3),
        )
        async with compose(
            tmp_path,
            gallery,
            network={"max_attempts": 1},
            download={
                "continue_on_image_error": False,
                "image_concurrency_per_chapter": 2,
            },
        ) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            first = result.items[0].attempts[0]
            assert len(first.download.saved_files) == 1
            assert first.images[2].status == "unprocessed"
            assert gallery.requested.count((1, A.url + "/good.png")) == 1
            assert (2, A.url + "/good.png") not in gallery.requested
            assert result.items[0].status == "success"

    asyncio.run(scenario())


@pytest.mark.parametrize("permanent", [False, True])
def test_only_final_failures_are_notified_and_secrets_masked(tmp_path, permanent):
    async def scenario():
        gallery = RetryGallery()
        messages, events = [], []

        class Sender:
            async def send(self, title, message):
                messages.append(message)
                return True

        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            service.notifications.config = Notification(enabled=True, notify_on=("fetch_error", "download_success"))
            service.notifications.senders = {"desktop": Sender()}
            service.events.on(EventName.FETCH_FAILED, events.append)
            await transport(service, gallery, permanent=permanent)
            await service.workflow(FEED, workflow_retry_delay=0)
            assert len(events) == (2 if permanent else 1)
            assert len(messages) == 1
            assert ("image_fetch_failed: count=1" in messages[0]) is permanent
            assert ("download_success" in messages[0]) is not permanent

    asyncio.run(scenario())


def test_full_success_does_not_wait_or_refresh(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))
        async with compose(tmp_path, gallery) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED)
            assert gallery.checks == 1
            assert len(result.rounds) == 1
            assert result.workflow_retry_delay == 600

    asyncio.run(scenario())


def test_permanent_http_failure_does_not_wait(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery) as service:
            await service.gateway.client.aclose()
            service.gateway.client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(404))
            )
            result = await service.workflow(FEED)
            assert gallery.checks == 1
            assert _status(result) == 1

    asyncio.run(scenario())


def test_internal_image_error_stops_without_retry(tmp_path, monkeypatch):
    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery) as service:

            async def broken(*args, **kwargs):
                raise RuntimeError("private core error")

            monkeypatch.setattr(service.gateway, "_transport", broken)
            with pytest.raises(RuntimeError) as caught:
                await service.workflow(FEED)
            assert caught.value.workflow_result.stop_error.code == "unexpected_runtime_error"
            assert gallery.checks == 1

    asyncio.run(scenario())


def test_retry_feed_uses_fresh_instances_after_cleanup(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        instances = []

        class Fresh:
            closed = False

            def __init__(self):
                instances.append(self)

            def auth_flow(self, context):
                assert not self.closed
                return None

            async def check_updates(self, url, context):
                assert not self.closed
                return await gallery.check_updates(url, context)

            async def inspect(self, url, context):
                assert not self.closed
                return await gallery.inspect(url, context)

            async def create_image_request(self, image, context):
                assert not self.closed
                return await gallery.create_image_request(image, context)

            async def transform_image(self, artifact, context):
                return artifact

            async def recover_image_request(self, image, failed, response, context):
                return None

            def cleanup_after_use(self):
                assert not self.closed
                self.closed = True

        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            record = service.registry.records["core.generic-html"]
            service.registry.resolve = lambda *_args, **_kwargs: (record, Fresh())
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert result.items[0].status == "success"
            assert len(instances) == 4
            assert all(instance.closed for instance in instances)

    asyncio.run(scenario())


def test_retry_wait_holds_feed_lock_and_cancellation_releases_it(tmp_path, monkeypatch):
    async def scenario():
        first, second = RetryGallery(), RetryGallery()
        started = asyncio.Event()
        original_sleep = asyncio.sleep

        async def sleep(delay):
            if delay == 600:
                started.set()
                await asyncio.Event().wait()
            else:
                await original_sleep(delay)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        async with (
            compose(tmp_path, first, network={"max_attempts": 1}) as one,
            compose(
                tmp_path,
                second,
                network={"max_attempts": 1},
            ) as two,
        ):
            await transport(one, first)
            await transport(two, second)
            task1 = asyncio.create_task(one.workflow(FEED))
            await asyncio.wait_for(started.wait(), 10)
            task2 = asyncio.create_task(two.workflow(FEED, workflow_retries=0))
            await original_sleep(0.05)
            assert second.checks == 0
            task1.cancel()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task1
            assert caught.value.workflow_result.cancelled
            assert not caught.value.workflow_result.timed_out
            await asyncio.wait_for(task2, 10)
            assert second.checks == 1

    asyncio.run(scenario())


def test_feed_republication_clears_removed_image_ledger(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.candidates = (A, B)
        old_check = gallery.check_updates

        async def check(url, context):
            gallery.candidates = (B,) if gallery.checks == 1 else (A, B)
            return await old_check(url, context)

        gallery.check_updates = check
        async with compose(tmp_path, gallery, network={"max_attempts": 1}, existing_file="rename") as service:
            await transport(service, gallery, permanent=True)
            result = await service.workflow(FEED, workflow_retries=2, workflow_retry_delay=0)
            assert result.rounds[1].removed_urls == (A.url,)
            assert result.rounds[2].selected_urls == (A.url, B.url)
            assert (3, A.url + "/good.png") in gallery.requested
            assert (2, A.url + "/good.png") not in gallery.requested

    asyncio.run(scenario())


def test_changed_completed_url_is_checked_on_retry(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.candidates = (A, B)
        good = ImageResource(A.url + "/good.png", image_id="same")
        gallery.manifests[1, A.url] = manifest(good)
        gallery.manifests[2, A.url] = manifest(replace(good, metadata={"version": "2"}))
        old_check = gallery.check_updates

        async def check(url, context):
            if gallery.checks:
                gallery.candidates = (UpdateCandidate(A.url, "a", "2"), B)
            return await old_check(url, context)

        gallery.check_updates = check
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert (2, A.url + "/good.png") in gallery.requested
            assert result.items[0].attempts[0].status == "success"
            assert all(item.status == "success" for item in result.items)

    asyncio.run(scenario())


def test_final_notification_and_attempt_json_mask_opaque_locators(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(ImageResource("opaque?token=private-locator-secret"))
        messages = []

        class Sender:
            async def send(self, title, message):
                messages.append(message)
                return True

        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            service.notifications.config = Notification(enabled=True, notify_on=("fetch_error",))
            service.notifications.senders = {"desktop": Sender()}

            async def create(image, context):
                raise AuthenticationError("private-secret")

            gallery.create_image_request = create
            result = await service.workflow(FEED, workflow_retry_delay=0)
            payload = workflow_payload(result, service.outputs.root)
            assert "private-locator-secret" not in str(payload)
            assert "private-locator-secret" not in str(messages)
            assert "private-secret" not in str(messages)
            assert len(messages) == 1
            assert payload["items"][0]["attempts"][1]["images"][0]["failure"]["code"] == "authentication_error"

    asyncio.run(scenario())


def test_nonretryable_fail_fast_still_retries_unprocessed_images(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        old_manifest = manifest()
        async with compose(
            tmp_path,
            gallery,
            download={
                "continue_on_image_error": False,
                "image_concurrency_per_chapter": 1,
            },
        ) as service:
            gallery.manifests[1, A.url] = manifest(*reversed(old_manifest.chapters[0].images))
            await transport(service, gallery, permanent=True)
            old_request = gallery.create_image_request

            async def create(image, context):
                if image.url.endswith("/bad.png"):
                    from image_downloader import HttpStatusError

                    raise HttpStatusError(404)
                return await old_request(image, context)

            gallery.create_image_request = create
            result = await service.workflow(FEED, workflow_retry_delay=0)
            assert gallery.checks == 2
            assert result.items[0].status == "partial"
            assert (2, A.url + "/good.png") in gallery.requested
            assert result.items[0].attempts[1].images[0].retained

    asyncio.run(scenario())


def test_save_os_error_is_not_retried(tmp_path, monkeypatch):
    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery) as service:
            await transport(service, gallery)
            old_write = service.outputs.write_bytes_atomic

            def write(relative, data):
                if str(relative).endswith(".png"):
                    raise OSError("disk error")
                return old_write(relative, data)

            monkeypatch.setattr(service.outputs, "write_bytes_atomic", write)
            # Use a successful single image so there is no retryable transport failure.
            gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))
            result = await service.workflow(FEED)
            assert gallery.checks == 1
            assert result.items[0].download.failures[0].code == "storage_error"
            assert _status(result) == 1

    asyncio.run(scenario())


def test_cancellation_during_output_commit_keeps_settled_success(tmp_path, monkeypatch):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))
        started, release = asyncio.Event(), asyncio.Event()
        original = OutputAllocation.commit

        async def commit(allocation):
            started.set()
            await release.wait()
            await original(allocation)

        monkeypatch.setattr(OutputAllocation, "commit", commit)
        async with compose(tmp_path, gallery) as service:
            await transport(service, gallery)
            task = asyncio.create_task(service.workflow(FEED, workflow_retries=0))
            await asyncio.wait_for(started.wait(), 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            result = caught.value.workflow_result
            assert len(result.items[0].download.saved_files) == 1
            assert result.items[0].attempts[0].images[0].status == "saved"

    asyncio.run(scenario())


def test_deadline_during_state_commit_finishes_transaction(tmp_path, monkeypatch):
    async def scenario():
        gallery = RetryGallery()
        started, release = threading.Event(), threading.Event()
        original = WorkflowState.complete

        def complete(state, *args):
            started.set()
            assert release.wait(5)
            original(state, *args)

        monkeypatch.setattr(WorkflowState, "complete", complete)
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            task = asyncio.create_task(service.workflow(FEED, workflow_retry_delay=0, workflow_retry_timeout=0.5))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                await asyncio.sleep(0.6)
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(WorkflowRetryTimeoutError) as caught:
                await task
            assert caught.value.workflow_result.items[0].status == "success"
            assert (await service.workflow(FEED)).items == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("force,extension", [(None, ".jpeg"), ("WEBP", ".webp")])
def test_retry_preserves_image_format_precedence(tmp_path, force, extension):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(
            *(replace(image, save_options=ImageSaveOptions(format="JPEG")) for image in manifest().chapters[0].images)
        )
        async with compose(tmp_path, gallery, network={"max_attempts": 1}, image_format="PNG") as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0, force_image_format=force)
            assert all(path.endswith(extension) for path in result.items[0].download.saved_files)
            assert result.items[0].attempts[1].images[0].retained

    asyncio.run(scenario())


def test_cancellation_during_final_notification_keeps_completed_results(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))
        started = asyncio.Event()

        class Sender:
            async def send(self, title, message):
                started.set()
                await asyncio.Event().wait()

        async with compose(tmp_path, gallery) as service:
            await transport(service, gallery)
            service.notifications.config = Notification(enabled=True, notify_on=("download_success",))
            service.notifications.senders = {"desktop": Sender()}
            task = asyncio.create_task(service.workflow(FEED))
            await asyncio.wait_for(started.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            result = caught.value.workflow_result
            assert result.cancelled
            assert result.items[0].status == "success"
            assert result.items[0].download.saved_files

    asyncio.run(scenario())
