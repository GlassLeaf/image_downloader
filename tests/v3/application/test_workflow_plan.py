from __future__ import annotations

import asyncio
import multiprocessing
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import httpx
import pytest
from test_workflow import FEED, A, B, Gallery, attach_transport, compose, snapshot

from image_downloader import PluginError, RequestSpec, RuntimeComposer, UpdateCandidate, UpdateStateError
from image_downloader.application import workflow_plan
from image_downloader.storage import FileSystem
from image_downloader.storage.workflow import WorkflowState


def persistent_files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and p.suffix != ".lock"}


@pytest.mark.parametrize("scope", ["all", "updated"])
@pytest.mark.parametrize(
    "previous,current,complete",
    [
        (None, (A, B), ()),
        ((A, B), (B,), (A.url, B.url)),
        ((B,), (A, B), (B.url,)),
        ((A, B), (A, B), (A.url,)),
        ((A, B), (A, UpdateCandidate(B.url, "b", "2")), (A.url, B.url)),
        ((A, B), (), ()),
        ((), (A,), ()),
        ((A,), (A, UpdateCandidate(A.url, "alias", "1")), (A.url,)),
    ],
)
def test_preview_matches_preparation_without_mutating_history(tmp_path, scope, previous, current, complete):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = current
        async with compose(tmp_path, gallery) as service:
            state = WorkflowState(service.state.filesystem)
            if previous is not None:
                state.prepare("core.generic-html", FEED, snapshot(*previous), "updated")
                for url in complete:
                    state.complete("core.generic-html", FEED, url)
            # Listing history must not initialize or alter workflow selection.
            service.state.apply_snapshot("core.generic-html", FEED, snapshot(*current))
            before = persistent_files(tmp_path)
            first = await service.plan_workflow(FEED, download_scope=scope)
            second = await service.plan_workflow(FEED, download_scope=scope)
            assert first.selected_urls == second.selected_urls
            assert first.first_run is (previous is None)
            assert persistent_files(tmp_path) == before
            changes, selected = state.prepare("core.generic-html", FEED, first.snapshot, scope)
            assert first.selected_urls == tuple(selected)
            assert first.changes == changes
            assert all(i.reasons == selected.get(i.candidate.url, ("completed",)) for i in first.items)
            assert gallery.checks == 2 and gallery.cleanups == 2 and not gallery.seen
            with pytest.raises(FrozenInstanceError):
                first.cancelled = True

    asyncio.run(scenario())


def test_plans_do_not_consume_first_normal_workflow(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            plan = await service.plan_workflow(FEED)
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            assert result.selected_urls == plan.selected_urls == (A.url, B.url)
            assert all(item.status == "success" for item in result.items)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, "plugin", "cancel", "cleanup", "duplicate"])
def test_plan_session_is_detached_and_never_records_or_notifies(tmp_path, monkeypatch, failure):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            before = persistent_files(tmp_path)
            gateways = []
            gateway_type = workflow_plan.RequestGateway

            def gateway(*args, **kwargs):
                value = gateway_type(*args, **kwargs)
                gateways.append(value)
                return value

            monkeypatch.setattr(workflow_plan, "RequestGateway", gateway)

            async def check(url, context):
                gateways[0].client.cookies.set("temporary", "secret", domain="example.test")
                print("plugin stdout")
                if failure == "plugin":
                    raise PluginError("private failure")
                if failure == "cancel":
                    raise asyncio.CancelledError()
                return snapshot(A, A) if failure == "duplicate" else snapshot(A, B)

            def cleanup():
                gallery.cleanups += 1
                if failure == "cleanup":
                    raise PluginError("cleanup failed")

            async def forbidden(*args, **kwargs):
                raise AssertionError("planning must not emit notifications or download events")

            gallery.check_updates, gallery.cleanup_after_use = check, cleanup
            service.events.emit = forbidden
            service.notifications.flush = forbidden
            service.gateway.client.cookies.set("original", "value", domain="example.test")
            if failure:
                error_type = asyncio.CancelledError if failure == "cancel" else PluginError
                with pytest.raises(error_type) as caught:
                    await service.plan_workflow(FEED)
                result = caught.value.workflow_plan_result
                assert result.cancelled is (failure == "cancel")
                if failure in {"cleanup", "duplicate"}:
                    assert result.snapshot is not None
                if failure == "cleanup":
                    assert result.selected_urls == (A.url, B.url)
            else:
                result = await service.plan_workflow(FEED)
                assert result.selected_urls == (A.url, B.url)
            assert gallery.cleanups == 1 and not gallery.seen
            assert [c.name for c in service.gateway.client.cookies.jar] == ["original"]
            assert gateways[0].client.is_closed
            assert persistent_files(tmp_path) == before
            # Do not persist our test's deliberate mutation of the ordinary service.
            service.gateway.client.cookies.clear()

    asyncio.run(scenario())


@pytest.mark.parametrize("filename", ["updates.json", "workflow.json"])
@pytest.mark.parametrize("payload", [b"broken", b'{"schema_version": 99}'])
def test_plan_rejects_bad_history_before_check_without_overwrite(tmp_path, filename, payload):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            service.state.filesystem.write_bytes_atomic(filename, payload)
            before = persistent_files(tmp_path)
            with pytest.raises(UpdateStateError):
                await service.plan_workflow(FEED)
            assert persistent_files(tmp_path) == before
            assert gallery.checks == 0 and gallery.cleanups == 1

    asyncio.run(scenario())


def test_plan_same_feed_lock_blocks_check_and_cancellation_releases_it(tmp_path):
    async def scenario():
        first, second = Gallery(), Gallery()
        started, release = asyncio.Event(), asyncio.Event()

        async def check(url, context):
            started.set()
            await release.wait()
            return snapshot(A)

        first.check_updates = check
        async with compose(tmp_path, first) as service1, compose(tmp_path, second) as service2:
            task1 = asyncio.create_task(service1.plan_workflow(FEED))
            await asyncio.wait_for(started.wait(), 5)
            task2 = asyncio.create_task(service2.plan_workflow(FEED))
            await asyncio.sleep(0.1)
            assert second.checks == 0
            task1.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task1
            result = await asyncio.wait_for(task2, 5)
            assert result.first_run and second.checks == 1

    asyncio.run(scenario())


def test_planning_composition_close_never_persists_cookies_or_logs(tmp_path):
    async def scenario():
        gallery = Gallery()
        ordinary = compose(tmp_path, gallery)
        config = ordinary.config
        await ordinary.close()
        before = persistent_files(tmp_path)
        service = RuntimeComposer(
            config, config_root=tmp_path.resolve(), plugin_root=(tmp_path / "plugins").resolve()
        )._compose_for_workflow_plan()
        service.gateway.client.cookies.set("do-not-save", "secret", domain="example.test")
        await service.logger.core("preview diagnostic", module="workflow")
        await service.close()
        assert persistent_files(tmp_path) == before

    asyncio.run(scenario())


def test_plan_auth_refresh_uses_detached_cookie_session(tmp_path, monkeypatch):
    async def scenario():
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(
                401 if len(seen) == 1 else 200,
                headers={"Set-Cookie": "session=refreshed; Path=/"},
            )

        base = workflow_plan.RequestGateway

        class MockGateway(base):
            def __init__(self, config, jar):
                super().__init__(config, jar)
                self.original_client = self.client
                self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), cookies=jar)

            async def close(self):
                await super().close()
                await self.original_client.aclose()

        class Flow:
            token = "old"

            async def apply(self, request):
                return replace(request, headers={"Authorization": self.token})

            def is_auth_failure(self, request, response):
                return response.status == 401

            async def refresh(self, failed, response):
                self.token = "new"
                return replace(failed, headers={"Authorization": self.token})

        gallery = Gallery()
        gallery.auth_flow = lambda context: Flow()

        async def check(url, context):
            await context.requests.execute(RequestSpec(url))
            return snapshot(A)

        gallery.check_updates = check
        monkeypatch.setattr(workflow_plan, "RequestGateway", MockGateway)
        async with compose(tmp_path, gallery) as service:
            service.gateway.client.cookies.set("session", "original", domain="example.test", path="/")
            service._cookie_baseline = service.cookie_store.snapshot(service.gateway.client.cookies.jar)
            before = persistent_files(tmp_path)
            result = await service.plan_workflow(FEED)
            assert result.selected_urls == (A.url,)
            assert [r.headers["Authorization"] for r in seen] == ["old", "new"]
            assert "session=original" in seen[0].headers["cookie"]
            assert service.gateway.client.cookies.get("session") == "original"
            assert persistent_files(tmp_path) == before

    asyncio.run(scenario())


def _hold_preview_feed_lock(root, ready, release):
    state = WorkflowState(FileSystem(Path(root)))
    with state.feed_lock("core.generic-html", FEED):
        ready.set()
        assert release.wait(15)


def test_plan_waits_for_other_process_feed_lock_but_not_other_feed(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    gallery = Gallery()
    service = compose(tmp_path, gallery)
    process = context.Process(target=_hold_preview_feed_lock, args=(str(service.state.filesystem.root), ready, release))

    async def scenario():
        task = asyncio.create_task(service.plan_workflow(FEED))
        await asyncio.sleep(0.1)
        assert gallery.checks == 0
        # The planning service is locked, but another service/feed can proceed.
        other = Gallery()
        async with compose(tmp_path, other) as other_service:
            await asyncio.wait_for(other_service.plan_workflow(FEED + "-other"), 5)
            assert other.checks == 1
        release.set()
        assert (await asyncio.wait_for(task, 5)).selected_urls == (A.url, B.url)
        await service.close()

    try:
        process.start()
        assert ready.wait(10)
        asyncio.run(scenario())
        process.join(5)
        assert process.exitcode == 0
    finally:
        release.set()
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_preview_does_not_migrate_legacy_update_history(tmp_path):
    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            payload = b'{"records": {}}'
            service.state.filesystem.write_bytes_atomic("updates.json", payload)
            result = await service.plan_workflow(FEED)
            assert result.first_run and result.selected_urls == (A.url, B.url)
            assert service.state.filesystem.read_text("updates.json") == payload.decode()

    asyncio.run(scenario())
