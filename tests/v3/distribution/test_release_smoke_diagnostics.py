from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tools.release_smoke import ROOT_MODULES, _check_archive_names, _run


@pytest.mark.parametrize("source_archive", [False, True])
def test_archive_accepts_current_modules_and_rejects_obsolete_roots(source_archive: bool) -> None:
    repository = Path(__file__).resolve().parents[3]
    package = repository / "src" / "image_downloader"
    names = [
        path.relative_to(repository / "src").as_posix()
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    prefix = "image_downloader-0.0.0.1b0/src/" if source_archive else ""
    archive_names = [prefix + name for name in names]
    _check_archive_names(archive_names, source_archive=source_archive)
    assert ROOT_MODULES == {path.name for path in package.glob("*.py")}
    with pytest.raises(RuntimeError, match="obsolete root module"):
        _check_archive_names([*archive_names, prefix + "image_downloader/legacy.py"], source_archive=source_archive)


def test_failed_command_preserves_output_and_exit_status(capsys: pytest.CaptureFixture[str]) -> None:
    script = "import sys; print('smoke output'); print('smoke error', file=sys.stderr); sys.exit(4)"
    with pytest.raises(subprocess.CalledProcessError) as caught:
        _run(sys.executable, "-c", script)
    assert caught.value.returncode == 4
    captured = capsys.readouterr()
    assert "smoke output" in captured.err
    assert "smoke error" in captured.err
    assert captured.out == ""


def test_successful_command_returns_output_without_printing(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(sys.executable, "-c", "print('smoke passed')").strip() == "smoke passed"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
