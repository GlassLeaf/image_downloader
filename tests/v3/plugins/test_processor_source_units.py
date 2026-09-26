"""Integration coverage for the shipped image-processor reference units."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image

from image_downloader import Chapter, DownloadManifest, ImageArtifact, ImageResource, TransformContext
from image_downloader.config import AppConfig
from image_downloader.plugins.plugin_manifest import read_manifest, verify_signed_plugin_source
from image_downloader.security import PluginRuntime, install_plugin, safe_app_settings

HISTORY_ID = "local.image-downloader.artifact-history-processor"
RESIZE_ID = "local.image-downloader.resize-processor"


def _context(config: AppConfig, record, image: ImageResource) -> TransformContext:
    chapter = Chapter(1, "chapter", images=(image,))
    manifest = DownloadManifest("work", (chapter,))
    return TransformContext(
        image,
        config={},
        app_settings=safe_app_settings(config),
        plugin_manifest=record.manifest.value,
        catalog=record.catalog.as_json() if record.catalog is not None else None,
        manifest=manifest,
        chapter=chapter,
    )


def _png(width: int, height: int) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (width, height), color=(20, 40, 60)).save(stream, format="PNG")
    return stream.getvalue()


def test_processor_source_units_are_signed_strictly_loaded_and_transform_artifacts(
    tmp_path: Path, plugin_sources: Path
) -> None:
    root = (tmp_path / "plugin-root").resolve()
    for name in ("artifact-history-processor", "resize-processor"):
        source = (plugin_sources / name).resolve()
        verify_signed_plugin_source(read_manifest(source))
        install_plugin(root, source)

    config = AppConfig.model_validate(
        {
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "strict"},
            "image_processors": {"chain": [HISTORY_ID, RESIZE_ID]},
            "plugin_settings": {
                HISTORY_ID: {"config": {"label": "reviewed"}},
                RESIZE_ID: {"config": {"max_width": 80, "max_height": 80}},
            },
        }
    )
    runtime = PluginRuntime(config, root, mode="strict")
    runtime.prepare()
    runtime.validate_all()

    image = ImageResource("https://example.test/image.png", image_id="image-7")
    artifact = ImageArtifact(_png(400, 200), "image/png", image.url, image_id=image.image_id, history=("source",))
    history_record, history = runtime.processor(HISTORY_ID) or (None, None)
    assert history_record is not None and history is not None
    history_context = _context(config, history_record, image)
    history_context = TransformContext(
        image,
        config=runtime.effective_config(history_record),
        app_settings=history_context.app_settings,
        plugin_manifest=history_context.plugin_manifest,
        catalog=history_context.catalog,
        manifest=history_context.manifest,
        chapter=history_context.chapter,
    )
    labeled = asyncio.run(history.transform(artifact, history_context))
    assert labeled.history == ("source", "reviewed")
    assert artifact.history == ("source",)

    resize_record, resize = runtime.processor(RESIZE_ID) or (None, None)
    assert resize_record is not None and resize is not None
    resize_context = _context(config, resize_record, image)
    resize_context = TransformContext(
        image,
        config=runtime.effective_config(resize_record),
        app_settings=resize_context.app_settings,
        plugin_manifest=resize_context.plugin_manifest,
        catalog=resize_context.catalog,
        manifest=resize_context.manifest,
        chapter=resize_context.chapter,
    )
    resized = asyncio.run(resize.transform(labeled, resize_context))
    with Image.open(BytesIO(resized.data)) as output:
        assert output.format == "PNG"
        assert output.size == (80, 40)
    assert resized.content_type == "image/png"
    assert resized.extension == "png"
    assert resized.source_url == artifact.source_url
    assert resized.image_id == "image-7"
    assert resized.history == ("source", "reviewed", "resize:80x40")
