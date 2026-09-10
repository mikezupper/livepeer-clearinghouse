"""Provider-neutral authentication workflows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from clearinghouse.application.auth_ports import (
    AuthRepository,
    EmailCodeSender,
    OAuthProviderClient,
)
from clearinghouse.domain.auth import (
    AuthProvider,
    BrowserSession,
    CsrfRejected,
    EmailChallenge,
    InvalidAuthentication,
    IssuedSession,
    OAuthStart,
    OAuthTransaction,
    Principal,
    ProviderDisabled,
    RateLimited,
)


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    """Explicit authentication lifetime and abuse-control policy."""

    pepper: str
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    otp_send_limit: int = 5
    otp_send_window_seconds: int = 3600
    otp_verify_limit: int = 10
    otp_verify_window_seconds: int = 900
    oauth_ttl_seconds: int = 600
    oauth_attempt_limit: int = 20
    oauth_attempt_window_seconds: int = 900
    session_ttl_seconds: int = 86400
    session_absolute_ttl_seconds: int = 604800
    session_inactivity_seconds: int = 3600


class AuthService:
    """Authenticate identities and issue rotating opaque browser sessions."""

    def __init__(
        self,
        repository: AuthRepository,
        sender: EmailCodeSender,
        oauth: OAuthProviderClient,
        policy: AuthPolicy,
        *,
        enabled_providers: frozenset[AuthProvider] = frozenset({AuthProvider.EMAIL}),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._oauth = oauth
        self._policy = policy
        self._enabled = enabled_providers
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def enabled_providers(self) -> tuple[str, ...]:
        """Return stable provider names for capability discovery."""
        return tuple(provider.value for provider in AuthProvider if provider in self._enabled)

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(
            self._policy.pepper.encode(), f"{purpose}\0{value}".encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _opaque_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(18)}"

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    async def _rate_limit(
        self, scope: str, value: str, limit: int, window_seconds: int, now: datetime
    ) -> None:
        retry_after = await self._repository.consume_rate_limit(
            scope,
            self._digest(f"rate:{scope}", value),
            limit=limit,
            window_seconds=window_seconds,
            now=now,
        )
        if retry_after is not None:
            raise RateLimited(retry_after)

    async def request_email_code(self, normalized_email: str, client_ip: str) -> None:
        """Persist and deliver a code with address and network abuse limits."""
        now = self._clock()
        await self._rate_limit(
            "otp-send-email",
            normalized_email,
            self._policy.otp_send_limit,
            self._policy.otp_send_window_seconds,
            now,
        )
        await self._rate_limit(
            "otp-send-ip",
            client_ip,
            self._policy.otp_send_limit,
            self._policy.otp_send_window_seconds,
            now,
        )
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge = EmailChallenge(
            id=self._opaque_id("otp"),
            email_hash=self._digest("email", normalized_email),
            code_hash=self._digest("otp", code),
            request_ip_hash=self._digest("ip", client_ip),
            expires_at=now + timedelta(seconds=self._policy.otp_ttl_seconds),
            max_attempts=self._policy.otp_max_attempts,
        )
        await self._sender.send_code(
            normalized_email,
            code,
            max(1, self._policy.otp_ttl_seconds // 60),
        )
        await self._repository.replace_email_challenge(challenge)

    async def verify_email_code(
        self, normalized_email: str, code: str, client_ip: str, user_agent: str
    ) -> IssuedSession:
        """Consume a valid code exactly once and rotate into a browser session."""
        now = self._clock()
        await self._rate_limit(
            "otp-verify-email",
            normalized_email,
            self._policy.otp_verify_limit,
            self._policy.otp_verify_window_seconds,
            now,
        )
        await self._rate_limit(
            "otp-verify-ip",
            client_ip,
            self._policy.otp_verify_limit,
            self._policy.otp_verify_window_seconds,
            now,
        )
        valid = await self._repository.verify_email_challenge(
            self._digest("email", normalized_email), self._digest("otp", code), now
        )
        if not valid:
            raise InvalidAuthentication("invalid or expired email code")
        principal = await self._repository.resolve_identity(
            AuthProvider.EMAIL, normalized_email, normalized_email
        )
        return await self._issue_session(principal, now, client_ip, user_agent)

    async def start_oauth(
        self, provider: AuthProvider, redirect_uri: str, client_ip: str
    ) -> OAuthStart:
        """Create one-time state, PKCE, and OIDC nonce before redirecting."""
        if provider not in self._enabled or provider is AuthProvider.EMAIL:
            raise ProviderDisabled(provider.value)
        now = self._clock()
        await self._rate_limit(
            "oauth-start-ip",
            client_ip,
            self._policy.oauth_attempt_limit,
            self._policy.oauth_attempt_window_seconds,
            now,
        )
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(32) if provider is AuthProvider.GOOGLE else None
        transaction = OAuthTransaction(
            id=self._opaque_id("oauth"),
            provider=provider,
            state_hash=self._digest("oauth-state", state),
            pkce_verifier=verifier,
            nonce_hash=self._digest("oauth-nonce", nonce) if nonce else None,
            redirect_uri=redirect_uri,
            expires_at=now + timedelta(seconds=self._policy.oauth_ttl_seconds),
        )
        await self._repository.save_oauth_transaction(transaction)
        return OAuthStart(
            authorization_url=self._oauth.authorization_url(
                provider,
                state=state,
                code_challenge=self._pkce_challenge(verifier),
                nonce=nonce,
                redirect_uri=redirect_uri,
            ),
            flow_token=state,
        )

    async def complete_oauth(
        self,
        provider: AuthProvider,
        state: str,
        flow_token: str | None,
        code: str,
        client_ip: str,
        user_agent: str,
    ) -> IssuedSession:
        """Consume correlated flow state and authenticate the stable provider subject."""
        if provider not in self._enabled or provider is AuthProvider.EMAIL:
            raise ProviderDisabled(provider.value)
        now = self._clock()
        await self._rate_limit(
            "oauth-callback-ip",
            client_ip,
            self._policy.oauth_attempt_limit,
            self._policy.oauth_attempt_window_seconds,
            now,
        )
        if flow_token is None or not hmac.compare_digest(flow_token, state):
            raise InvalidAuthentication("oauth flow is not bound to this browser")
        transaction = await self._repository.consume_oauth_transaction(
            provider, self._digest("oauth-state", state), now
        )
        if transaction is None:
            raise InvalidAuthentication("invalid or expired oauth state")
        identity = await self._oauth.exchange(transaction, code=code)
        if identity.provider is not provider or not identity.subject:
            raise InvalidAuthentication("oauth provider identity mismatch")
        if transaction.nonce_hash is not None and (
            identity.nonce is None
            or not hmac.compare_digest(
                self._digest("oauth-nonce", identity.nonce), transaction.nonce_hash
            )
        ):
            raise InvalidAuthentication("oauth nonce mismatch")
        principal = await self._repository.resolve_identity(
            provider, identity.subject, identity.email
        )
        return await self._issue_session(principal, now, client_ip, user_agent)

    async def _issue_session(
        self, principal: Principal, now: datetime, client_ip: str, user_agent: str
    ) -> IssuedSession:
        if not principal.active:
            raise InvalidAuthentication("principal is inactive")
        issued = self._build_session(
            principal,
            now,
            self._digest("ip", client_ip),
            self._digest("user-agent", user_agent),
        )
        await self._repository.create_session(issued.session)
        return issued

    def _build_session(
        self,
        principal: Principal,
        now: datetime,
        client_ip_hash: str,
        user_agent_hash: str,
    ) -> IssuedSession:
        token = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(32)
        session = BrowserSession(
            id=self._opaque_id("bws"),
            principal=principal,
            token_hash=self._digest("session", token),
            csrf_hash=self._digest("csrf", csrf),
            expires_at=now + timedelta(seconds=self._policy.session_ttl_seconds),
            absolute_expires_at=now + timedelta(seconds=self._policy.session_absolute_ttl_seconds),
            last_seen_at=now,
            client_ip_hash=client_ip_hash,
            user_agent_hash=user_agent_hash,
        )
        return IssuedSession(session=session, token=token, csrf_token=csrf)

    async def authenticate(self, token: str | None) -> BrowserSession:
        """Resolve and refresh a usable session without exposing its stored hash."""
        if not token:
            raise InvalidAuthentication("missing browser session")
        now = self._clock()
        session = await self._repository.use_session(
            self._digest("session", token), now, self._policy.session_inactivity_seconds
        )
        if session is None:
            raise InvalidAuthentication("invalid browser session")
        return session

    async def renew_session(self, token: str | None) -> IssuedSession:
        """Atomically rotate a valid session at the controlled renewal endpoint."""
        if not token:
            raise InvalidAuthentication("missing browser session")
        now = self._clock()
        replacement_token = secrets.token_urlsafe(48)
        replacement_csrf = secrets.token_urlsafe(32)
        session = await self._repository.renew_session(
            self._digest("session", token),
            now=now,
            inactivity_seconds=self._policy.session_inactivity_seconds,
            replacement_id=self._opaque_id("bws"),
            replacement_token_hash=self._digest("session", replacement_token),
            replacement_csrf_hash=self._digest("csrf", replacement_csrf),
            ttl_seconds=self._policy.session_ttl_seconds,
        )
        if session is None:
            raise InvalidAuthentication("invalid browser session")
        return IssuedSession(session, replacement_token, replacement_csrf)

    async def validate_csrf(
        self,
        session: BrowserSession,
        csrf_cookie: str | None,
        csrf_header: str | None,
        origin: str | None,
        allowed_origins: frozenset[str],
    ) -> None:
        """Require an allowed origin and session-bound double-submit token."""
        if origin not in allowed_origins or not csrf_cookie or not csrf_header:
            raise CsrfRejected("csrf validation failed")
        if not hmac.compare_digest(csrf_cookie, csrf_header):
            raise CsrfRejected("csrf validation failed")
        if not hmac.compare_digest(self._digest("csrf", csrf_cookie), session.csrf_hash):
            raise CsrfRejected("csrf validation failed")

    async def logout(self, session: BrowserSession) -> None:
        """Revoke a server-side session immediately."""
        await self._repository.revoke_session(session.id)
