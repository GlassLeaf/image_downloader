"""Opt-in Windows long-path I/O probe; all created paths are confined and removed."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from image_downloader.exceptions import StorageError, error_info_for
from image_downloader.storage import FileSystem, atomic_write


def probe(parent_length: int, *, legacy_temp: bool) -> dict[str, object]:  # noqa: C901 -- opt-in diagnostic matrix
    workspace = Path(__file__).resolve().parents[3]
    base = workspace / ".runtime"
    base.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="r15-boundary-", dir=base))
    assert root.is_relative_to(base)
    directories = [root]
    parent = root
    payload = b"r15-near-maximum\x00\xff" * 64
    real_mkstemp = tempfile.mkstemp

    def confined(path: Path) -> None:
        assert path == root or path.is_relative_to(root)

    def attempt(length: int, mode: str, writer: str) -> dict[str, object]:
        name = "f" * (length - len(str(parent)) - 1)
        assert 1 <= len(name) <= 255
        plain = parent / name
        confined(plain)
        target = plain if mode == "plain" else Path("\\\\?\\" + str(plain))
        selected_root = root if mode == "plain" else Path("\\\\?\\" + str(root))
        relative = plain.relative_to(root)
        fs = FileSystem(selected_root)
        row: dict[str, object] = {"units": length, "argument_units": len(str(target)), "leaf_units": len(name)}

        def legacy_mkstemp(*, prefix: str, dir: Path) -> tuple[int, str]:
            return real_mkstemp(prefix=f".{name}.", dir=dir)

        context = patch("image_downloader.storage.filesystem.tempfile.mkstemp", legacy_mkstemp)
        try:
            with context if legacy_temp and writer != "direct" else nullcontext():
                for data in (payload, payload + b"overwrite"):
                    if writer == "direct":
                        target.write_bytes(data)
                    elif writer == "core":
                        fs.write_bytes_atomic(relative, data)
                    else:
                        atomic_write(target, data)
                    assert target.read_bytes() == data
                    assert fs.read_bytes_bounded(relative, len(data)) == data
            row.update(ok=True, read_write_overwrite=True)
        except (OSError, ValueError, StorageError) as error:
            if isinstance(error, ValueError) and "too long for Windows" not in str(error):
                raise
            cause = error.__cause__ if isinstance(error, StorageError) else error
            row.update(
                ok=False,
                exception=type(error).__name__,
                errno=getattr(cause, "errno", None),
                winerror=getattr(cause, "winerror", None),
            )
            if isinstance(error, StorageError):
                row["diagnostic"] = error_info_for(error).message
        finally:
            # Clean using the ordinary path: adding \\?\ can itself exceed a
            # Python argument-length guard. Failed oversized names never exist.
            try:
                plain.unlink()
            except FileNotFoundError:
                pass
            except ValueError as error:
                if "path too long for Windows" not in str(error):
                    raise
        return row

    def boundary(mode: str, writer: str) -> dict[str, object]:
        low, high = len(str(parent)) + 2, len(str(parent)) + 256
        assert attempt(low, mode, writer)["ok"]
        assert not attempt(high, mode, writer)["ok"]
        while high - low > 1:
            middle = (low + high) // 2
            if attempt(middle, mode, writer)["ok"]:
                low = middle
            else:
                high = middle
        return {
            "maximum": attempt(low, mode, writer),
            "one_over": attempt(high, mode, writer),
            "near_maximum": [attempt(low - delta, mode, writer) for delta in (1, 8, 16)],
        }

    report: dict[str, object] = {"parent_units": parent_length}
    try:
        while len(str(parent)) < parent_length:
            gap = parent_length - len(str(parent))
            count = min(200, gap - 1)
            if gap - count - 1 == 1:
                count -= 1
            assert count > 0
            child = parent / ("d" * count)
            confined(child)
            child.mkdir()
            directories.append(child)
            parent = child
        for mode in ("plain", "extended"):
            direct = boundary(mode, "direct")
            report[mode + "_direct"] = direct
            maximum = int(direct["maximum"]["units"])
            for writer in ("core", "atomic_write"):
                result = boundary(mode, writer)
                expected = maximum - 10 if legacy_temp else maximum
                assert result["maximum"]["units"] == expected, result
                checks = [
                    attempt(length, mode, writer)
                    for length in sorted({maximum - 10, maximum - 9, maximum - 1, maximum, maximum + 1})
                ]
                for check in checks:
                    assert check["ok"] == (int(check["units"]) <= expected), check
                result["acceptance_checks"] = checks
                report[mode + "_" + writer] = result
    finally:
        for entry in parent.iterdir():
            confined(entry)
            assert not entry.is_dir()
            entry.unlink()
        for directory in reversed(directories):
            confined(directory)
            directory.rmdir()
        assert not root.exists()
    report["cleanup_complete"] = True
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-temp", action="store_true", help="Reproduce the pre-fix temporary-name overhead")
    parser.add_argument("--output", type=Path, help="Write JSON results to this file")
    options = parser.parse_args()
    if os.name != "nt":
        parser.error("This opt-in full-path boundary probe requires Windows")
    report = {
        "python": sys.version,
        "legacy_temp": options.legacy_temp,
        "length_convention": "absolute UTF-16 units, excluding NUL and the extended-path prefix",
        "results": [probe(length, legacy_temp=options.legacy_temp) for length in (32520, 32600, 32680)],
    }
    document = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if options.output:
        options.output.write_text(document, encoding="utf-8")
    print(document)


if __name__ == "__main__":
    main()
