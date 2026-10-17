"""Stable source keys shared by update state, workflow state and history."""

from hashlib import sha256


def source_key(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()
