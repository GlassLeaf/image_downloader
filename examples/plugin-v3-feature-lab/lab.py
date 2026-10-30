"""Local fictional feature lab. This entry point never reads a browser profile."""

from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
import threading
from pathlib import Path

import yaml
from server import LabSite

from image_downloader.security import install_plugin

ROOT = Path(__file__).resolve().parent
SLUGS = (
    "additional-files",
    "credential-gallery",
    "cookie-gallery",
    "origin-auth-gallery",
    "output-gallery",
    "configured-host-gallery",
    "short-lived-gallery",
)
SECRET_UNITS = ("credential-gallery", "origin-auth-gallery")


def plugin_id(slug: str) -> str:
    return f"local.image-downloader.lab-{slug}"


def environment_name(slug: str) -> str:
    return f"IMAGE_DOWNLOADER_PLUGIN_{plugin_id(slug).encode().hex().upper()}_{b'LAB_TOKEN'.hex().upper()}"


def prepare(work: Path, origin: str, asset_origin: str) -> Path:
    """Copy all sample packages; never sign or create a private key implicitly."""
    work.mkdir(parents=True, exist_ok=True)
    sources = work / "sources"
    if sources.exists():
        raise ValueError("lab sources already exist; choose a fresh work directory")
    sources.mkdir()
    for slug in SLUGS:
        source = ROOT.parent / "plugin-v3-additional-files" if slug == "additional-files" else ROOT / slug
        shutil.copytree(source, sources / slug, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    config = {
        "storage": {"data_root": str(work / "data")},
        "plugins": {"root": str(work / "plugins")},
        "security": {"plugin_verification": "off"},
        "logging": {"console": {"enabled": False}},
        "notification": {"enabled": False},
        "network": {"max_attempts": 1},
        "output": {
            "isolate_by_plugin": True,
            "existing_file": "skip",
            "directory_format": "%CHAPTER_NUMBER%_%CHAPTER_TITLE%",
            "filename_format": "%IMAGE_INDEX%_%ORIGINAL_STEM%.%EXT%",
        },
        "plugin_settings": {
            plugin_id(slug): {"config": {"origin": origin, "asset_origin": asset_origin}} for slug in SLUGS
        },
    }
    for slug in SECRET_UNITS:
        config["plugin_settings"][plugin_id(slug)]["secrets"] = {"api_token": "LAB_TOKEN"}
    config["plugin_settings"][plugin_id("short-lived-gallery")]["download_policy"] = {
        "preserve_image_start_order": True
    }
    target = work / "app.yaml"
    target.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    install(work, strict=False)
    config["output"]["filename_format"] = (
        f"%IMAGE_INDEX%_%ORIGINAL_STEM%%PLUGIN[{plugin_id('output-gallery')}:VARIANT]%.%EXT%"
    )
    (work / "output.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return target


def install(work: Path, *, strict: bool) -> None:
    for slug in SLUGS:
        install_plugin(work / "plugins", work / "sources" / slug, mode="strict" if strict else "bypass-all")
    for name in ("app.yaml", "output.yaml"):
        target = work / name
        if target.exists():
            config = yaml.safe_load(target.read_text(encoding="utf-8"))
            config["security"]["plugin_verification"] = "strict" if strict else "off"
            target.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


def credentials(action: str) -> None:
    if not sys.stdin.isatty():
        raise ValueError("credentials command requires an interactive local terminal")
    import keyring

    if action == "set":
        value = getpass.getpass("Local dummy token (enter lab-token): ")
        if value != "lab-token":
            raise ValueError("use the fictional lab token only")
        for slug in SECRET_UNITS:
            keyring.set_password(f"image-downloader.plugin.{plugin_id(slug)}", "LAB_TOKEN", value)
    else:
        for slug in SECRET_UNITS:
            keyring.delete_password(f"image-downloader.plugin.{plugin_id(slug)}", "LAB_TOKEN")
    print("PASS LAB_CREDENTIALS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("serve", "prepare", "install", "credentials-set", "credentials-delete", "env-names")
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--workflow-failure", action="store_true")
    args = parser.parse_args()
    if args.action == "env-names":
        for slug in SECRET_UNITS:
            print(f"{slug}: {environment_name(slug)}")
        return 0
    if args.action.startswith("credentials-"):
        credentials(args.action.split("-", 1)[1])
        return 0
    from image_downloader.storage.path_safety import existing_directory

    if args.work_dir is None:
        parser.error("--work-dir ABSOLUTE_PATH is required")
    work = existing_directory(args.work_dir, "lab work directory")
    if args.action == "serve":
        work.mkdir(parents=True, exist_ok=True)
        with LabSite() as site:
            site.fail_workflow_once = args.workflow_failure
            (work / "endpoints.json").write_text(
                json.dumps({"origin": site.origin, "asset_origin": site.asset_origin}), encoding="utf-8"
            )
            print(f"Local fictional site: {site.origin}", flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
    elif args.action == "prepare":
        endpoints = json.loads((work / "endpoints.json").read_text(encoding="utf-8"))
        print(prepare(work, endpoints["origin"], endpoints["asset_origin"]))
    else:
        install(work, strict=args.strict)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("FAIL LAB_SETUP", file=sys.stderr)
        raise SystemExit(1) from None
