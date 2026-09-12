"""Small authentication, personal-account, and SDK credential workflows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from clearinghouse.application.auth_ports import OAuthProviderClient
from clearinghouse.application.core_store import (
    AccessIdentity,
    CoreStore,
    CoreTransaction,
    StoredChallenge,
    StoredCredential,
    StoredOAuthFlow,
    StoredSession,
)
from clearinghouse.application.pagination import KeysetPage
from clearinghouse.domain.auth import AuthProvider, OAuthTransaction
from clearinghouse.domain.core import CredentialId
from clearinghouse.domain.email import normalize_email


class EmailSender(Protocol):
    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None: ...


class AccessDenied(ValueError):
    """Authentication or ownership proof was invalid."""


@dataclass(frozen=True, slots=True)
class IssuedBrowserSession:
    session: StoredSession
    token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    credential: StoredCredential
    token: str


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    state: str


class AccessService:
    """Issue opaque credentials while keeping only keyed digests in storage."""

    def __init__(
        self,
        store: CoreStore,
        sender: EmailSender,
        *,
        pepper: str,
        admin_email: str | None = None,
        otp_ttl_seconds: int = 600,
        otp_max_attempts: int = 5,
        session_ttl_seconds: int = 86_400,
        enabled_oauth: frozenset[str] = frozenset(),
        oauth_client: OAuthProviderClient | None = None,
        clock: Callable[[], datetime] | None = None,
        code_factory: Callable[[], str] | None = None,
        secret_factory: Callable[[], str] | None = None,
    ) -> None:
        if len(pepper) < 16:
            raise ValueError("authentication pepper must be at least 16 characters")
        self.store = store
        self.sender = sender
        self.pepper = pepper.encode()
        self.admin_email = normalize_email(admin_email.strip()) if admin_email else None
        self.otp_ttl_seconds = otp_ttl_seconds
        self.otp_max_attempts = otp_max_attempts
        self.session_ttl_seconds = session_ttl_seconds
        self.enabled_oauth = enabled_oauth
        self.oauth_client = oauth_client
        self.clock = clock or (lambda: datetime.now(UTC))
        self.code_factory = code_factory or (lambda: f"{secrets.randbelow(1_000_000):06d}")
        self.secret_factory = secret_factory or (lambda: secrets.token_urlsafe(36))

    @property
    def providers(self) -> tuple[str, ...]:
        return ("email", *(name for name in ("google", "github") if name in self.enabled_oauth))

    def digest(self, purpose: str, value: str) -> bytes:
        return hmac.new(self.pepper, f"{purpose}\0{value}".encode(), hashlib.sha256).digest()

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        value = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    @staticmethod
    def _stable_challenge_id(email: str) -> str:
        return f"otp_{hashlib.sha256(email.encode()).hexdigest()[:20]}"

    async def request_code(self, email: str) -> None:
        normalized = normalize_email(email.strip())
        code = self.code_factory()
        if len(code) != 6 or not code.isdecimal():
            raise RuntimeError("OTP factory must produce exactly six digits")
        now = self.clock()
        challenge = StoredChallenge(
            self._stable_challenge_id(normalized),
            normalized,
            self.digest("otp", code),
            now + timedelta(seconds=self.otp_ttl_seconds),
            0,
        )
        async with self.store.transaction() as transaction:
            await transaction.put_challenge(challenge)
        await self.sender.send_code(normalized, code, max(1, self.otp_ttl_seconds // 60))

    async def verify_code(self, email: str, code: str) -> IssuedBrowserSession:
        normalized = normalize_email(email.strip())
        challenge_id = self._stable_challenge_id(normalized)
        now = self.clock()
        async with self.store.transaction() as transaction:
            challenge = await transaction.get_challenge(challenge_id)
            valid = (
                challenge is not None
                and challenge.email == normalized
                and challenge.expires_at > now
                and challenge.attempts < self.otp_max_attempts
                and hmac.compare_digest(challenge.code_digest, self.digest("otp", code))
            )
            if not valid:
                if challenge is not None:
                    await transaction.increment_challenge_attempts(challenge_id)
                raise AccessDenied("invalid or expired email code")
            await transaction.delete_challenge(challenge_id)
            identity = await transaction.resolve_identity(normalized, admin_email=self.admin_email)
            issued = await self._put_session(transaction, identity, now)
        return issued

    async def _put_session(
        self, transaction: CoreTransaction, identity: AccessIdentity, now: datetime
    ) -> IssuedBrowserSession:
        token = f"och_web_{self.secret_factory()}"
        csrf = self.secret_factory()
        session = StoredSession(
            f"ses_{self.secret_factory()}",
            self.digest("session", token),
            self.digest("csrf", csrf),
            identity,
            now + timedelta(seconds=self.session_ttl_seconds),
        )
        await transaction.put_session(session)
        return IssuedBrowserSession(session, token, csrf)

    async def start_oauth(self, provider: str, redirect_uri: str) -> OAuthStart:
        if provider not in self.enabled_oauth or self.oauth_client is None:
            raise AccessDenied("OAuth provider is disabled")
        parsed = AuthProvider(provider)
        state = self.secret_factory()
        verifier = self.secret_factory() + self.secret_factory()
        nonce = self.secret_factory() if parsed is AuthProvider.GOOGLE else None
        now = self.clock()
        flow = StoredOAuthFlow(
            provider,
            self.digest("oauth-state", state),
            verifier,
            self.digest("oauth-nonce", nonce) if nonce else None,
            redirect_uri,
            now + timedelta(minutes=10),
        )
        async with self.store.transaction() as transaction:
            await transaction.put_oauth_flow(flow)
        return OAuthStart(
            self.oauth_client.authorization_url(
                parsed,
                state=state,
                code_challenge=self._pkce_challenge(verifier),
                nonce=nonce,
                redirect_uri=redirect_uri,
            ),
            state,
        )

    async def complete_oauth(
        self, provider: str, state: str, flow_cookie: str | None, code: str
    ) -> IssuedBrowserSession:
        if (
            provider not in self.enabled_oauth
            or self.oauth_client is None
            or flow_cookie is None
            or not hmac.compare_digest(state, flow_cookie)
        ):
            raise AccessDenied("invalid OAuth response")
        now = self.clock()
        async with self.store.transaction() as transaction:
            flow = await transaction.pop_oauth_flow(
                provider, self.digest("oauth-state", state), now=now
            )
        if flow is None:
            raise AccessDenied("invalid OAuth response")
        parsed = AuthProvider(provider)
        oauth_identity = await self.oauth_client.exchange(
            OAuthTransaction(
                f"oauth_{state[:12]}",
                parsed,
                self.digest("oauth-state", state).hex(),
                flow.verifier,
                flow.nonce_digest.hex() if flow.nonce_digest else None,
                flow.redirect_uri,
                flow.expires_at,
            ),
            code=code,
        )
        if flow.nonce_digest is not None and (
            oauth_identity.nonce is None
            or not hmac.compare_digest(
                flow.nonce_digest, self.digest("oauth-nonce", oauth_identity.nonce)
            )
        ):
            raise AccessDenied("invalid OAuth nonce")
        if oauth_identity.provider is not parsed or not oauth_identity.email:
            raise AccessDenied("OAuth provider did not return a verified email")
        async with self.store.transaction() as transaction:
            identity = await transaction.resolve_identity(
                normalize_email(oauth_identity.email), admin_email=self.admin_email
            )
            return await self._put_session(transaction, identity, now)

    async def authenticate_session(self, token: str | None) -> StoredSession:
        if token is None:
            raise AccessDenied("authentication required")
        async with self.store.transaction() as transaction:
            session = await transaction.get_session(self.digest("session", token))
        if session is None or session.expires_at <= self.clock():
            raise AccessDenied("authentication required")
        return session

    async def logout(self, token: str) -> None:
        async with self.store.transaction() as transaction:
            await transaction.delete_session(self.digest("session", token))

    async def create_credential(self, identity: AccessIdentity, name: str) -> IssuedCredential:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 80:
            raise ValueError("credential name must be 1 to 80 characters")
        token = f"och_live_{self.secret_factory()}"
        credential = StoredCredential(
            CredentialId(f"cred_{self.secret_factory()}"),
            identity.account_id,
            identity.user_id,
            normalized_name,
            self.digest("credential", token),
            self.clock(),
            None,
        )
        async with self.store.transaction() as transaction:
            await transaction.put_credential(credential)
        return IssuedCredential(credential, token)

    async def authenticate_credential(self, token: str) -> StoredCredential:
        async with self.store.transaction() as transaction:
            credential = await transaction.get_credential(self.digest("credential", token))
        if credential is None:
            raise AccessDenied("invalid SDK credential")
        return credential

    async def list_credentials(
        self, identity: AccessIdentity, *, limit: int = 50, after: tuple[str, ...] = ()
    ) -> KeysetPage[StoredCredential]:
        async with self.store.transaction() as transaction:
            return await transaction.list_credentials(identity.account_id, limit=limit, after=after)

    async def revoke_credential(
        self, identity: AccessIdentity, credential_id: CredentialId
    ) -> None:
        async with self.store.transaction() as transaction:
            revoked = await transaction.revoke_credential(
                identity.account_id, credential_id, self.clock()
            )
        if not revoked:
            raise AccessDenied("credential not found")
