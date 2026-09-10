"""Secure identity onboarding values and failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clearinghouse.domain.auth import AuthProvider


@dataclass(frozen=True, slots=True)
class IdentityInvitation:
    """Stored metadata for a one-time principal-link invitation."""

    id: str
    source_principal_id: str
    principal_id: str
    tenant_id: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedIdentityInvitation:
    """Invitation metadata plus the secret returned exactly once."""

    invitation: IdentityInvitation
    secret: str


@dataclass(frozen=True, slots=True)
class IdentityLink:
    """Public, PII-free result of an explicit identity link."""

    id: str
    identity_id: str
    provider: AuthProvider
    principal_id: str
    tenant_id: str
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class OperatorBootstrap:
    """Result of the one-shot configured operator bootstrap."""

    principal_id: str
    created: bool


class OnboardingError(Exception):
    """Base class for expected onboarding failures."""


class OnboardingForbidden(OnboardingError):
    """The actor cannot administer the requested tenant principal."""


class OnboardingNotFound(OnboardingError):
    """The target is missing or hidden by tenant isolation."""


class OnboardingConflict(OnboardingError):
    """Existing state makes the requested link or bootstrap unsafe."""


class InvalidInvitation(OnboardingError):
    """An invitation is missing, expired, consumed, or does not match."""
