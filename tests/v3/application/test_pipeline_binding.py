"""Prepared settings remain private and immutable across images and attempts."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from image_downloader.configuration.models import AppConfig
from image_downloader.media.pipeline_binding import PreparedPipelineBinding
from image_downloader.media.processor_chain import PreparedProcessor
from image_downloader.plugins.runtime import safe_app_settings


def test_prepared_settings_are_detached_and_next_attempt_observes_changes():
    config = AppConfig.model_validate(
        {
            "network": {"headers": {"Authorization": "private"}, "proxy": "http://proxy.test"},
            "image_processors": {"transport_metadata_access": {"test.processor": ["test.site"]}},
        }
    )
    plugin_config = {"nested": {"items": [1, 2]}}
    manifest, catalog = {"entry": {"class": "First"}}, {"selection_priority": 1}

    def prepare():
        return PreparedPipelineBinding.prepare(
            config, "test.site", plugin_config, safe_app_settings(config), manifest, catalog
        )

    first = prepare()
    config = config.model_copy(update={
        "media": config.media.model_copy(update={"max_image_pixels": 100}),
        "output": config.output.model_copy(update={"image_format": "JPEG"}),
        "image_processors": config.image_processors.model_copy(update={"transport_metadata_access": {}}),
    })
    plugin_config["nested"]["items"].append(3)
    manifest["entry"]["class"] = "Second"
    catalog["selection_priority"] = 2
    second = prepare()
    assert first.config["nested"]["items"] == (1, 2)
    assert second.config["nested"]["items"] == (1, 2, 3)
    assert first.manifest["entry"]["class"] == "First"
    assert second.manifest["entry"]["class"] == "Second"
    assert first.catalog["selection_priority"] == 1
    assert second.catalog["selection_priority"] == 2
    assert first.max_image_pixels is None and second.max_image_pixels == 100
    assert first.image_format == "ORIGINAL" and second.image_format == "JPEG"
    assert first.metadata_processors == frozenset({"test.processor"})
    assert second.metadata_processors == frozenset()
    assert "headers" not in first.app_settings["network"]
    assert "proxy" not in first.app_settings["network"]
    with pytest.raises(TypeError):
        first.config["nested"]["items"] = ()
    with pytest.raises(FrozenInstanceError):
        first.plugin_id = "changed"


def test_processor_binding_takes_independent_manifest_catalog_and_setting_snapshots():
    manifest = {"entry": {"class": "Original"}}
    catalog = {"selection_priority": 1}
    config = {"options": [1]}
    record = SimpleNamespace(manifest=SimpleNamespace(value=manifest), catalog=SimpleNamespace(as_json=lambda: catalog))
    binding = PreparedProcessor("test.processor", record, object(), config, {"media": {"max_image_pixels": None}})
    manifest["entry"]["class"] = "Changed"
    catalog["selection_priority"] = 2
    config["options"].append(2)
    assert binding.plugin_manifest["entry"]["class"] == "Original"
    assert binding.catalog["selection_priority"] == 1
    assert binding.config["options"] == (1,)
