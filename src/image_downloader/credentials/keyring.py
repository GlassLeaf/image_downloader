"""Keyring access for runtime-owned secret resolution."""

from __future__ import annotations


def load_secret(service: str, username: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(service, username)
    except Exception:
        return None
