"""Minimal, redacted evidence for offline workflow file-presence checks."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from ..exceptions import StorageSafetyError
from ..models import WorkflowResult
from ..privacy.log_safety import safe_relative_path, safe_url
from ..storage.filesystem import FileSystem


def url_key(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def verification_evidence(result: WorkflowResult, root: Path) -> dict[str, Any]:
    items = []
    filesystem = FileSystem(root)
    for item in result.items:
        attempt = item.attempts[-1] if item.attempts else None
        download = attempt.download if attempt else None
        files = []
        available = download is not None
        if download is not None:
            for chapter in download.chapters:
                for image in chapter.outcomes:
                    if image.kind.value not in {"saved", "skipped"}:
                        continue
                    try:
                        if image.path is None:
                            raise ValueError
                        # Keep the original spelling; never follow a link while recording evidence.
                        relative = Path(image.path).relative_to(root)
                        filesystem._parts(relative)
                        value = relative.as_posix()
                        if safe_relative_path(image.path, root) != value:
                            raise ValueError
                    except (ValueError, OSError, StorageSafetyError):
                        available = False
                        continue
                    files.append({"path": value, "kind": image.kind.value})
        items.append(
            {
                "url_key": url_key(item.url),
                "url": safe_url(item.url),
                "round_number": attempt.round_number if attempt else None,
                "status": attempt.status if attempt else "unprocessed",
                "expected_images": sum(len(c.images) for c in download.manifest.chapters) if download else None,
                "available": available,
                "files": files,
            }
        )
    return {
        "version": 1,
        "rounds": [
            {
                "round_number": r.round_number,
                "selection_established": r.snapshot is not None,
                "selected_url_keys": [url_key(url) for url in r.selected_urls],
            }
            for r in result.rounds
        ],
        "items": items,
    }
