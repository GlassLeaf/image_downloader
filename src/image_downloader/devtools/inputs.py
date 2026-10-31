"""Explicit DTO and result references for call/scenario inputs; no eval."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .. import models
from ..ports import PluginExecutionContext, PluginFormatContext, TransformContext

TYPES: dict[str, type[Any]] = {
    name: value
    for name, value in vars(models).items()
    if isinstance(value, type) and (is_dataclass(value) or issubclass(value, Enum))
}
TYPES.update(
    PluginExecutionContext=PluginExecutionContext,
    TransformContext=TransformContext,
    PluginFormatContext=PluginFormatContext,
    datetime=datetime,
)


class DevelopmentInputError(ValueError):
    """Input construction/binding failed before invoking plugin code."""


def decode_input(value: Any, *, root: Path, refs: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [decode_input(item, root=root, refs=refs) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        parts = str(value["$ref"]).split(".")
        result = refs[parts.pop(0)]
        for part in parts:
            result = (
                result[int(part)]
                if isinstance(result, (list, tuple))
                else (result[part] if isinstance(result, Mapping) else getattr(result, part))
            )
        return result
    if "$file" in value:
        return (root / value["$file"]).read_bytes()
    if "$base64" in value:
        return base64.b64decode(value["$base64"], validate=True)
    if "$type" not in value:
        return {key: decode_input(item, root=root, refs=refs) for key, item in value.items()}
    name = value["$type"]
    if name not in TYPES:
        raise DevelopmentInputError(f"unknown public input type: {name}")
    cls = TYPES[name]
    if cls is datetime:
        return datetime.fromisoformat(value["value"])
    if issubclass(cls, Enum):
        return cls(value["value"])
    if "fields" in value:
        data = decode_input(value["fields"], root=root, refs=refs)
    else:
        data = {key: decode_input(item, root=root, refs=refs) for key, item in value.items() if key != "$type"}
    return cls(**data)
