from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import struct
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from image_downloader import Chapter, DownloadManifest, ImageArtifact, ImageResource, TransformContext
from image_downloader.exceptions import ImageDimensionLimitError, PluginError

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "docs/investigations/plugin-thread-benchmark-2026-11-02/optimized-plugin-a"
SAMPLE_SOURCE = ROOT / "plugin-implementation-samples/plugin-a"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VECTORS = json.loads((Path(__file__).parent / "fixtures/plugin-a-wat-vectors.json").read_text())


@pytest.fixture(scope="module", params=[SOURCE, SAMPLE_SOURCE], ids=["optimized", "sample"])
def implementation(request):
    source = request.param
    package = types.ModuleType("plugin_a_transform_test_" + source.parent.name.replace("-", "_"))
    package.__path__ = [str(source)]
    sys.modules[package.__name__] = package
    wat = load(source / "wat_module.py", package.__name__ + ".wat_module")
    plugin_class = load(source / "plugin.py", package.__name__ + ".plugin").ADownloadPlugin
    try:
        yield wat, plugin_class
    finally:
        for name in list(sys.modules):
            if name == package.__name__ or name.startswith(package.__name__ + "."):
                sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def wat(implementation):
    return implementation[0]


@pytest.fixture(scope="module")
def plugin_class(implementation):
    return implementation[1]


@pytest.mark.parametrize("vector", VECTORS)
def test_wat_matches_frozen_original_tables(vector, wat):
    assert wat.build_transform_table(
        bytes.fromhex(vector["records"]), vector["width"], vector["height"], vector["iv"], vector["salt"]
    ) == tuple(vector["expected"])


def placeholder(data, prefix="A"):
    return prefix * 66 + base64.b64encode(data).decode()


def test_decoding_preserves_null_groups_and_immutable_bytes(wat):
    assert wat.decode_placeholder(placeholder(struct.pack("<HH", 0, 1) + b"12345678")) == (None, b"12345678")


@pytest.mark.parametrize("source", ["", "A" * 66 + "!", placeholder(b"x"), placeholder(b"\x01\x00short")])
def test_malformed_placeholders_are_explicit_errors(source, wat):
    with pytest.raises(ValueError):
        wat.decode_placeholder(source)


@pytest.mark.parametrize("records", [b"", b"short", b"\x00" * 8])
def test_invalid_record_headers_are_explicit_errors(records, wat):
    with pytest.raises(ValueError):
        wat.build_transform_table(records, 256, 256, "iv", "salt")


def test_cache_is_bounded_and_shared_between_threads(plugin_class):
    plugin = plugin_class()
    value = placeholder(b"\x01\x00" + b"12345678")
    with ThreadPoolExecutor(4) as pool:
        groups = list(pool.map(lambda _: plugin._records("same", value), range(32)))
    assert all(group is groups[0] for group in groups)
    for i in range(8):
        plugin._records(str(i), value)
    assert len(plugin._placeholder_cache) == 4
    assert plugin._cached_bytes == 32
    plugin._CACHE_BYTES = 4
    assert plugin._records("oversize", value) == (b"12345678",)
    assert ("oversize", value) not in plugin._placeholder_cache
    plugin.cleanup_after_use()
    assert plugin._cached_bytes == 0
    assert not plugin._placeholder_cache


def test_parallel_pixels_match_independent_reference_and_size_limit_precedes_copy(monkeypatch, plugin_class):
    harness = load(ROOT / "tools/benchmark_plugin_threads.py", "benchmark_a_fixtures")
    bodies, encoded, expected = harness.fixtures(8, 256, 384)
    images = tuple(
        ImageResource(
            f"image:{i}",
            index=i + 1,
            metadata={"iv": "test-iv", "salt": "test-salt", "placeholder": encoded},
            plugin_data={"iv": "test-iv", "salt": "test-salt"},
        )
        for i in range(8)
    )
    chapter = Chapter(1, "test", images=images, chapter_id="one", plugin_data={"placeholder": encoded})
    manifest = DownloadManifest("test", (chapter,))
    contexts = [TransformContext(image, {}, {}, {}, None, manifest, chapter) for image in images]
    plugin = plugin_class()

    def convert(i):
        artifact = ImageArtifact(bodies[i % 4], "image/jpeg", images[i].url)
        result = plugin.transform_image(artifact, contexts[i])
        import io

        with Image.open(io.BytesIO(result.data)) as decoded:
            assert hashlib.sha256(decoded.convert("RGB").tobytes()).hexdigest() == expected[i % 4]
        assert result.content_type == "image/webp"
        assert result.extension == ".webp"
        return result

    with ThreadPoolExecutor(4) as pool:
        assert len(list(pool.map(convert, range(8)))) == 8
    limited = TransformContext(images[0], {}, {"media": {"max_image_pixels": 1}}, {}, None, manifest, chapter)
    monkeypatch.setattr(Image.Image, "copy", lambda _: pytest.fail("expanded before checking dimensions"))
    with pytest.raises(ImageDimensionLimitError):
        plugin.transform_image(ImageArtifact(bodies[0], "image/jpeg", images[0].url), limited)
    missing_chapter = replace(chapter, plugin_data={"placeholder": placeholder(b"\x00\x00")})
    missing = TransformContext(
        replace(images[0], plugin_data={"iv": "x", "salt": "x"}),
        {},
        {},
        {},
        None,
        manifest,
        missing_chapter,
        image_metadata={"iv": "x", "salt": "x", "placeholder": placeholder(b"\x00\x00")},
    )
    with pytest.raises(PluginError, match="metadata"):
        plugin.transform_image(ImageArtifact(bodies[0], "image/jpeg", images[0].url), missing)
