from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.sign_local_site_plugin import file_tree, sign

from image_downloader.plugins.plugin_manifest import (
    collect_plugin_file_tree,
    read_manifest,
    verify_signed_plugin_source,
)


def test_signer_and_verifier_cover_the_same_plugin_files(tmp_path: Path) -> None:
    (tmp_path / "plugin.py").write_text("class Site: pass\n", encoding="utf-8")
    (tmp_path / "plugin.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("old manifest\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "manifest.json").write_text("nested metadata\n", encoding="utf-8")
    (tmp_path / "loose.pyc").write_bytes(b"outside cache")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "notes.txt").write_text("keep\n", encoding="utf-8")
    (tmp_path / "__pycache__" / "plugin.pyc").write_bytes(b"ignored cache")
    metadata = {
        "id": "local.example.gallery",
        "publisher": "local.example",
        "version": "1.0.0",
        "kind": "site_plugin",
        "capabilities": [],
        "match_priority": 0,
        "entry": {"file": "plugin.py", "class": "Site"},
        "config_file": "plugin.yaml",
    }
    (tmp_path / "plugin-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    tree = file_tree(tmp_path)
    assert tree == collect_plugin_file_tree(tmp_path)
    assert "manifest.json" not in tree
    assert "nested/manifest.json" in tree
    assert "loose.pyc" in tree
    assert "__pycache__/notes.txt" in tree
    assert "__pycache__/plugin.pyc" not in tree

    sign(tmp_path, Ed25519PrivateKey.generate())
    verify_signed_plugin_source(read_manifest(tmp_path))


def test_metadata_template_matches_manifest_template_and_signs(tmp_path: Path, repository_root: Path) -> None:
    template = repository_root / "examples" / "plugin-v3-template"
    metadata = json.loads((template / "plugin-metadata.json.example").read_text(encoding="utf-8"))
    wrapper = json.loads((template / "manifest.json.example").read_text(encoding="utf-8"))
    manifest = wrapper["manifest"]
    expected = {"id", "publisher", "version", "kind", "capabilities", "match_priority", "entry", "config_file"}

    assert set(metadata) == expected
    assert metadata == {field: manifest[field] for field in expected}

    for name in ("sample_plugin.py", "sample_plugin.yaml"):
        (tmp_path / name).write_bytes((template / name).read_bytes())
    (tmp_path / "plugin-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    sign(tmp_path, Ed25519PrivateKey.generate())
    verify_signed_plugin_source(read_manifest(tmp_path))
