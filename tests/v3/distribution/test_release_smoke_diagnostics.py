from __future__ import annotations

import subprocess
import sys

import pytest
from tools.release_smoke import _run


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
