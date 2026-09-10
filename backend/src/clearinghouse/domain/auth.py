"""Authentication domain values and failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class AuthProvider(StrEnum):
    """Supported interactive identity providers."""

    EMAIL = "email"
    GOOGLE = "google"
    GITHUB = "github"


class Role(StrEnum):
    """Capabilities attached to an authenticated principal."""

    OPERATOR = "operator"
    TENANT_ADMIN = "tenant_admin"
    CREDENTIAL_HOLDER = "credential_holder"


@dataclass(frozen=True, slots=True)
class Principal:
    """Tenant-scoped identity returned by the authentication boundary."""

    id: str
    tenant_id: str | None
    account_id: str | None
    roles: frozenset[Role]
    active: bool = True


@dataclass(frozen=True, slots=True)
class EmailChallenge:
    """A stored, short-lived email-code challenge."""

    id: str
    email_hash: str
    code_hash: str
    request_ip_hash: str
    expires_at: datetime
    max_attempts: int


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


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """A database-backed authenticated browser session."""

    id: str
    principal: Principal
    token_hash: str
    csrf_hash: str
    expires_at: datetime
    absolute_expires_at: datetime
    last_seen_at: datetime
    client_ip_hash: str
    user_agent_hash: str
    revoked: bool = False

    def is_active(self, now: datetime, inactivity_seconds: int) -> bool:
        """Return whether the session remains usable at ``now``."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        inactive_for = (now.astimezone(UTC) - self.last_seen_at.astimezone(UTC)).total_seconds()
        return (
            self.principal.active
            and not self.revoked
            and now < self.expires_at
            and now < self.absolute_expires_at
            and inactive_for <= inactivity_seconds
        )


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A session plus the raw values that may be returned once to a browser."""

    session: BrowserSession
    token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class OAuthStart:
    """Provider redirect plus browser-bound flow secret."""

    authorization_url: str
    flow_token: str


class AuthenticationError(Exception):
    """Base class for expected authentication failures."""


class InvalidAuthentication(AuthenticationError):
    """Credentials or transient flow state were invalid."""


class ProviderDisabled(AuthenticationError):
    """An unavailable optional provider was requested."""


class CsrfRejected(AuthenticationError):
    """A state-changing browser request failed origin or token validation."""


class AuthenticationUnavailable(AuthenticationError):
    """An authoritative authentication dependency was unavailable."""


class RateLimited(AuthenticationError):
    """A request exceeded an abuse-control window."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("authentication request rate limited")
        self.retry_after_seconds = max(1, retry_after_seconds)
