"""Internal numeric checks shared by existing validation boundaries."""

from math import isfinite


def _is_finite(value: int | float) -> bool:
    """Treat integers beyond float conversion range as invalid finite inputs."""
    try:
        return isfinite(value)
    except OverflowError:
        return False
