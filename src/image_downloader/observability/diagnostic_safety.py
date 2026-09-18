"""Keep diagnostic failures separate from download and persistence outcomes."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable


def warn_diagnostic_failure() -> None:
    """Use only a fixed message so even an exception containing secrets cannot leak."""
    try:
        sys.stderr.write("warning: diagnostic logging failed; saved files are unchanged\n")
    except Exception:
        pass


async def best_effort_diagnostic(
    writer: Callable[..., Awaitable[object]], *args: object, **kwargs: object
) -> None:
    try:
        await writer(*args, **kwargs)
    except Exception:
        warn_diagnostic_failure()


def best_effort_diagnostic_sync(writer: Callable[..., object], *args: object, **kwargs: object) -> None:
    try:
        writer(*args, **kwargs)
    except Exception:
        warn_diagnostic_failure()
