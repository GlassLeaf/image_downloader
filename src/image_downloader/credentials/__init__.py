"""Core credential management. Plugins retain the read-only SecretProvider API."""

from .profile_store import ProfileCredentialStore, SmtpCredential
from .vault import CredentialVault

__all__ = ["CredentialVault", "ProfileCredentialStore", "SmtpCredential"]
