from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image
from tools.sign_local_site_plugin import sign

from image_downloader import (
    ImageArtifact,
    ImageFetchRequest,
    ImageTransportMetadata,
    RequestResponse,
    TransformContext,
    TransportRequestMetadata,
)
from image_downloader.plugins.plugin_manifest import read_manifest, verify_signed_plugin_source

EXAMPLE_ROOT = Path(__file__).resolve().parents[3] / "examples" / "plugin-v3-metadata-bridge"


def _load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _png(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, format="PNG")
    return output.getvalue()


def test_metadata_bridge_examples_are_signable_plugin_packages(tmp_path: Path) -> None:
    for directory_name in ("site-plugin", "processor-plugin"):
        package = tmp_path / directory_name
        shutil.copytree(EXAMPLE_ROOT / directory_name, package)
        sign(package, Ed25519PrivateKey.generate())
        verify_signed_plugin_source(read_manifest(package))


def test_site_metadata_drives_request_transform_and_authorized_processor() -> None:
    site_module = _load_module(
        "metadata_bridge_gallery_example", EXAMPLE_ROOT / "site-plugin" / "metadata_bridge_gallery.py"
    )
    processor_module = _load_module(
        "metadata_bridge_processor_example", EXAMPLE_ROOT / "processor-plugin" / "metadata_bridge_processor.py"
    )

    class Requests:
        async def execute(self, spec: object) -> RequestResponse:
            assert spec.url == "https://metadata-bridge.example.test/api/works/42"
            return RequestResponse(
                "https://metadata-bridge.example.test/api/works/42",
                200,
                {},
                json.dumps(
                    {
                        "id": "42",
                        "title": "Metadata Book",
                        "images": [{"id": "image-1", "variant": "full", "orientation": 6}],
                    }
                ).encode(),
            )

    async def scenario() -> None:
        site = site_module.MetadataBridgeGalleryPlugin()
        manifest = await site.inspect(
            "https://metadata-bridge.example.test/works/42", SimpleNamespace(requests=Requests())
        )
        image = manifest.chapters[0].images[0]
        assert image.metadata == {"variant": "full", "orientation": "6"}

        image_request = await site.create_image_request(image, SimpleNamespace())
        assert isinstance(image_request, ImageFetchRequest)
        assert image_request.request.url.endswith("/api/images/image-1/download")
        assert image_request.request.query == {"variant": "full"}
        assert image_request.plugin_data == {"variant": "full", "orientation": "6"}

        context = TransformContext(image, {}, {}, {}, None, manifest, manifest.chapters[0])
        rotated = await site.transform_image(
            ImageArtifact(_png(2, 3), "image/png", image.url, image_id=image.image_id, extension="png"), context
        )
        with Image.open(BytesIO(rotated.data)) as output:
            assert output.size == (3, 2)
        assert rotated.history == ("site-orientation-normalized",)

        request_metadata = TransportRequestMetadata("https://metadata-bridge.example.test/image", {})
        transport_metadata = ImageTransportMetadata(
            request_metadata,
            request_metadata,
            "https://metadata-bridge.example.test/image",
            {},
            image_request.plugin_data,
        )
        processor_context = TransformContext(
            image,
            {},
            {},
            {},
            None,
            manifest,
            manifest.chapters[0],
            site_manifest={"id": "local.image-downloader.metadata-bridge-gallery"},
            transport_metadata=transport_metadata,
        )
        processed = await processor_module.MetadataBridgeProcessor().transform(rotated, processor_context)
        assert processed.history == ("site-orientation-normalized", "metadata-bridge:profile-applied")

        redacted_context = TransformContext(
            image,
            {},
            {},
            {},
            None,
            manifest,
            manifest.chapters[0],
            transport_metadata=ImageTransportMetadata(
                request_metadata,
                request_metadata,
                "https://metadata-bridge.example.test/image",
                {},
                {"variant": "[REDACTED]", "orientation": "[REDACTED]"},
                is_redacted=True,
            ),
        )
        redacted = await processor_module.MetadataBridgeProcessor().transform(rotated, redacted_context)
        assert redacted.history == ("site-orientation-normalized", "metadata-bridge:transport-metadata-redacted")

    asyncio.run(scenario())
