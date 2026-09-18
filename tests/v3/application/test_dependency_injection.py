from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import image_downloader.application.composer as composer_module
from image_downloader.config import AppConfig, resolve_paths
from image_downloader.runtime import DownloadService, RuntimeComposer, _RuntimeDependencies
from image_downloader.storage.cookies import CookieStore


def _composer(tmp_path: Path) -> RuntimeComposer:
    data_root = (tmp_path / "data").resolve()
    plugin_root = (tmp_path / "plugins").resolve()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(data_root)},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root)


def _dependencies() -> _RuntimeDependencies:
    return _RuntimeDependencies(
        outputs=MagicMock(),
        logs=MagicMock(),
        state=MagicMock(),
        events=MagicMock(),
        logger=MagicMock(),
        notifications=MagicMock(),
        cookie_store=MagicMock(),
        cookie_baseline={},
        gateway=MagicMock(),
        image_processor=MagicMock(),
        output_locks=MagicMock(),
    )


def test_download_service_uses_injected_dependencies_without_constructing_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    composer = _composer(tmp_path)
    registry = composer.compose_registry()
    dependencies = _dependencies()

    def unexpected_construction(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("DownloadService must not construct infrastructure")

    for name in (
        "FileSystem",
        "UpdateState",
        "EventBus",
        "DownloadLogger",
        "NotificationService",
        "CookieStore",
        "RequestGateway",
        "ImageProcessor",
    ):
        monkeypatch.setattr(composer_module, name, unexpected_construction)

    service = DownloadService(composer.config, registry, dependencies)

    assert service.outputs is dependencies.outputs
    assert service.logs is dependencies.logs
    assert service.state is dependencies.state
    assert service.events is dependencies.events
    assert service.logger is dependencies.logger
    assert service.notifications is dependencies.notifications
    assert service.cookie_store is dependencies.cookie_store
    assert service.gateway is dependencies.gateway
    assert service.image_processor is dependencies.image_processor
    assert service.output_locks is dependencies.output_locks


def test_runtime_composer_builds_all_default_dependencies(tmp_path: Path) -> None:
    async def scenario() -> None:
        composer = _composer(tmp_path)
        service = composer.compose()
        paths = resolve_paths(composer.config)
        try:
            assert service.outputs.root == paths["downloads"]
            assert service.logs.root == paths["logs"]
            assert service.state.filesystem.root == paths["state"]
            assert service.events is service.notifications.events
            assert service.logger is service.notifications.logger
            assert service.logger is service.gateway._logger
            assert service.cookie_store.filesystem.root == paths["cookie"]
            assert service.output_locks.state.root == paths["state"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_two_services_preserve_each_others_cookie_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(CookieStore, "_key", lambda _self, *, create: b"t" * 32)

    async def scenario() -> None:
        composer = _composer(tmp_path)
        first = composer.compose()
        second = composer.compose()
        first.gateway.client.cookies.set("first", "one", domain="example.test")
        second.gateway.client.cookies.set("second", "two", domain="example.test")
        await first.close()
        await second.close()
        assert {cookie.name: cookie.value for cookie in first.cookie_store.load()} == {
            "first": "one", "second": "two"
        }

    asyncio.run(scenario())


def test_compose_keeps_the_public_call_and_injects_factory_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    composer = _composer(tmp_path)
    dependencies = _dependencies()
    monkeypatch.setattr(composer, "_build_dependencies", lambda: dependencies)

    service = composer.compose()

    assert service.outputs is dependencies.outputs
    assert service.gateway is dependencies.gateway
    assert service.image_processor is dependencies.image_processor
    assert service.output_locks is dependencies.output_locks
