"""Internal numeric checks shared by existing validation boundaries."""

from math import isfinite


def _is_finite(value: int | float) -> bool:
    """Treat integers beyond float conversion range as invalid finite inputs."""
    try:
        return isfinite(value)
    except OverflowError:
        return False


def numeric_value_error(value: object, *, positive: bool = False, integer: bool = False) -> str | None:
    """Describe an invalid numeric input at either the API or CLI boundary."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _is_finite(value):
        return "must be a finite number"
    if integer and type(value) is not int:
        return "must be an integer"
    if value < 0 or (positive and value == 0):
        return "must be positive" if positive else "must be non-negative"
    return None
