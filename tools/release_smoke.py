"""Check beta artifacts through a non-editable installation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {"__pycache__", ".local", ".runtime", "profiles", "image_downloader.und"}
ROOT_MODULES = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "config.py",
    "exceptions.py",
    "immutable.py",
    "models.py",
    "ports.py",
    "runtime.py",
    "security.py",
}
OBSOLETE_MODULES = {"commands/handlers.py", "observability/log_safety.py"}


def _artifacts() -> tuple[Path, Path, str]:
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = str(project["version"])
    wheel = REPOSITORY / "dist" / f"image_downloader-{version}-py3-none-any.whl"
    sdist = REPOSITORY / "dist" / f"image_downloader-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise RuntimeError("build both wheel and sdist before running release smoke")
    return wheel, sdist, version


def _check_archive_names(names: list[str], *, source_archive: bool) -> None:
    if any(FORBIDDEN_PARTS.intersection(Path(name).parts) for name in names):
        raise RuntimeError("distribution contains local or generated data")
    for name in names:
        parts = Path(name).parts
        if "image_downloader" not in parts:
            continue
        package_index = parts.index("image_downloader")
        if len(parts) == package_index + 2 and parts[-1].endswith(".py") and parts[-1] not in ROOT_MODULES:
            raise RuntimeError(f"distribution contains an obsolete root module: {parts[-1]}")
        if any(name.endswith(f"image_downloader/{module}") for module in OBSOLETE_MODULES):
            raise RuntimeError(f"distribution contains an obsolete module: {name}")
    if not any(name.endswith("image_downloader/app.yaml") for name in names):
        raise RuntimeError("distribution is missing bundled configuration")
    required_modules = (
        "application/service.py",
        "commands/dispatch.py",
        "commands/download.py",
        "commands/cookie.py",
        "commands/doctor.py",
        "commands/config.py",
        "commands/plugin.py",
        "configuration/layers.py",
        "credentials/plugin_secrets.py",
        "media/artifact_pipeline.py",
        "observability/chapter_reporter.py",
        "plugins/management.py",
        "plugins/plugin_manifest.py",
        "plugins/runtime.py",
        "privacy/log_safety.py",
        "storage/path_safety.py",
        "transport/gateway.py",
    )
    for module in required_modules:
        if not any(name.endswith(f"image_downloader/{module}") for name in names):
            raise RuntimeError(f"distribution is missing reorganized module: {module}")
    if source_archive and any("archive/docs/legacy/" in name for name in names):
        raise RuntimeError("historical documentation unexpectedly entered the source distribution")


def _run(*arguments: str) -> str:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True)
    return result.stdout


def verify(*, offline: bool) -> None:
    wheel, sdist, version = _artifacts()
    with zipfile.ZipFile(wheel) as package:
        _check_archive_names(package.namelist(), source_archive=False)
    with tarfile.open(sdist, "r:gz") as package:
        _check_archive_names(package.getnames(), source_archive=True)

    with tempfile.TemporaryDirectory(prefix="image-downloader-beta-smoke-") as temporary:
        base = Path(temporary).resolve()
        environment = base / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=offline).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        install = [str(python), "-m", "pip", "install"]
        if offline:
            install.extend(("--no-index", "--no-deps"))
        _run(*install, str(wheel))
        origin = _run(str(python), "-c", "import image_downloader; print(image_downloader.__file__)")
        installed_path = Path(origin.strip())
        if not installed_path.is_relative_to(environment):
            raise RuntimeError(f"import did not use the wheel installation: {installed_path}")

        data_root = base / "data"
        plugin_root = base / "plugins"
        data_root.mkdir()
        plugin_root.mkdir()
        common = ("--data-root", str(data_root), "--plugin-root", str(plugin_root), "--json")
        doctor = json.loads(_run(str(python), "-m", "image_downloader", "doctor", *common))
        if doctor["application"]["version"] != version or not doctor["healthy"]:
            raise RuntimeError("installed package doctor did not pass")

        source = REPOSITORY / "plugin-sources" / "public-gallery"
        _run(
            str(python),
            "-m",
            "image_downloader",
            "plugin",
            "install",
            str(source),
            "--plugin-root",
            str(plugin_root),
            "--yes",
            "--json",
        )
        installed = json.loads(_run(str(python), "-m", "image_downloader", "doctor", *common))
        if not installed["healthy"] or not any(
            item["name"] == "local.image-downloader.public-gallery" and item["loaded"]
            for item in installed["plugins"]
        ):
            raise RuntimeError("installed signed plugin was not loaded in strict mode")
    print(f"release smoke passed: {version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="reuse system dependencies for local smoke tests")
    verify(offline=parser.parse_args().offline)
