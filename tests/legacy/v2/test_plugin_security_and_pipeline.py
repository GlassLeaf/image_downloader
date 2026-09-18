from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from image_downloader.exceptions import PluginError
from image_downloader.media import ImageProcessContext, ImageTransformPipeline
from image_downloader.models import ImageArtifact, ImageResource
from image_downloader.plugins.trust import PluginCatalog, PluginVerifier, TrustedPlugin


class _Distribution:
    def __init__(self, root: Path) -> None:
        self.metadata = {"Name": "fixture-plugin"}
        self.files = [PurePosixPath("fixture.py")]
        self.root = root

    def locate_file(self, item: PurePosixPath) -> Path:
        return self.root / str(item)


class _Transform:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name, self.calls = name, calls

    async def transform(self, artifact: ImageArtifact, _context: ImageProcessContext) -> ImageArtifact:
        self.calls.append(self.name)
        return artifact


def test_catalog_verifies_signature_and_installed_file_tree(tmp_path: Path) -> None:
    plugin = tmp_path / "fixture.py"
    plugin.write_text("value = 1\n", encoding="utf-8")
    manifest = {"files": {"fixture.py": hashlib.sha256(plugin.read_bytes()).hexdigest()}}
    private = Ed25519PrivateKey.generate()
    entry = TrustedPlugin(
        "fixture.plugin",
        "fixture-plugin",
        "Fixture",
        base64.b64encode(
            private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ).decode(),
        manifest,
        base64.b64encode(private.sign(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())).decode(),
        frozenset({"site-downloader"}),
    )
    verifier = PluginVerifier(PluginCatalog([entry]))
    assert verifier.verify(_Distribution(tmp_path), capability="site-downloader") == entry
    plugin.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(PluginError, match="verification failed"):
        verifier.verify(_Distribution(tmp_path), capability="site-downloader")


def test_image_pipeline_runs_site_then_configured_processors(tmp_path: Path) -> None:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(buffer, format="PNG")
    calls: list[str] = []
    site = _Transform("site:fixture", calls)
    common = _Transform("fixture.strip-metadata", calls)
    pipeline = ImageTransformPipeline(resolver=lambda name: common if name == common.name else None)

    async def scenario() -> None:
        result = await pipeline.process(
            ImageArtifact(buffer.getvalue(), "image/png", "https://example.test/a.png", "a"),
            image=ImageResource("https://example.test/a.png", image_id="a"),
            plugin_config={},
            site_transform=site,
            chain=[common.name],
        )
        assert result.extension == ".png"
        assert calls == ["site:fixture", "fixture.strip-metadata"]
        assert result.history == ("site:fixture", "core.decode-normalize", "fixture.strip-metadata", "core.final-validate")

    asyncio.run(scenario())
