"""Create a local Ed25519 key when requested and sign v3 site plugin units."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from image_downloader.plugins.plugin_manifest import collect_plugin_file_tree


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_key(path: Path, create: bool) -> Ed25519PrivateKey:
    if path.exists():
        value = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(value, Ed25519PrivateKey):
            raise ValueError("signing key is not an Ed25519 private key")
        return value
    if not create:
        raise FileNotFoundError(f"signing key does not exist: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    os.chmod(path, 0o600)
    return key


def file_tree(directory: Path) -> dict[str, str]:
    return collect_plugin_file_tree(directory)


def sign(directory: Path, key: Ed25519PrivateKey) -> None:
    metadata_path = directory / "plugin-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected = {"id", "publisher", "version", "kind", "capabilities", "match_priority", "entry", "config_file"}
    if not isinstance(metadata, dict) or set(metadata) != expected:
        raise ValueError(f"invalid plugin metadata: {metadata_path}")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    tree = file_tree(directory)
    manifest = {
        "schema_version": 1,
        **metadata,
        "api_version": "3",
        "public_key": base64.b64encode(public).decode("ascii"),
        "key_id": sha256(public),
        "file_tree": tree,
        "file_tree_sha256": sha256(canonical_json(tree)),
    }
    wrapper = {"manifest": manifest, "signature": base64.b64encode(key.sign(canonical_json(manifest))).decode("ascii")}
    (directory / "manifest.json").write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--create-key", action="store_true")
    parser.add_argument("plugin_directories", type=Path, nargs="+")
    args = parser.parse_args()
    key = load_key(args.key.resolve(), args.create_key)
    for directory in args.plugin_directories:
        sign(directory.resolve(), key)
        print(f"signed {directory.resolve()}")


if __name__ == "__main__":
    main()
