"""Authentication domain values and failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AuthProvider(StrEnum):
    """Supported interactive identity providers."""

    EMAIL = "email"
    GOOGLE = "google"
    GITHUB = "github"


@dataclass(frozen=True, slots=True)
class OAuthTransaction:
    """Server-side OAuth correlation and PKCE material."""

    id: str
    provider: AuthProvider
    state_hash: str
    pkce_verifier: str
    nonce_hash: str | None
    redirect_uri: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthIdentity:
    """Verified stable identity returned by an OAuth provider."""

    provider: AuthProvider
    subject: str
    email: str | None
    nonce: str | None = None


class AuthenticationError(Exception):
    """Base class for expected authentication failures."""


class InvalidAuthentication(AuthenticationError):
    """Credentials or transient flow state were invalid."""


class AuthenticationUnavailable(AuthenticationError):
    """An authoritative authentication dependency was unavailable."""
