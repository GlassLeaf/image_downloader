from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from image_downloader.config import AppConfig
from image_downloader.immutable import freeze_json, thaw_json
from image_downloader.models import Chapter, DownloadManifest, ImageResource, RequestResponse, RequestSpec
from image_downloader.models import freeze_json as model_freeze_json
from image_downloader.models import thaw_json as model_thaw_json
from image_downloader.ports import PluginExecutionContext, TransformContext


class _Secrets:
    def get(self, name: str) -> str:
        raise LookupError(name)


class _Requests:
    async def execute(self, spec: RequestSpec) -> RequestResponse:
        return RequestResponse(spec.url, 200, {}, b"")


def _nested_value() -> dict[object, object]:
    return {
        7: {
            "list": [{"leaf": 1}],
            "tuple": ("value", {"nested": True}),
            "set": {"left", "right"},
        }
    }


def _assert_deeply_frozen(value: Mapping[str, object]) -> None:
    nested = value["7"]
    assert isinstance(nested, Mapping)
    assert nested["list"] == ({"leaf": 1},)
    assert nested["tuple"] == ("value", {"nested": True})
    assert nested["set"] == frozenset(("left", "right"))
    with pytest.raises(TypeError):
        value["new"] = "rejected"
    with pytest.raises(TypeError):
        nested["new"] = "rejected"
    with pytest.raises(TypeError):
        nested["list"][0]["leaf"] = 2  # type: ignore[index]


def test_idr_033_freeze_and_thaw_use_one_recursive_collection_contract() -> None:
    source = _nested_value()
    frozen = freeze_json(source)

    assert isinstance(frozen, Mapping)
    _assert_deeply_frozen(frozen)
    source[7]["list"][0]["leaf"] = 99  # type: ignore[index]
    assert frozen["7"]["list"][0]["leaf"] == 1  # type: ignore[index]

    thawed = thaw_json(frozen)
    assert isinstance(thawed, dict)
    assert thawed["7"]["list"] == [{"leaf": 1}]
    assert thawed["7"]["tuple"] == ["value", {"nested": True}]
    assert set(thawed["7"]["set"]) == {"left", "right"}
    json.dumps(thawed)


def test_idr_033_dto_config_and_plugin_context_share_the_utility() -> None:
    request = RequestSpec("https://example.test", json=_nested_value())
    config = AppConfig.model_validate(
        {"plugin_settings": {"com.example.site": {"config": {"payload": _nested_value()}}}}
    )
    context = PluginExecutionContext(_nested_value(), _nested_value(), _nested_value(), None, _Secrets(), _Requests())
    image = ImageResource("https://example.test/image.png")
    chapter = Chapter(1, "chapter", images=(image,))
    manifest = DownloadManifest("title", (chapter,))
    transform = TransformContext(image, _nested_value(), {}, {}, None, manifest, chapter)

    assert isinstance(request.json, Mapping)
    _assert_deeply_frozen(request.json)
    plugin_payload = config.plugin_settings["com.example.site"].config["payload"]
    assert isinstance(plugin_payload, Mapping)
    _assert_deeply_frozen(plugin_payload)
    _assert_deeply_frozen(context.config)
    _assert_deeply_frozen(transform.config)


def test_idr_033_models_keep_compatibility_reexports() -> None:
    assert model_freeze_json is freeze_json
    assert model_thaw_json is thaw_json
