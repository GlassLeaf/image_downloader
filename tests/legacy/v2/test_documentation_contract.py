from __future__ import annotations

from pathlib import Path

import image_downloader


def test_plugin_api_reference_matches_public_v11_contract() -> None:
    document = (Path(__file__).parents[1] / "docs" / "plugin-api-reference.md").read_text(encoding="utf-8")
    for symbol in (
        "BaseDownloader",
        "PluginContext",
        "PluginSecrets",
        "RequestSpec",
        "RequestResponse",
        "ParseResult",
        "Chapter",
        "ImageResource",
        "UpdatedUrl",
        "UpdateResult",
        "UnsupportedSiteFeature",
    ):
        assert symbol in document
        assert hasattr(image_downloader, symbol)
    for obsolete in ("RequestResponse.text", "RequestResponse.json()", "ContentMetadata", "ChapterResource", "UpdateItem"):
        assert obsolete not in document
