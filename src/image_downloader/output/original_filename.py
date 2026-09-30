"""Resolve the non-secret source filename used by output templates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import unquote, unquote_to_bytes, urlsplit

from ..models import ImageResource, RequestResponse


def resolve_original_filename(image: ImageResource, response: RequestResponse) -> str:
    """Return one safe-to-format source filename, falling back to the image index.

    The returned value is still passed through ``safe_component`` after template
    expansion.  This function only selects one filename candidate and strips
    path components so source URLs and response headers cannot introduce a
    directory component.
    """

    candidates = (
        _filename_leaf(image.original_filename),
        _content_disposition_filename(response.headers),
        _url_filename(response.url),
        _url_filename(image.url),
    )
    return next((candidate for candidate in candidates if candidate is not None), f"{image.index:04d}")


def original_filename_parts(filename: str | None, index: int) -> tuple[str, str, str]:
    """Return full name, stem, and last extension for output-token expansion."""

    name = _filename_leaf(filename) or f"{index:04d}"
    stem, separator, extension = name.rpartition(".")
    if not separator or not stem:
        return name, name, ""
    return name, stem, extension


def _content_disposition_filename(headers: Mapping[str, str]) -> str | None:
    value = next(
        (
            header_value
            for header_name, header_value in headers.items()
            if isinstance(header_name, str)
            and header_name.casefold() == "content-disposition"
            and isinstance(header_value, str)
        ),
        None,
    )
    if value is None:
        return None
    parameters = _content_disposition_parameters(value)
    if parameters is None:
        return None
    encoded = parameters.get("filename*")
    if encoded is not None:
        decoded = _decode_extended_filename(encoded)
        candidate = _filename_leaf(decoded)
        if candidate is not None:
            return candidate
    return _filename_leaf(parameters.get("filename"))


def _content_disposition_parameters(value: str) -> dict[str, str] | None:
    """Parse disposition parameters while preserving ``filename*`` spelling."""

    parameters: dict[str, str] = {}
    position = value.find(";")
    if position < 0:
        return parameters
    position += 1
    while position < len(value):
        position = _skip_parameter_separators(value, position)
        if position >= len(value):
            break
        parsed = _parse_content_disposition_parameter(value, position)
        if parsed is None:
            return None
        name, parameter_value, position = parsed
        if name in parameters:
            return None
        parameters[name] = parameter_value
    return parameters


def _skip_parameter_separators(value: str, position: int) -> int:
    while position < len(value) and value[position] in " \t;":
        position += 1
    return position


def _parse_content_disposition_parameter(value: str, position: int) -> tuple[str, str, int] | None:
    name_start = position
    while position < len(value) and value[position] not in "=;":
        position += 1
    name = value[name_start:position].strip().casefold()
    if not name or position >= len(value) or value[position] != "=":
        return None
    position = _skip_whitespace(value, position + 1)
    if position < len(value) and value[position] == '"':
        return _quoted_parameter(value, name, position + 1)
    value_start = position
    while position < len(value) and value[position] != ";":
        position += 1
    return name, value[value_start:position].strip(), position


def _quoted_parameter(value: str, name: str, position: int) -> tuple[str, str, int] | None:
    characters: list[str] = []
    while position < len(value):
        character = value[position]
        position += 1
        if character == "\\":
            if position >= len(value):
                return None
            characters.append(value[position])
            position += 1
        elif character == '"':
            position = _skip_whitespace(value, position)
            if position < len(value) and value[position] != ";":
                return None
            return name, "".join(characters), position
        else:
            characters.append(character)
    return None


def _skip_whitespace(value: str, position: int) -> int:
    while position < len(value) and value[position] in " \t":
        position += 1
    return position


def _decode_extended_filename(value: str) -> str | None:
    charset, separator, encoded = value.partition("'")
    if not separator:
        return None
    _language, separator, encoded = encoded.partition("'")
    if not separator or re.search(r"%(?![0-9A-Fa-f]{2})", encoded):
        return None
    try:
        return unquote_to_bytes(encoded).decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError):
        return None


def _url_filename(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return _filename_leaf(unquote(parsed.path, encoding="utf-8", errors="replace"))


def _filename_leaf(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    leaf = re.split(r"[\\/]", value)[-1].strip()
    if not leaf or leaf in {".", ".."}:
        return None
    return leaf
