"""Minimum save requirements, final-artifact hooks, CLI overrides and diagnostics."""

from __future__ import annotations

import asyncio
import io
import itertools
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from image_downloader import DownloadRequest, ImageSaveOptions, PluginError, WorkflowRequest
from image_downloader.config import AppConfig
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ConfigurationError, ImageDecodeError
from image_downloader.media import ImageProcessor, TransformExecutor
from image_downloader.media.artifact_pipeline import ArtifactPipeline
from image_downloader.media.pipeline_binding import PreparedPipelineBinding
from image_downloader.media.processor_chain import PreparedProcessor
from image_downloader.media.save_policy import aggregate_save_options
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource
from image_downloader.plugins.builtin import GenericHtmlPlugin
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.plugins.plugin_validation import validate_save_options


def picture(image_format="PNG", size=(8, 6)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def core():
    with ImageProcessor() as processor:
        yield processor


def run_pipeline(
    core, *, site=None, processors=(), image_format=None, image_quality=None, data=None, mime="image/png", config=None
):
    records = []

    async def log(event, **values):
        records.append((event, values))

    logger = SimpleNamespace(core=log, plugin=log)
    executor = TransformExecutor()
    binding = PreparedPipelineBinding.prepare(
        config or AppConfig(),
        "test.site",
        {"owner": "site"},
        {},
        {},
        None,
        image_format=image_format,
        image_quality=image_quality,
    )
    bindings = tuple(
        PreparedProcessor(
            f"test.processor{i}",
            SimpleNamespace(manifest=SimpleNamespace(value={}), catalog=None),
            processor,
            {"owner": f"processor{i}"},
            {},
        )
        for i, processor in enumerate(processors)
    )
    pipeline = ArtifactPipeline._from_binding(
        binding,
        core,
        logger,
        PluginInvoker("test.site", logger),
        bindings,
        transform_executor=executor,
    )
    image = ImageResource("image:1")
    chapter = Chapter(1, "Chapter", images=(image,))
    artifact = ImageArtifact(data if data is not None else picture(), mime, image.url)

    async def scenario():
        async with executor:
            return await pipeline.process(
                site or GenericHtmlPlugin(), artifact, image, DownloadManifest("Work", (chapter,)), chapter
            )

    result = asyncio.run(scenario())
    return result, [values for event, values in records if event == "image_save_policy_unmet"]


def test_minimum_requirements_are_order_independent_and_auxiliary_settings_are_not():
    values = [
        ImageSaveOptions(min_width=1920, min_height=1080, quality=90, optimize=True),
        ImageSaveOptions(min_width=800, min_height=600, lossless=True, optimize=False),
        ImageSaveOptions(quality=80, lossless=False),
        ImageSaveOptions(),
    ]
    for order in itertools.permutations(values):
        combined = aggregate_save_options(list(order))
        assert (combined.min_width, combined.min_height, combined.quality, combined.lossless) == (1920, 1080, 90, True)
        assert combined.optimize == next(value.optimize for value in reversed(order) if value.optimize is not None)


@pytest.mark.parametrize(
    "options",
    [
        object(),
        ImageSaveOptions(min_width=0),
        ImageSaveOptions(min_height=True),
        ImageSaveOptions(quality=101),
        ImageSaveOptions(compress_level=-1),
        ImageSaveOptions(lossless=1),
        ImageSaveOptions(exif="yes"),
    ],
)
def test_invalid_requirements_identify_the_plugin_and_hook(options):
    with pytest.raises(PluginError, match="plugin=test.site, hook=image_save_options"):
        validate_save_options("test.site", "image_save_options", options)


def test_all_save_hooks_receive_the_same_final_artifact_and_private_context(core):
    calls = []
    final_bytes = picture(size=(16, 12))

    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            calls.append((artifact, context.config["owner"]))
            return ImageSaveOptions(quality=90, min_width=1920)

    class Processor:
        def transform(self, artifact, context):
            return replace(artifact, data=final_bytes, history=("processor",))

        def image_save_options(self, artifact, context):
            calls.append((artifact, context.config["owner"]))
            return ImageSaveOptions(lossless=True, min_width=800)

    result, warnings = run_pipeline(core, site=Site(), processors=(Processor(),))
    assert calls[0][0] is calls[1][0]
    assert [owner for _, owner in calls] == ["site", "processor0"]
    assert calls[0][0].data is final_bytes and result.data is final_bytes
    assert [(warning["policy_field"], warning["requested"], warning["actual"]) for warning in warnings] == [
        ("min_width", 1920, 16),
    ]


def test_jpeg_preservation_meets_quality_and_lossless_without_reencoding(core, monkeypatch):
    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(quality=90, lossless=True, min_width=4, min_height=3)

    def unexpected(*args, **kwargs):
        pytest.fail("preserving bytes must not invoke the encoder")

    monkeypatch.setattr(core, "process", unexpected)
    source = picture("JPEG")
    result, warnings = run_pipeline(core, site=Site(), data=source, mime="image/jpeg")
    assert result.data is source and not warnings


def test_cli_quality_overrides_both_minimum_quality_and_lossless_once_at_the_end(core, monkeypatch):
    calls = []
    encode = core.process
    transformed = picture(size=(16, 12))

    def observe(data, **values):
        calls.append((data, values["options"]))
        return encode(data, **values)

    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(quality=90, lossless=True)

    class Processor:
        def transform(self, artifact, context):
            assert artifact.content_type == "image/png"
            return replace(artifact, data=transformed)

    monkeypatch.setattr(core, "process", observe)
    result, warnings = run_pipeline(core, site=Site(), processors=(Processor(),), image_format="WEBP", image_quality=80)
    assert len(calls) == 1 and calls[0][0] is transformed
    assert calls[0][1].quality == 80 and calls[0][1].lossless is False
    assert result.content_type == "image/webp" and result.extension == ".webp"
    assert {warning["reason"] for warning in warnings} == {"cli_quality_below_minimum", "cli_overrides_lossless"}


@pytest.mark.parametrize("target,quality,lossless", [("JPEG", 100, None), ("WEBP", None, True)])
def test_required_conversion_uses_the_best_available_encoding(core, monkeypatch, target, quality, lossless):
    calls = []
    encode = core.process

    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(quality=90, lossless=True)

    def observe(data, **values):
        calls.append(values["options"])
        return encode(data, **values)

    monkeypatch.setattr(core, "process", observe)
    _, warnings = run_pipeline(core, site=Site(), image_format=target)
    assert len(calls) == 1 and (calls[0].quality, calls[0].lossless) == (quality, lossless)
    assert bool(warnings) == (target == "JPEG")


def test_auxiliary_options_do_not_override_lossless_jpeg_preservation(core):
    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(lossless=True, exif=False, optimize=True)

    source = picture("JPEG")
    result, warnings = run_pipeline(core, site=Site(), data=source, mime="image/jpeg")
    assert result.data is source
    assert {warning["reason"] for warning in warnings} == {"preserve_lossless_requirement"}


def test_unknown_dimensions_in_weak_mode_are_reported_without_rejecting_the_image(core):
    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(min_width=1920, min_height=1080)

    source = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    result, warnings = run_pipeline(
        core,
        site=Site(),
        data=source,
        mime="image/svg+xml",
        config=AppConfig.model_validate({"media": {"input_validation": "content_type"}}),
    )
    assert result.data is source
    assert len(warnings) == 2 and all(warning["reason"] == "dimensions_unknown" for warning in warnings)


@pytest.mark.parametrize("request_type", [DownloadRequest, WorkflowRequest])
def test_output_inputs_reject_obsolete_and_invalid_combinations(request_type):
    with pytest.raises(TypeError):
        request_type(url="https://example.test/", force_image_format="PNG")
    with pytest.raises(ValueError):
        request_type(url="https://example.test/", image_format="ORIGINAL", image_quality=80)
    for quality in (-1, 101, True, 80.0):
        with pytest.raises(ValueError):
            request_type(url="https://example.test/", image_quality=quality)
    with pytest.raises(TypeError):
        ImageResource("image:1", save_options=ImageSaveOptions())
    with pytest.raises(TypeError):
        ImageSaveOptions(format="PNG")


@pytest.mark.parametrize("configured", [False, True])
def test_requested_same_format_corrects_metadata_without_reencoding(core, monkeypatch, configured):
    def unexpected(*args, **kwargs):
        pytest.fail("the data already has the requested format")

    monkeypatch.setattr(core, "process", unexpected)
    source = picture()
    config = AppConfig.model_validate({"output": {"image_format": "PNG" if configured else None}})
    result, warnings = run_pipeline(
        core, data=source, mime="image/jpeg", config=config, image_format=None if configured else "PNG"
    )
    assert result.data is source and result.content_type == "image/png" and result.extension == ".png"
    assert not warnings


def test_explicit_original_skips_auxiliary_encoding_and_reports_it(core):
    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(optimize=True, min_width=1920)

    source = picture()
    result, warnings = run_pipeline(core, site=Site(), data=source, image_format="ORIGINAL")
    assert result.data is source
    assert {warning["reason"] for warning in warnings} == {"resolution_below_minimum", "original_preserves_bytes"}


def test_quality_for_png_is_reported_without_changing_the_format(core):
    source = picture()
    result, warnings = run_pipeline(core, data=source, image_quality=80)
    assert result.data is source and result.content_type == "image/png"
    assert [warning["reason"] for warning in warnings] == ["unsupported_option"]


@pytest.mark.parametrize(
    "configured, operation, expected",
    [
        (None, None, "PNG"),
        ("JPEG", None, "JPEG"),
        ("WEBP", None, "WEBP"),
        ("PNG", None, "PNG"),
        ("JPEG", "WEBP", "WEBP"),
        ("JPEG", "ORIGINAL", "PNG"),
        ("ORIGINAL", "JPEG", "JPEG"),
    ],
)
def test_configured_format_and_operation_precedence(core, configured, operation, expected):
    config = AppConfig.model_validate({"output": {"image_format": configured}})
    source = picture()
    result, warnings = run_pipeline(core, data=source, config=config, image_format=operation)
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.format == expected
    assert result.content_type == {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[expected]
    assert result.extension == {"PNG": ".png", "JPEG": ".jpeg", "WEBP": ".webp"}[expected]
    assert config.output.image_format == configured
    assert not warnings


@pytest.mark.parametrize("configured", [None, "ORIGINAL"])
def test_configured_original_and_null_handle_auxiliary_options_differently(core, configured, monkeypatch):
    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(optimize=True)

    calls = []
    encode = core.process

    def process(*args, **kwargs):
        calls.append(kwargs["options"])
        return encode(*args, **kwargs)

    monkeypatch.setattr(core, "process", process)
    config = AppConfig.model_validate({"output": {"image_format": configured}})
    source = picture()
    result, warnings = run_pipeline(core, data=source, site=Site(), config=config)
    if configured == "ORIGINAL":
        assert result.data is source and not calls
        assert [warning["reason"] for warning in warnings] == ["original_preserves_bytes"]
    else:
        assert len(calls) == 1 and calls[0].format == "PNG" and not warnings
    assert result.content_type == "image/png" and result.extension == ".png"


def test_direct_pipeline_rejects_configured_original_with_quality(core):
    config = AppConfig.model_validate({"output": {"image_format": "ORIGINAL"}})
    record = SimpleNamespace(id="test.site", manifest=SimpleNamespace(value={}), catalog=None)
    registry = SimpleNamespace(effective_config=lambda *_: {})
    with pytest.raises(ConfigurationError, match="output.image_format") as captured:
        ArtifactPipeline(
            config, registry, record, None, core, None, None, (), image_quality=80, transform_executor=None
        )
    assert diagnostic_for(captured.value).details["field"] == "output.image_format"


@pytest.mark.parametrize("explicit, expected", [(None, "PNG"), ("WEBP", "WEBP"), ("ORIGINAL", "ORIGINAL")])
def test_direct_pipeline_resolves_configured_format(core, explicit, expected):
    config = AppConfig.model_validate({"output": {"image_format": "PNG"}})
    record = SimpleNamespace(id="test.site", manifest=SimpleNamespace(value={}), catalog=None)
    registry = SimpleNamespace(effective_config=lambda *_: {})
    pipeline = ArtifactPipeline(
        config, registry, record, None, core, None, None, (), image_format=explicit, transform_executor=None
    )
    assert pipeline.image_format == expected and config.output.image_format == "PNG"


def test_explicit_format_does_not_trust_unverified_mime_in_weak_mode(core):
    config = AppConfig.model_validate({"media": {"input_validation": "content_type"}})
    with pytest.raises(ImageDecodeError):
        run_pipeline(core, data=b"not a PNG", mime="image/png", image_format="PNG", config=config)


def test_save_hook_rejects_async_at_load_time_and_awaitable_at_runtime():
    from image_downloader.plugins.lifecycle import PluginClassLoader

    class AsyncHook:
        async def image_save_options(self, artifact, context):
            return ImageSaveOptions()

    with pytest.raises(PluginError, match="image_save_options must be synchronous"):
        PluginClassLoader._require_save_options(AsyncHook())

    class AwaitableHook:
        def image_save_options(self, artifact, context):
            async def result():
                return ImageSaveOptions()

            return result()

    async def scenario():
        async with TransformExecutor() as executor:
            with pytest.raises(PluginError, match="synchronous"):
                await PluginInvoker("test.site").image_save_options(
                    AwaitableHook(),
                    ImageArtifact(b"test", "image/png", "image:1"),
                    None,
                    chapter_id="1",
                    executor=executor,
                )

    asyncio.run(scenario())


def test_lossless_conversion_preserves_all_animation_frames(core):
    buffer = io.BytesIO()
    first = Image.new("RGB", (8, 6), "red")
    second = Image.new("RGB", (8, 6), "blue")
    first.save(buffer, format="GIF", save_all=True, append_images=[second], duration=[100, 200], loop=0)

    class Site(GenericHtmlPlugin):
        def image_save_options(self, artifact, context):
            return ImageSaveOptions(lossless=True)

    result, warnings = run_pipeline(core, site=Site(), data=buffer.getvalue(), mime="image/gif", image_format="WEBP")
    assert not warnings
    with Image.open(io.BytesIO(result.data)) as output:
        assert output.n_frames == 2
        assert output.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        output.seek(1)
        assert output.convert("RGB").getpixel((0, 0)) == (0, 0, 255)


def test_unmet_requirements_are_successful_and_recorded_in_the_chapter_log(tmp_path):
    from support.workflow import FEED, A, compose
    from support.workflow_retry import RetryGallery, manifest, transport

    async def scenario():
        gallery = RetryGallery()
        gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))
        gallery.image_save_options = lambda artifact, context: ImageSaveOptions(min_width=1920, min_height=1080)
        async with compose(tmp_path, gallery) as service:
            await transport(service, gallery)
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retry_delay=0))
        item = result.items[0]
        assert item.status == "success" and len(item.attempts) == 1
        assert not item.download.failures
        chapter_log = Path(item.download.saved_files[0]).parent / "log.log"
        contents = chapter_log.read_text(encoding="utf-8")
        assert "event=image_save_policy_unmet" in contents
        assert "field=min_width requested=1920 actual=2 reason=resolution_below_minimum" in contents
        assert "field=min_height requested=1080 actual=2 reason=resolution_below_minimum" in contents

    asyncio.run(scenario())
