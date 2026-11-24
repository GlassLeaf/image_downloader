"""No-overwrite publication keeps its destination and cleans up its own link."""

import os
import subprocess
import sys

import pytest

from image_downloader.exceptions import StorageSafetyError
from image_downloader.storage import FileSystem


@pytest.mark.parametrize("failure", ["once", "persistent", "interrupt"])
def test_published_file_cleanup_retries_without_deleting_destination(tmp_path, monkeypatch, failure):
    filesystem = FileSystem(tmp_path)
    real_unlink = os.unlink
    attempts = []

    def unlink(path, *args, **kwargs):
        attempts.append(path)
        if failure == "persistent" or len(attempts) == 1:
            raise KeyboardInterrupt() if failure == "interrupt" else PermissionError("artificial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    if failure == "once":
        assert filesystem.write_bytes_atomic_new("new.bin", b"test") == tmp_path / "new.bin"
    else:
        with pytest.raises(KeyboardInterrupt if failure == "interrupt" else StorageSafetyError):
            filesystem.write_bytes_atomic_new("new.bin", b"test")
    assert len(attempts) == 2
    assert (tmp_path / "new.bin").read_bytes() == b"test"
    assert (tmp_path / "new.bin").stat().st_nlink == (2 if failure == "persistent" else 1)


def test_publication_failure_preserves_primary_and_existing_destination(tmp_path, monkeypatch):
    filesystem = FileSystem(tmp_path)
    (tmp_path / "new.bin").write_bytes(b"existing")
    primary = KeyboardInterrupt("artificial primary")
    monkeypatch.setattr(os, "link", lambda *_: (_ for _ in ()).throw(primary))
    monkeypatch.setattr(os, "unlink", lambda *_: (_ for _ in ()).throw(PermissionError("cleanup")))
    with pytest.raises(KeyboardInterrupt) as caught:
        filesystem.write_bytes_atomic_new("new.bin", b"replacement")
    assert caught.value is primary
    assert (tmp_path / "new.bin").read_bytes() == b"existing"


def test_existing_destination_is_never_replaced(tmp_path):
    filesystem = FileSystem(tmp_path)
    (tmp_path / "new.bin").write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        filesystem.write_bytes_atomic_new("new.bin", b"replacement")
    assert (tmp_path / "new.bin").read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.bin"]


def test_child_exit_after_link_requires_explicit_recovery(tmp_path, repository_root):
    script = """
import os, sys
from pathlib import Path
from image_downloader.storage import FileSystem
real_link = os.link
def exit_after_link(source, destination):
    real_link(source, destination)
    os._exit(73)
os.link = exit_after_link
FileSystem(Path(sys.argv[1])).write_bytes_atomic_new('new.bin', b'test')
"""
    environment = dict(os.environ, PYTHONPATH=str(repository_root / "src"))
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 73, result.stderr
    filesystem = FileSystem(tmp_path)
    with pytest.raises(StorageSafetyError):
        filesystem.exists("new.bin")
    (temporary,) = tmp_path.glob(".id-*")
    assert temporary.samefile(tmp_path / "new.bin")
    temporary.unlink()
    assert filesystem.read_bytes_bounded("new.bin", 4) == b"test"
