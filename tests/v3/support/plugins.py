from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from image_downloader.security import (
    canonical_jcs,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def make_plugin(
    root: Path,
    *,
    plugin_id: str = "com.example.gallery",
    kind: str = "site_plugin",
    priority: int = 1,
    configured_match: bool = False,
    runnable: bool = False,
) -> Path:
    folder = "site_plugins" if kind == "site_plugin" else "image_processor_plugins"
    directory = root / folder / plugin_id.rsplit(".", 1)[-1]
    directory.mkdir(parents=True)
    source_name = "entry.source"
    source = ("import logging\nfrom image_downloader.models import DownloadManifest\n\n" if runnable else "") + (
        "class Entry:\n"
        "    def validate_config(self, config, app_settings):\n"
        "        if 'author' not in config: raise ValueError('config')\n"
        "        return None\n"
    )
    if kind == "site_plugin":
        inspect_source = (
            "    async def inspect(self, url, context):\n"
            "        logging.getLogger(__name__).warning('plugin-operation-marker')\n"
            "        return DownloadManifest('logged', ())\n"
            if runnable
            else "    async def inspect(self, url, context): raise AssertionError('not used')\n"
        )
        source += (
            "    def matches(self, url): return 'example.test' in url\n"
            + inspect_source
            + "    async def create_image_request(self, image, context): raise AssertionError('not used')\n"
            "    async def recover_image_request(self, image, failed, response, context): return None\n"
            "    def auth_flow(self, context): return None\n"
            "    async def transform_image(self, artifact, context): return artifact\n"
        )
        if configured_match:
            source += (
                "    def matches_with_config(self, url, config, app_settings):\n"
                "        return str(config.get('alternate_host', '')) in url\n"
            )
    else:
        source += "    async def transform(self, artifact, context): return artifact\n"
    author_name = "entry.yaml"
    files = {source_name: source.encode(), author_name: b"config:\n  author: true\n  nested:\n    one: 1\n"}
    for name, content in files.items():
        (directory / name).write_bytes(content)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    tree = {name: _digest(content) for name, content in files.items()}
    manifest = {
        "schema_version": 1,
        "id": plugin_id,
        "publisher": "com.example",
        "version": "1.0",
        "api_version": "3",
        "kind": kind,
        "capabilities": ["reserved"],
        "match_priority": priority,
        "entry": {"file": source_name, "class": "Entry"},
        "config_file": author_name,
        "public_key": base64.b64encode(public).decode(),
        "key_id": _digest(public),
        "file_tree": tree,
        "file_tree_sha256": _digest(canonical_jcs(tree)),
    }
    wrapper = {"manifest": manifest, "signature": base64.b64encode(private.sign(canonical_jcs(manifest))).decode()}
    (directory / "manifest.json").write_text(json.dumps(wrapper), encoding="utf-8")
    return directory


def make_minimal_plugin(root: Path, *, plugin_id: str = "com.example.minimal") -> Path:
    directory = make_plugin(root, plugin_id=plugin_id)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "manifest": {
                    "id": plugin_id,
                    "kind": "site_plugin",
                    "entry": {"file": "entry.source", "class": "Entry"},
                }
            }
        ),
        encoding="utf-8",
    )
    return directory
