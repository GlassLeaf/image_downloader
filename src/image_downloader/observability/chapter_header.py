"""Shared keys and ordering for chapter log headers and their offline verifier."""

from __future__ import annotations

import re

EXTRA_HEADER_KEYS = (
    "content_id",
    "chapter_number",
    "chapter_id",
    "content_published_at",
    "content_publication_date",
    "chapter_published_at",
    "chapter_publication_date",
)
HEADER_KEYS = ("url", "content_title", "author", "title", "subtitle", *EXTRA_HEADER_KEYS)
_CUSTOM_KEY = re.compile(r"(content|chapter)\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")


def is_extra_header_key(key: str) -> bool:
    return key in EXTRA_HEADER_KEYS or _CUSTOM_KEY.fullmatch(key) is not None


def header_key_order(key: str) -> tuple[int, str]:
    if key in HEADER_KEYS:
        return HEADER_KEYS.index(key), ""
    return len(HEADER_KEYS) + (not key.startswith("content.")), key


def valid_header_keys(keys: list[str]) -> bool:
    if keys in (["url", "title"], ["url", "title", "subtitle"]):
        return True
    return (
        {"url", "content_title", "title", "chapter_number"}.issubset(keys)
        and len(keys) == len(set(keys))
        and all(key in HEADER_KEYS or is_extra_header_key(key) for key in keys)
        and keys == sorted(keys, key=header_key_order)
    )
