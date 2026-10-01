from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from image_downloader.plugins.plugin_manifest import read_manifest, verify_signed_plugin_source


def test_signed_plugins_survive_windows_line_ending_conversion(repository_root: Path, tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required to exercise checkout line ending conversion")
    checkout = tmp_path / "checkout"
    subprocess.run(
        [
            git, "clone", "--quiet", "--no-checkout", "--no-hardlinks", "-c", "core.autocrlf=true",
            str(repository_root), str(checkout),
        ],
        check=True,
        capture_output=True,
    )

    def run_git(*arguments: str) -> None:
        subprocess.run([git, "-C", str(checkout), *arguments], check=True, capture_output=True)

    run_git("read-tree", "HEAD")
    # Include attributes being edited locally as well as committed CI settings.
    shutil.copyfile(repository_root / ".gitattributes", checkout / ".gitattributes")
    run_git("add", ".gitattributes")
    run_git("checkout-index", "--all", "--force")
    manifests = sorted((checkout / "plugin-sources").glob("*/manifest.json"))
    assert manifests
    for manifest in manifests:
        verify_signed_plugin_source(read_manifest(manifest.parent))
