from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from image_downloader.config import AppConfig
from image_downloader.models import RequestResponse, RequestSpec
from image_downloader.ports import PluginExecutionContext
from image_downloader.security import PluginRuntime, install_plugin

GENERIC_ID = "local.image-downloader.generic-css-selector"
CATALOG_ID = "local.image-downloader.paginated-catalog"


class _Requests:
    def __init__(self, responses: Mapping[str, bytes]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        self.specs.append(spec)
        return RequestResponse(spec.url, 200, {"Content-Type": "text/html"}, self.responses[spec.url])


class _Secrets:
    def get(self, name: str) -> str:
        raise AssertionError(f"unexpected secret request: {name}")


def _registry(tmp_path: Path, plugin_sources: Path) -> PluginRuntime:
    root = (tmp_path / "plugin-root").resolve()
    for name in ("generic-css-selector", "paginated-catalog"):
        install_plugin(root, (plugin_sources / name).resolve())
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "strict"},
        }
    )
    registry = PluginRuntime(config, root, mode="strict")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    assert not [item for item in registry.diagnostics if not item.loaded and not item.warning]
    return registry


def _context(
    registry: PluginRuntime, record, requests: _Requests, overrides: Mapping[str, Mapping[str, object]] | None = None
):
    catalog = record.catalog.as_json() if record.catalog else None
    return PluginExecutionContext(
        registry.effective_config(record, overrides),
        {},
        record.manifest.value,
        catalog,
        _Secrets(),
        requests,
    )


def _fixture(local_plugin_fixtures: Path, name: str) -> bytes:
    return (local_plugin_fixtures / name).read_bytes()


@pytest.fixture(scope="module")
def installed_registry(tmp_path_factory: pytest.TempPathFactory, plugin_sources: Path) -> PluginRuntime:
    return _registry(tmp_path_factory.mktemp("local-site-plugins"), plugin_sources)


def test_signed_units_load_strictly_and_specific_catalog_wins(installed_registry: PluginRuntime) -> None:
    registry = installed_registry

    catalog_record, _ = registry.resolve("https://catalog.example.test/collections/summer", fallback_enabled=True)
    generic_record, _ = registry.resolve("https://generic.example.test/gallery", fallback_enabled=True)

    assert catalog_record.id == CATALOG_ID
    assert generic_record.id == GENERIC_ID
    assert catalog_record.catalog is not None and generic_record.catalog is not None


def test_paginated_catalog_collects_unique_work_chapters_in_page_order(
    installed_registry: PluginRuntime, local_plugin_fixtures: Path
) -> None:
    registry = installed_registry
    url = "https://catalog.example.test/collections/summer"
    responses = {
        url: _fixture(local_plugin_fixtures, "catalog-page-1.html"),
        f"{url}?page=2": _fixture(local_plugin_fixtures, "catalog-page-2.html"),
        "https://catalog.example.test/works/one": _fixture(local_plugin_fixtures, "catalog-work-one.html"),
        "https://catalog.example.test/works/two": _fixture(local_plugin_fixtures, "catalog-work-two.html"),
        "https://catalog.example.test/works/three": _fixture(local_plugin_fixtures, "catalog-work-three.html"),
    }
    record, plugin = registry.resolve(url, fallback_enabled=True)
    requests = _Requests(responses)
    manifest = asyncio.run(plugin.inspect(url, _context(registry, record, requests)))

    assert manifest.title == "Summer Collection"
    assert [chapter.chapter_id for chapter in manifest.chapters] == ["one:a", "one:b", "two:a", "three:a"]
    assert [image.url for chapter in manifest.chapters for image in chapter.images] == [
        "https://catalog.example.test/works/images/one-a-1.jpg",
        "https://catalog.example.test/images/one-b-1.jpg",
        "https://cdn.example.test/two-a-1.jpg",
        "https://catalog.example.test/images/three-a-1.jpg",
    ]
    assert [spec.url for spec in requests.specs] == list(responses)
    image = manifest.chapters[0].images[0]
    assert (
        asyncio.run(plugin.create_image_request(image, _context(registry, record, requests))).referer
        == "https://catalog.example.test/works/one"
    )


def test_generic_css_selector_uses_dom_order_and_only_http_src_values(
    installed_registry: PluginRuntime, local_plugin_fixtures: Path
) -> None:
    registry = installed_registry
    url = "https://generic.example.test/gallery/page"
    record, plugin = registry.resolve(url, fallback_enabled=True)
    requests = _Requests({url: _fixture(local_plugin_fixtures, "generic-page.html")})
    manifest = asyncio.run(
        plugin.inspect(
            url,
            _context(
                registry, record, requests, {GENERIC_ID: {"selector": ".viewer > figure > img[data-kind='full']"}}
            ),
        )
    )

    assert manifest.title == "Generic Fixture"
    assert [image.url for image in manifest.chapters[0].images] == [
        "https://generic.example.test/gallery/relative/first.jpg"
    ]
    assert [image.index for image in manifest.chapters[0].images] == [1]


@pytest.mark.parametrize("config", ({"selector": ""}, {"selector": "["}, {"selector": "img[src]", "extra": True}))
def test_generic_css_selector_rejects_invalid_or_unknown_configuration(
    installed_registry: PluginRuntime, config: Mapping[str, object]
) -> None:
    registry = installed_registry
    _, plugin = registry.resolve("https://generic.example.test/gallery", fallback_enabled=True)

    with pytest.raises(ValueError):
        plugin.validate_config(config, {})
