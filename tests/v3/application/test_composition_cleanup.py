"""QS-013: a failed composition owns and rolls back its unfinished resources."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import threading
from collections import Counter
from functools import partial
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import image_downloader.application.composer as composer_module
import image_downloader.application.construction as construction_module
import image_downloader.media.image_processor as processor_module
import image_downloader.observability.logging as logging_module
import image_downloader.transport.gateway as gateway_module
from image_downloader import AppConfig, AuthenticationError, RuntimeComposer
from image_downloader.plugins import source_loader
from image_downloader.plugins.runtime import PluginRuntime
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore

METHODS = ("compose", "_compose_for_inspection", "_compose_for_workflow_plan")
PLUGIN_ID = "com.example.cleanup"


def _composer(tmp_path: Path) -> RuntimeComposer:
    root = tmp_path / "plugins"
    directory = root / "site_plugins" / "cleanup"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {"manifest": {"id": PLUGIN_ID, "kind": "site_plugin", "entry": {"file": "entry.py", "class": "Entry"}}}
        ),
        encoding="utf-8",
    )
    (directory / "entry.py").write_text(
        "class Entry:\n"
        "    def validate_config(self, config, app_settings): pass\n"
        "    def matches(self, url): return True\n"
        "    async def inspect(self, url, context): pass\n"
        "    async def create_image_request(self, image, context): pass\n"
        "    async def recover_image_request(self, image, failed, response, context): pass\n"
        "    def auth_flow(self, context): pass\n"
        "    async def transform_image(self, artifact, context): return artifact\n"
        "    @staticmethod\n"
        "    def lazy():\n"
        "        from . import helper\n"
        "        return helper.VALUE\n",
        encoding="utf-8",
    )
    (directory / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path, plugin_root=root)


def _import_state():
    return (
        {name: value for name, value in sys.modules.items() if name.startswith("_image_downloader_plugins")},
        dict(source_loader._NAMESPACE_FINDER._contexts),
        tuple(sys.meta_path),
    )


@pytest.fixture
def resources(monkeypatch):
    captured = {}
    closed = []
    loops = []
    close_errors = {}
    workers = []
    client_class = httpx.AsyncClient
    persist = Mock()
    notify = AsyncMock()
    monkeypatch.setattr(CookieStore, "load", lambda self: CookieJar())
    monkeypatch.setattr(CookieStore, "persist_delta", persist)
    monkeypatch.setattr(composer_module.NotificationService, "flush", notify)

    def unexpected_request(request):
        raise AssertionError("composition must not make HTTP requests")

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(unexpected_request))
    )

    def executor(**kwargs):
        value = Mock()
        workers.append(value)
        return value

    monkeypatch.setattr(processor_module, "ProcessPoolExecutor", executor)

    def capture(name, constructor):
        def create(*args, **kwargs):
            value = constructor(*args, **kwargs)
            captured.setdefault(name, []).append(value)
            original = value.close
            if inspect.iscoroutinefunction(original):

                async def close():
                    closed.append(name)
                    loops.append(asyncio.get_running_loop())
                    await asyncio.sleep(0)
                    await original()
                    if name in close_errors:
                        raise close_errors[name]
            else:

                def close():
                    closed.append(name)
                    original()
                    if name in close_errors:
                        raise close_errors[name]

            value.close = close
            return value

        return create

    for name, constructor in (
        ("registry", "PluginRuntime"),
        ("sink", "DebugFileSink"),
        ("logger", "DownloadLogger"),
        ("gateway", "RequestGateway"),
        ("processor", "ImageProcessor"),
    ):
        monkeypatch.setattr(composer_module, constructor, capture(name, getattr(composer_module, constructor)))
    yield SimpleNamespace(
        captured=captured,
        closed=closed,
        loops=loops,
        close_errors=close_errors,
        workers=workers,
        persist=persist,
        notify=notify,
    )
    # Also clean up on assertion failure so these tests never leak real handles.
    for values in captured.values():
        for value in values:
            try:
                if inspect.iscoroutinefunction(value.close):
                    asyncio.run(value.close())
                else:
                    value.close()
            except BaseException:
                pass


def _fail_at(stage, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    if stage == "cookie_load":
        monkeypatch.setattr(CookieStore, "load", fail)
    elif stage == "cookie_snapshot":
        monkeypatch.setattr(CookieStore, "snapshot", fail)
    else:
        name = {
            "logger": "DownloadLogger",
            "processor": "ImageProcessor",
            "output_locks": "OutputDirectoryLocks",
            "service": "DownloadService",
        }[stage]
        monkeypatch.setattr(composer_module, name, fail)


def _expect_failure(composer, method, error, *, active_loop):
    cause = error.__cause__

    def check():
        with pytest.raises(type(error)) as caught:
            getattr(composer, method)()
        assert caught.value is error
        assert caught.value.__cause__ is cause

    if active_loop:

        async def scenario():
            check()

        asyncio.run(scenario())
    else:
        check()


def _assert_released(resources):
    counts = Counter(resources.closed)
    for name, values in resources.captured.items():
        assert counts[name] == len(values), (name, counts)
        for value in values:
            if name == "sink":
                assert value._stream.closed
            elif name == "gateway":
                assert value.client.is_closed
            elif name == "processor":
                assert value._closed
            elif name == "registry":
                assert value.loader._closed
    for worker in resources.workers:
        worker.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert all(loop.is_closed() for loop in resources.loops)
    assert not any(thread.name == "image-downloader-initialization-cleanup" for thread in threading.enumerate())
    resources.persist.assert_not_called()
    resources.notify.assert_not_called()


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("active_loop", [False, True])
@pytest.mark.parametrize("stage", ["cookie_load", "cookie_snapshot", "processor", "output_locks", "service"])
def test_failed_composition_releases_every_completed_resource(
    tmp_path, monkeypatch, resources, method, active_loop, stage
):
    composer = _composer(tmp_path)
    before = _import_state()
    error = AuthenticationError("construction secret must not be added to diagnostics")
    error.__cause__ = OSError("original cause")
    _fail_at(stage, monkeypatch, error)
    _expect_failure(composer, method, error, active_loop=active_loop)
    assert PLUGIN_ID in resources.captured["registry"][0].records
    _assert_released(resources)
    assert _import_state() == before
    if stage in {"output_locks", "service"}:
        assert resources.closed == (
            ["processor", "gateway", "logger"] + (["sink"] if method == "compose" else []) + ["registry"]
        )


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("active_loop", [False, True])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_interrupted_composition_keeps_the_original_interruption(
    tmp_path, monkeypatch, resources, method, active_loop, error_type
):
    composer = _composer(tmp_path)
    before = _import_state()
    error = error_type("construction interrupted")
    _fail_at("output_locks", monkeypatch, error)
    _expect_failure(composer, method, error, active_loop=active_loop)
    _assert_released(resources)
    assert _import_state() == before


@pytest.mark.parametrize("active_loop", [False, True])
def test_logger_constructor_failure_closes_its_not_yet_adopted_sink(tmp_path, monkeypatch, resources, active_loop):
    composer = _composer(tmp_path)
    before = _import_state()
    error = RuntimeError("logger constructor failed")
    _fail_at("logger", monkeypatch, error)
    _expect_failure(composer, "compose", error, active_loop=active_loop)
    assert resources.closed == ["sink", "registry"]
    _assert_released(resources)
    assert _import_state() == before


@pytest.mark.parametrize("active_loop", [False, True])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_cleanup_errors_do_not_hide_construction_failure_or_skip_remaining_resources(
    tmp_path, monkeypatch, resources, capsys, active_loop, error_type
):
    composer = _composer(tmp_path)
    before = _import_state()
    error = AuthenticationError("original-credential-value")
    resources.close_errors.update(
        {name: error_type("cleanup-credential-value") for name in ("processor", "gateway", "logger", "registry")}
    )
    _fail_at("service", monkeypatch, error)
    _expect_failure(composer, "compose", error, active_loop=active_loop)
    _assert_released(resources)
    assert _import_state() == before
    assert capsys.readouterr().err == (
        "warning: runtime initialization cleanup failed (image)\n"
        "warning: runtime initialization cleanup failed (http)\n"
        "warning: runtime initialization cleanup failed (logging)\n"
        "warning: runtime initialization cleanup failed (plugins)\n"
    )


def test_interruption_while_waiting_for_cleanup_still_finishes_the_thread(tmp_path, monkeypatch, resources, capsys):
    class InterruptedWait:
        def __init__(self):
            self.event = threading.Event()
            self.interrupted = False

        def set(self):
            self.event.set()

        def is_set(self):
            return self.interrupted and self.event.is_set()

        def wait(self):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt("second interruption must not replace original error")
            return self.event.wait()

    monkeypatch.setattr(
        construction_module, "threading", SimpleNamespace(Event=InterruptedWait, Thread=threading.Thread)
    )
    composer = _composer(tmp_path)
    before = _import_state()
    error = AuthenticationError("original construction failure")
    _fail_at("service", monkeypatch, error)
    _expect_failure(composer, "compose", error, active_loop=True)
    _assert_released(resources)
    assert _import_state() == before
    assert capsys.readouterr().err == (
        "warning: runtime initialization cleanup failed (http)\n"
        "warning: runtime initialization cleanup failed (logging)\n"
    )


@pytest.mark.parametrize("stage", ["builtin", "prepare"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_registry_composition_failure_unloads_its_own_modules(tmp_path, monkeypatch, resources, stage, error_type):
    composer = _composer(tmp_path)
    before = _import_state()
    original = PluginRuntime.prepare
    error = error_type("registry failed")

    def fail(self, *args):
        if stage == "prepare":
            original(self)
            assert self.module_namespace(self.records[PLUGIN_ID]) is not None
        raise error

    monkeypatch.setattr(PluginRuntime, "register_builtin" if stage == "builtin" else "prepare", fail)
    _expect_failure(composer, "compose_registry", error, active_loop=False)
    assert resources.closed == ["registry"]
    _assert_released(resources)
    assert _import_state() == before


def test_repeated_failures_preserve_another_runtime_and_its_lazy_import(tmp_path, monkeypatch, resources):
    composer = _composer(tmp_path)
    surviving = composer.compose_registry()
    try:
        record = surviving.records[PLUGIN_ID]
        plugin_class = surviving.loader.load_class(record)
        namespace = surviving.module_namespace(record)
        for _ in range(4):
            before = _import_state()
            error = AuthenticationError("retry failed")
            _fail_at("cookie_load", monkeypatch, error)
            _expect_failure(composer, "compose", error, active_loop=True)
            assert _import_state() == before
            assert surviving.module_namespace(record) == namespace
            assert plugin_class.lazy() == 42
    finally:
        surviving.close()


@pytest.mark.parametrize("method", METHODS)
def test_success_transfers_ownership_and_keeps_normal_close(tmp_path, resources, method):
    composer = _composer(tmp_path)
    before = _import_state()

    async def scenario():
        service = getattr(composer, method)()
        assert resources.closed == []
        assert not service.gateway.client.is_closed
        assert not service.image_processor._closed
        assert service._persist_cookies_on_close is (method != "_compose_for_workflow_plan")
        await service.close()
        closed = list(resources.closed)
        await service.close()
        assert resources.closed == closed

    asyncio.run(scenario())
    assert _import_state() == before
    assert Counter(resources.closed) == {
        "registry": 1,
        "gateway": 1,
        "processor": 1,
        "logger": 1,
        **({"sink": 1} if method == "compose" else {}),
    }
    assert resources.persist.call_count == (0 if method == "_compose_for_workflow_plan" else 1)
    resources.notify.assert_not_called()


@pytest.mark.parametrize("resource", ["sink", "gateway", "processor"])
def test_constructor_does_not_acquire_a_resource_before_its_local_lock(tmp_path, monkeypatch, resource):
    error = KeyboardInterrupt("local initialization interrupted")

    def fail(*args, **kwargs):
        raise error

    acquire = Mock()
    if resource == "sink":
        monkeypatch.setattr(logging_module, "asyncio", SimpleNamespace(Lock=fail))
        monkeypatch.setattr(FileSystem, "open_text_append", acquire)
        construct = partial(
            logging_module.DebugFileSink, filesystem=FileSystem(tmp_path), relative_path=Path("debug.log")
        )
    elif resource == "gateway":
        monkeypatch.setattr(gateway_module, "asyncio", SimpleNamespace(Semaphore=fail))
        monkeypatch.setattr(httpx, "AsyncClient", acquire)
        construct = partial(gateway_module.RequestGateway, AppConfig())
    else:
        monkeypatch.setattr(processor_module, "threading", SimpleNamespace(Lock=fail))
        monkeypatch.setattr(processor_module, "ProcessPoolExecutor", acquire)
        construct = processor_module.ImageProcessor
    with pytest.raises(KeyboardInterrupt) as caught:
        construct()
    assert caught.value is error
    acquire.assert_not_called()
