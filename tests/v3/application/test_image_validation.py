"""Image content validation, original preservation, and save boundaries."""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageFile

from image_downloader import AppConfig, RuntimeComposer, WorkflowStateService
from image_downloader.config import resolve_application_config
from image_downloader.exceptions import (
    ImageContentTypeError,
    ImageDecodeError,
    ImageDimensionLimitError,
    ImageMimeMismatchError,
)
from image_downloader.media import ImageProcessor
from image_downloader.media.artifact_pipeline import ArtifactPipeline
from image_downloader.media.processor_chain import PreparedProcessor
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource
from image_downloader.observability.events import EventName
from image_downloader.plugins.builtin import GenericHtmlPlugin
from image_downloader.plugins.plugin_invoker import PluginInvoker


def _image_bytes(image_format="PNG", size=(16, 16)):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format=image_format)
    return output.getvalue()


def _animation():
    output = io.BytesIO()
    first = Image.new("RGB", (16, 16), "red")
    second = Image.new("RGB", (16, 16), "blue")
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=100, loop=0)
    return output.getvalue()


def test_default_validation_and_explicit_existing_configuration(tmp_path):
    assert AppConfig().media.input_validation == "both"
    assert resolve_application_config(None).config.media.input_validation == "both"
    path = tmp_path / "app.yaml"
    contents = "media:\n  input_validation: content_type\n"
    path.write_text(contents, encoding="utf-8")
    assert resolve_application_config(path).config.media.input_validation == "content_type"
    assert path.read_text(encoding="utf-8") == contents


@pytest.fixture(scope="module")
def core():
    with ImageProcessor() as processor:
        yield processor


def _pipeline(core, config=None, processors=()):
    async def log(*args, **kwargs):
        pass

    logger = SimpleNamespace(core=log, plugin=log)
    record = SimpleNamespace(id="core.generic-html", manifest=SimpleNamespace(value={}), catalog=None)
    registry = SimpleNamespace(effective_config=lambda *args: {})
    return ArtifactPipeline(
        config or AppConfig(), registry, record, None, core, logger, PluginInvoker(record.id, logger), processors
    )


def _process(pipeline, data, mime, plugin=None):
    image = ImageResource("https://example.test/image.png")
    chapter = Chapter(1, "Demo", images=(image,))
    manifest = DownloadManifest("Demo", (chapter,))
    return asyncio.run(
        pipeline.process(plugin or GenericHtmlPlugin(), ImageArtifact(data, mime, image.url), image, manifest, chapter)
    )


@pytest.mark.parametrize("mime", ["image/png", "image/svg+xml", "image/avif", "", "application/octet-stream"])
def test_explicit_content_type_mode_accepts_allowed_nonempty_data_without_decode(mime):
    pipeline = _pipeline(None, AppConfig.model_validate({"media": {"input_validation": "content_type"}}))
    pipeline._validate_input(ImageArtifact(b"unvalidated", mime, "image:1"))


@pytest.mark.parametrize("mode", ["content_type", "both"])
@pytest.mark.parametrize("mime", ["image/unknown", "text/html", "application/json", "application/pdf"])
def test_mime_allow_list_rejects_other_types(core, mode, mime):
    config = AppConfig.model_validate({"media": {"input_validation": mode}})
    with pytest.raises(ImageContentTypeError):
        _pipeline(core, config)._validate_input(ImageArtifact(_image_bytes(), mime, "image:1"))


@pytest.mark.parametrize("mode", ["content_type", "decode", "both"])
def test_empty_data_is_rejected_in_every_mode(core, mode):
    config = AppConfig.model_validate({"media": {"input_validation": mode}})
    with pytest.raises(ImageDecodeError, match="empty"):
        _pipeline(core, config)._validate_input(ImageArtifact(b"", "image/png", "image:1"))


@pytest.mark.parametrize("mime", ["image/png", "", "application/octet-stream"])
def test_default_validation_rejects_non_image_bytes(core, mime):
    with pytest.raises(ImageDecodeError):
        _process(_pipeline(core), b"not image data", mime)


@pytest.mark.parametrize("mime", ["", "application/octet-stream"])
def test_unspecified_mime_is_not_a_mismatch(core, mime):
    config = AppConfig.model_validate({"media": {"content_type_mismatch": "error"}})
    data = _image_bytes()
    assert _process(_pipeline(core, config), data, mime).data == data


def test_mime_mismatch_accept_and_error(core):
    data = _image_bytes()
    assert _process(_pipeline(core), data, "image/jpeg").data == data
    strict = AppConfig.model_validate({"media": {"content_type_mismatch": "error"}})
    with pytest.raises(ImageMimeMismatchError):
        _process(_pipeline(core, strict), data, "image/jpeg")


def test_decode_mode_skips_mime_allow_list(core):
    config = AppConfig.model_validate({"media": {"input_validation": "decode"}})
    data = _image_bytes()
    assert _process(_pipeline(core, config), data, "application/pdf").data == data


def test_inspection_rejects_jpeg_that_verify_does_not_detect(core):
    data = _image_bytes("JPEG")[:-2]
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with pytest.raises(ImageDecodeError):
        core.inspect(data)


def test_inspection_reads_later_frames(core):
    data = _animation()[:-8]
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        assert image.n_frames == 2
        image.seek(1)
        with pytest.raises(OSError):
            image.load()
    with pytest.raises(ImageDecodeError):
        core.inspect(data)


def test_worker_rejects_truncation_without_changing_parent_settings(core, monkeypatch):
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(ImageDecodeError):
        core.inspect(_image_bytes("JPEG")[:-2])
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True


def test_each_frame_obeys_pixel_limit(core):
    output = io.BytesIO()
    small = Image.new("RGB", (2, 2), "red")
    large = Image.new("RGB", (16, 16), "blue")
    small.save(output, format="TIFF", save_all=True, append_images=[large])
    with pytest.raises(ImageDimensionLimitError):
        core.inspect(output.getvalue(), max_pixels=4)


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "GIF", "WEBP", "TIFF"])
def test_original_preserves_valid_image_bytes(core, image_format):
    data = _image_bytes(image_format)
    assert _process(_pipeline(core), data, "application/octet-stream").data == data


def test_original_preserves_all_animation_bytes(core):
    data = _animation()
    assert _process(_pipeline(core), data, "image/gif").data == data


def test_svg_requires_explicit_content_type_mode(core):
    data = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    with pytest.raises(ImageDecodeError):
        _process(_pipeline(core), data, "image/svg+xml")
    config = AppConfig.model_validate({"media": {"input_validation": "content_type"}})
    assert _process(_pipeline(core, config), data, "image/svg+xml").data == data


def test_site_transform_can_decode_before_validation(core):
    data = _image_bytes()

    class DecryptingSite(GenericHtmlPlugin):
        async def transform_image(self, artifact, context):
            assert artifact.data == b"encrypted"
            return replace(artifact, data=data)

    assert _process(_pipeline(core), b"encrypted", "image/png", DecryptingSite()).data == data


@pytest.mark.parametrize("output_format", ["ORIGINAL", "PNG"])
@pytest.mark.parametrize("corrupt_data", [b"not image data", _animation()[:-8]])
def test_processor_corruption_is_detected_before_save(core, output_format, corrupt_data):
    class CorruptingProcessor:
        async def transform(self, artifact, context):
            return replace(artifact, data=corrupt_data, content_type="image/gif")

    record = SimpleNamespace(manifest=SimpleNamespace(value={}), catalog=None)
    binding = PreparedProcessor("example.corrupt", record, CorruptingProcessor(), {}, {})
    config = AppConfig.model_validate({"output": {"image_format": output_format}})
    with pytest.raises(ImageDecodeError):
        _process(_pipeline(core, config, processors=(binding,)), _image_bytes(), "image/png")


@pytest.mark.parametrize("mime", [None, "image/png", "application/octet-stream"])
@pytest.mark.parametrize("case", ["invalid", "valid", "mixed"])
def test_download_validation_reaches_save_boundary(tmp_path, mime, case):
    body = b"not image data" if case == "invalid" else _image_bytes()
    config = AppConfig.model_validate({
        "storage": {"data_root": str(tmp_path / "data")},
        "plugins": {"root": str(tmp_path / "plugins")},
        "logging": {"console": {"enabled": False}},
    })

    async def scenario():
        async with RuntimeComposer(config, config_root=tmp_path, plugin_root=tmp_path / "plugins").compose() as service:
            await service.gateway.client.aclose()

            def respond(request):
                if request.url.path == "/gallery":
                    html = '<title>Demo</title><img src="/one.png">'
                    if case == "mixed":
                        html += '<img src="/broken.png">'
                    return httpx.Response(200, text=html)
                data = b"not image data" if request.url.path == "/broken.png" else body
                return httpx.Response(200, content=data, headers={} if mime is None else {"content-type": mime})

            service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            saved = []
            service.events.on(EventName.SAVE_SUCCESS, saved.append)
            destination = tmp_path / "data/profiles/default/downloads/0001_Demo_Demo/0001.png"
            if case == "invalid":
                destination.parent.mkdir(parents=True)
                destination.write_bytes(b"existing file must survive")
            result = await service.workflow("https://example.test/gallery", workflow_retries=0)
            download = result.items[0].download
            assert download is not None
            if case != "invalid":
                assert len(saved) == 1
                assert Path(download.saved_files[0]).read_bytes() == body
                if case == "valid":
                    assert not download.failures
                else:
                    assert len(download.failures) == 1 and download.failures[0].code == "image_decode_error"
                    assert result.items[0].status == "partial"
                    assert not WorkflowStateService(config).get_workflow("https://example.test/gallery")[0].items[0].completed
            else:
                assert not saved and not download.saved_files
                assert download.failures[0].code == "image_decode_error"
                assert destination.read_bytes() == b"existing file must survive"
                assert result.items[0].status != "success"
                assert not WorkflowStateService(config).get_workflow("https://example.test/gallery")[0].items[0].completed

    asyncio.run(scenario())
