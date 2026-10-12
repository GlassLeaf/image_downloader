"""Copy a source tree and remove only acquisition's Windows sharing retry.

The product source is never edited. Keep the CRT classification and verified
path checks unchanged so the control differs only in the two retry helpers.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.is_relative_to(source):
        parser.error("output must be outside the source tree")
    relative = Path("image_downloader/storage/interprocess_lock.py")
    original = (source / relative).read_bytes()
    text = original.decode("utf-8")
    tree = ast.parse(text)
    lock = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "InterProcessFileLock")
    helpers = [
        node
        for node in lock.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_open_for_acquisition", "_open_for_acquisition_async"}
    ]
    if len(helpers) != 2 or not all("retry" in ast.get_source_segment(text, node) for node in helpers):
        parser.error("expected both final sharing retry helpers")
    newline = "\r\n" if b"\r\n" in original else "\n"
    lines = text.splitlines(keepends=True)
    for node in sorted(helpers, key=lambda value: value.lineno, reverse=True):
        keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        replacement = (
            f"    {keyword} {node.name}(self) -> BinaryIO:{newline}"
            f"        return self._open_stream(){newline}"
        )
        lines[node.lineno - 1 : node.end_lineno] = [replacement]
    modified = "".join(lines).encode("utf-8")
    ast.parse(modified)
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    (output / relative).write_bytes(modified)
    metadata = {
        "source": str(source),
        "output": str(output),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "control_sha256": hashlib.sha256(modified).hexdigest(),
        "changed_helpers": [node.name for node in helpers],
        "unchanged": "CRT detection, publication, O_CREAT removal, identity checks, initialization and cleanup",
        "diff": "".join(
            difflib.unified_diff(text.splitlines(keepends=True), modified.decode().splitlines(keepends=True),
                                 fromfile="final/interprocess_lock.py", tofile="no-retry/interprocess_lock.py")
        ),
    }
    (output.parent / (output.name + "-variant.json")).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in metadata.items() if key != "diff"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
