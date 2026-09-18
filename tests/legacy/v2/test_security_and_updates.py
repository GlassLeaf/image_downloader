from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from image_downloader import (
    AppConfig,
    PluginDescriptor,
    UpdateCandidate,
    UpdateChangeKind,
    UpdateSnapshot,
)
from image_downloader.config import resolve_paths
from image_downloader.exceptions import ConfigurationError, PluginError
from image_downloader.runtime import DownloadService
from image_downloader.security import (
    ImageProcessorRegistry,
    PluginCatalog,
    RegisteredPlugin,
    V2PluginRegistry,
    canonical_jcs,
    read_manifest,
    secure_catalog_path,
    verify_manifest,
)


class Site:
    descriptor = PluginDescriptor("site", 1)

    def matches(self, url):
        return True

    async def inspect(self, url, context):
        raise AssertionError("not used")

    async def create_image_request(self, image, context):
        raise AssertionError("not used")

    async def recover_image_request(self, image, failed, response, context):
        return None

    def auth_flow(self, context):
        return None

    async def transform_image(self, artifact, context):
        return artifact


def test_priority_generic_fallback_and_tie_rejection() -> None:
    class Low(Site):
        descriptor = PluginDescriptor("low", 1)

    class High(Site):
        descriptor = PluginDescriptor("high", 2)

    registry = V2PluginRegistry(mode="off", catalog=None)
    registry._plugins.extend((RegisteredPlugin(Low, "low", 100), RegisteredPlugin(High, "high", 0)))
    assert registry.resolve("https://example.test").descriptor.id == "high"

    class SameA(Site):
        descriptor = PluginDescriptor("same-a", 2)

    class SameB(Site):
        descriptor = PluginDescriptor("same-b", 2)

    tie = V2PluginRegistry(mode="off", catalog=None)
    tie._plugins.extend((RegisteredPlugin(SameA, "same-a", 3), RegisteredPlugin(SameB, "same-b", 3)))
    with pytest.raises(PluginError, match="priority conflict"):
        tie.resolve("https://example.test")


def test_catalog_path_security_and_jcs(tmp_path: Path) -> None:
    catalog = tmp_path / "plugins.catalog.json"
    catalog.write_text('{"schema_version":1,"plugins":[]}', encoding="utf-8")
    assert secure_catalog_path(tmp_path, catalog.name) == catalog
    for invalid in ("../plugins.catalog.json", str(catalog.resolve()), "C:relative.json"):
        with pytest.raises(ConfigurationError):
            secure_catalog_path(tmp_path, invalid)
    assert canonical_jcs({"b": "値", "a": ["x"]}) == '{"a":["x"],"b":"値"}'.encode()


def test_signed_sidecar_catalog_and_file_tree_verification(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    dist_info = tmp_path / "demo.dist-info"
    dist_info.mkdir()
    module = tmp_path / "demo.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = hashlib.sha256(public).hexdigest()
    tree = {"demo.py": hashlib.sha256(module.read_bytes()).hexdigest()}
    manifest = {
        "schema_version": 1,
        "id": "site",
        "distribution": "demo",
        "publisher": "publisher",
        "api_version": "2",
        "key_id": key_id,
        "capabilities": ["site-downloader"],
        "file_tree": tree,
        "file_tree_sha256": hashlib.sha256(canonical_jcs(tree)).hexdigest(),
    }
    sidecar = {
        "manifest": manifest,
        "signature": base64.b64encode(private.sign(canonical_jcs(manifest))).decode("ascii"),
    }
    (dist_info / "image_downloader_plugin.json").write_text(json.dumps(sidecar), encoding="utf-8")

    class File:
        def __init__(self, path: str, parent: str) -> None:
            self.name, self.parent, self.path = path.rsplit("/", 1)[-1], SimpleNamespace(name=parent), path

        def as_posix(self) -> str:
            return self.path

    class Distribution:
        files = (File("demo.dist-info/image_downloader_plugin.json", "demo.dist-info"), File("demo.py", ""))
        metadata = {"Name": "demo"}

        def locate_file(self, item):
            return tmp_path / item.as_posix()

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugins": [
                    {
                        "id": "site",
                        "distribution": "demo",
                        "publisher": "publisher",
                        "key_id": key_id,
                        "public_key": base64.b64encode(public).decode("ascii"),
                        "capabilities": ["site-downloader"],
                        "manifest_digest": hashlib.sha256(canonical_jcs(manifest)).hexdigest(),
                        "file_tree_sha256": manifest["file_tree_sha256"],
                        "selection_priority": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    distribution = Distribution()
    verified = verify_manifest(
        distribution, read_manifest(distribution), PluginCatalog.load(catalog_path), "site-downloader"
    )
    assert verified.selection_priority == 3


def test_missing_v2_sidecar_is_rejected_before_entrypoint_import(monkeypatch) -> None:
    class Entry:
        name = "legacy"
        dist = SimpleNamespace(files=(), metadata={"Name": "legacy"})
        loaded = False

        def load(self):
            self.loaded = True
            return Site

    entry = Entry()
    monkeypatch.setattr("image_downloader.security.entry_points", lambda: SimpleNamespace(select=lambda **_: (entry,)))
    registry = V2PluginRegistry(mode="off", catalog=None)
    registry.load_entry_points()
    assert not entry.loaded
    assert registry.diagnostics[-1].loaded is False


def test_warn_loads_structural_v2_plugin_without_catalog(monkeypatch, tmp_path: Path) -> None:
    dist_info = tmp_path / "demo.dist-info"
    dist_info.mkdir()
    manifest = {
        "schema_version": 1,
        "id": "site",
        "distribution": "demo",
        "publisher": "example",
        "api_version": "2",
        "key_id": "unused",
        "capabilities": ["site-downloader"],
        "file_tree": {},
        "file_tree_sha256": "unused",
    }
    (dist_info / "image_downloader_plugin.json").write_text(
        json.dumps({"manifest": manifest, "signature": base64.b64encode(b"not-verified").decode("ascii")}),
        encoding="utf-8",
    )

    class PackageFile:
        name = "image_downloader_plugin.json"
        parent = SimpleNamespace(name="demo.dist-info")

        def as_posix(self):
            return "demo.dist-info/image_downloader_plugin.json"

    class Entry:
        name = "demo"
        dist = SimpleNamespace(
            files=(PackageFile(),), metadata={"Name": "demo"}, locate_file=lambda item: dist_info / item.name
        )

        def load(self):
            return Site

    monkeypatch.setattr(
        "image_downloader.security.entry_points", lambda: SimpleNamespace(select=lambda **_: (Entry(),))
    )
    registry = V2PluginRegistry(mode="warn", catalog=None)
    registry.load_entry_points()
    assert registry.resolve("https://example.test").descriptor.id == "site"
    assert any(item.warning and item.loaded for item in registry.diagnostics)


def test_update_snapshot_fallback_key_removal_and_atomic_state(tmp_path: Path) -> None:
    class UpdatingSite(Site):
        descriptor = PluginDescriptor("updates", 1)
        snapshot = UpdateSnapshot("https://example.test/list", (), datetime.now(UTC))

        async def check_updates(self, url, context):
            return type(self).snapshot

    settings = AppConfig.model_validate(
        {"security": {"plugin_verification": "off"}, "logging": {"console": {"enabled": False}}}
    )

    async def check() -> None:
        registry = V2PluginRegistry(mode="off", catalog=None)
        registry._plugins.append(RegisteredPlugin(UpdatingSite, "updates", 0))
        service = DownloadService(
            settings,
            resolve_paths(settings, base_dir=tmp_path),
            registry,
            ImageProcessorRegistry(mode="off", catalog=None),
        )
        try:
            UpdatingSite.snapshot = UpdateSnapshot(
                "https://example.test/list",
                (UpdateCandidate("https://example.test/item?a=1", revision="one"),),
                datetime.now(UTC),
            )
            first = await service.check_updates("https://example.test/list")
            assert first.changes[0].kind is UpdateChangeKind.ADDED
            UpdatingSite.snapshot = UpdateSnapshot("https://example.test/list", (), datetime.now(UTC))
            second = await service.check_updates("https://example.test/list")
            assert second.changes[0].kind is UpdateChangeKind.REMOVED
            assert (Path(service.paths["state"]) / "updates.json").exists()
        finally:
            await service.close()

    asyncio.run(check())
