"""Core-owned CLI diagnostics, independent from shared log/notification errors.

Only explicit core messages and structural metadata enter this channel. Never
attach an exception string, an input value, or an absolute filesystem path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel

from .exceptions import ArgumentError, ConfigurationError


@dataclass(frozen=True)
class Diagnostic:
    message: str
    details: Mapping[str, object]


def diagnostic_for(error: BaseException) -> Diagnostic | None:
    value = getattr(error, "_core_diagnostic", None)
    return value if isinstance(value, Diagnostic) else None


def argument_error(message: str, kind: str, **details: object) -> ArgumentError:
    error = ArgumentError(message)
    error._core_diagnostic = Diagnostic(message, MappingProxyType({"kind": kind, **details}))
    return error


def configuration_error(message: str, kind: str, **details: object) -> ConfigurationError:
    error = ConfigurationError(message)
    error._core_diagnostic = Diagnostic(message, MappingProxyType({"kind": kind, **details}))
    return error


def safe_option(token: str) -> str:
    """Identify an unknown option without publishing its value or control text."""
    name = token.split("=", 1)[0]
    return name if re.fullmatch(r"--?[A-Za-z][A-Za-z0-9-]{0,63}", name) else "(unknown option)"


def configuration_source(path: Path | None, role: str = "configuration") -> str:
    # A filename alone can itself contain secrets. Only conventional YAML names
    # are useful identifiers; arbitrary user filenames are described by role.
    if path is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,64}\.ya?ml", path.name):
        return f"{role}:{path.name}"
    return role


def _unwrapped_annotation(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _unwrapped_annotation(get_args(annotation)[0])
    if origin in {Union, UnionType}:
        alternatives = tuple(value for value in get_args(annotation) if value is not type(None))
        if len(alternatives) == 1:
            return _unwrapped_annotation(alternatives[0])
    return annotation


def _model_owned_field(model: type[BaseModel], location: object) -> str:
    """Follow schema fields, stopping before any user-owned mapping key."""
    names: list[str] = []
    annotation: object = model
    if isinstance(location, (tuple, list)):
        for part in location:
            annotation = _unwrapped_annotation(annotation)
            if isinstance(part, int) and get_origin(annotation) in {list, tuple}:
                annotation = get_args(annotation)[0]
                continue
            if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
                break
            field = next(
                (field for name, field in annotation.model_fields.items() if part == (field.alias or name)), None
            )
            if field is None:
                break
            names.append(str(part))
            annotation = field.annotation
    return ".".join(names) or "configuration"


def validation_diagnostic(
    issue: Mapping[str, object], *, source: str | None = None, model: type[BaseModel] | None = None
) -> ConfigurationError:
    if model is None:
        from .configuration.models import AppConfig

        model = AppConfig
    field = _model_owned_field(model, issue.get("loc", ()))
    kind = str(issue.get("type", ""))
    ctx = issue.get("ctx")
    constraint = ctx if isinstance(ctx, Mapping) else {}
    conditions = {
        "greater_than_equal": ("ge", "must be greater than or equal to"),
        "greater_than": ("gt", "must be greater than"),
        "less_than_equal": ("le", "must be less than or equal to"),
        "less_than": ("lt", "must be less than"),
    }
    if kind in conditions:
        key, text = conditions[kind]
        bound = constraint.get(key)
        expected = f"{text} {bound}" if isinstance(bound, (int, float)) else text
    elif kind == "value_error":
        safe_conditions = {
            "must be a finite number",
            "must contain only letters, numbers, '_' or '-'",
            "must be null or an absolute path",
            "safe parameter names are invalid",
            "pool_max_idle_connections must not exceed pool_max_connections",
            "origin_request_concurrency must not exceed request_concurrency",
            "registrable_domain_request_concurrency must not exceed request_concurrency",
            "output.directory_format cannot contain image-only tokens",
            "output format contains an invalid %PLUGIN[...]% token",
            "secret names must be lower_snake_case",
            "secret references must be uppercase environment references",
            "image processor IDs are invalid or duplicated",
            "transport metadata access IDs are invalid",
            "transport metadata access site IDs are invalid or duplicated",
            "plugin setting IDs must be reverse-DNS identifiers",
        }
        safe_conditions.update(
            f"output format cannot contain {token}; use explicit content, chapter, or image tokens instead"
            for token in ("%NUM%", "%TITLE%", "%SUBTITLE%")
        )
        condition = str(constraint.get("error", ""))
        expected = condition if condition in safe_conditions else "does not satisfy the configuration constraints"
    else:
        expected = {
            "int_type": "must be an integer",
            "bool_type": "must be a boolean",
            "string_type": "must be a string",
            "literal_error": "must use a supported choice",
            "dict_type": "must be a mapping",
            "missing": "is required",
            "extra_forbidden": "contains an unsupported field",
        }.get(kind, "does not satisfy the configuration constraints")
    details: dict[str, object] = {"field": field}
    if source is not None:
        details["source"] = source
    return configuration_error(f"{field} {expected}", "configuration_value", **details)
