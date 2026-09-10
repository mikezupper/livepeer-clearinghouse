"""Authentication workflow and HTTP security tests."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clearinghouse.application.authentication import AuthPolicy, AuthService
from clearinghouse.domain.auth import (
    AuthenticationUnavailable,
    AuthProvider,
    BrowserSession,
    CsrfRejected,
    EmailChallenge,
    InvalidAuthentication,
    OAuthIdentity,
    OAuthTransaction,
    Principal,
    ProviderDisabled,
    RateLimited,
    Role,
)
from clearinghouse.infrastructure.config import Settings
from clearinghouse.main import create_app

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
TEST_OAUTH_SECRET = SecretStr("unit-test-oauth-value")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class MemoryAuthRepository:
    def __init__(self) -> None:
        self.rate_counts: defaultdict[tuple[str, str, int], int] = defaultdict(int)
        self.challenge: EmailChallenge | None = None
        self.attempts = 0
        self.consumed = False
        self.transactions: dict[tuple[AuthProvider, str], OAuthTransaction] = {}
        self.sessions: dict[str, BrowserSession] = {}
        self.identities: dict[tuple[AuthProvider, str], Principal] = {}
        self.touches: list[str] = []

    async def consume_rate_limit(
        self,
        scope: str,
        key_hash: str,
        *,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> int | None:
        epoch = int(now.timestamp()) // window_seconds * window_seconds
        key = (scope, key_hash, epoch)
        if self.rate_counts[key] >= limit:
            return epoch + window_seconds - int(now.timestamp())
        self.rate_counts[key] += 1
        return None

    async def replace_email_challenge(self, challenge: EmailChallenge) -> None:
        self.challenge = challenge
        self.attempts = 0
        self.consumed = False

    async def verify_email_challenge(self, email_hash: str, code_hash: str, now: datetime) -> bool:
        challenge = self.challenge
        if (
            challenge is None
            or self.consumed
            or challenge.email_hash != email_hash
            or challenge.expires_at <= now
            or self.attempts >= challenge.max_attempts
        ):
            return False
        self.attempts += 1
        valid = challenge.code_hash == code_hash
        self.consumed = valid
        return valid

    async def resolve_identity(
        self, provider: AuthProvider, provider_subject: str, email: str | None
    ) -> Principal:
        del email
        key = (provider, provider_subject)
        if key not in self.identities:
            self.identities[key] = Principal(
                "prn_abcdefgh", None, None, frozenset({Role.CREDENTIAL_HOLDER})
            )
        return self.identities[key]

    async def save_oauth_transaction(self, transaction: OAuthTransaction) -> None:
        self.transactions[(transaction.provider, transaction.state_hash)] = transaction

    async def consume_oauth_transaction(
        self, provider: AuthProvider, state_hash: str, now: datetime
    ) -> OAuthTransaction | None:
        transaction = self.transactions.pop((provider, state_hash), None)
        if transaction is None or transaction.expires_at <= now:
            return None
        return transaction

    async def create_session(self, session: BrowserSession) -> None:
        self.sessions[session.token_hash] = session

    async def use_session(
        self, token_hash: str, now: datetime, inactivity_seconds: int
    ) -> BrowserSession | None:
        session = self.sessions.get(token_hash)
        if session is None or not session.is_active(now, inactivity_seconds):
            return None
        self.touches.append(session.id)
        self.sessions[token_hash] = replace(session, last_seen_at=now)
        return self.sessions[token_hash]

    async def renew_session(
        self,
        token_hash: str,
        *,
        now: datetime,
        inactivity_seconds: int,
        replacement_id: str,
        replacement_token_hash: str,
        replacement_csrf_hash: str,
        ttl_seconds: int,
    ) -> BrowserSession | None:
        current = await self.use_session(token_hash, now, inactivity_seconds)
        if current is None:
            return None
        replacement = BrowserSession(
            id=replacement_id,
            principal=current.principal,
            token_hash=replacement_token_hash,
            csrf_hash=replacement_csrf_hash,
            expires_at=min(now + timedelta(seconds=ttl_seconds), current.absolute_expires_at),
            absolute_expires_at=current.absolute_expires_at,
            last_seen_at=now,
            client_ip_hash=current.client_ip_hash,
            user_agent_hash=current.user_agent_hash,
        )
        self.sessions[token_hash] = replace(current, revoked=True)
        self.sessions[replacement.token_hash] = replacement
        return replacement

    async def revoke_session(self, session_id: str) -> None:
        for key, session in self.sessions.items():
            if session.id == session_id:
                self.sessions[key] = replace(session, revoked=True)


class CapturingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, int]] = []
        self.unavailable = False

    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
        if self.unavailable:
            raise AuthenticationUnavailable("mail unavailable")
        self.messages.append((email, code, expires_in_minutes))


class FakeOAuth:
    def __init__(self) -> None:
        self.transactions: list[OAuthTransaction] = []
        self.identity = OAuthIdentity(AuthProvider.GOOGLE, "google-subject", "user@example.com")

    def authorization_url(
        self,
        provider: AuthProvider,
        *,
        state: str,
        code_challenge: str,
        nonce: str | None,
        redirect_uri: str,
    ) -> str:
        nonce_value = nonce or ""
        return (
            f"https://provider.example/authorize?provider={provider.value}&state={state}"
            f"&challenge={code_challenge}&nonce={nonce_value}&redirect={redirect_uri}"
        )

    async def exchange(self, transaction: OAuthTransaction, *, code: str) -> OAuthIdentity:
        assert code
        self.transactions.append(transaction)
        return self.identity


@pytest.fixture
def repository() -> MemoryAuthRepository:
    return MemoryAuthRepository()


@pytest.fixture
def sender() -> CapturingSender:
    return CapturingSender()


@pytest.fixture
def oauth() -> FakeOAuth:
    return FakeOAuth()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(
    repository: MemoryAuthRepository,
    sender: CapturingSender,
    oauth: FakeOAuth,
    clock: Clock,
) -> AuthService:
    return AuthService(
        repository,
        sender,
        oauth,
        AuthPolicy(pepper="test-pepper", otp_send_limit=2, otp_verify_limit=3),
        enabled_providers=frozenset({AuthProvider.EMAIL, AuthProvider.GOOGLE}),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_email_code_is_hashed_single_use_and_issues_session(
    service: AuthService,
    repository: MemoryAuthRepository,
    sender: CapturingSender,
) -> None:
    await service.request_email_code("person@example.com", "192.0.2.1")
    email, code, minutes = sender.messages[0]
    assert email == "person@example.com"
    assert re.fullmatch(r"[0-9]{6}", code)
    assert minutes == 10
    assert repository.challenge is not None
    assert code not in repository.challenge.code_hash
    assert "person@example.com" not in repository.challenge.email_hash

    issued = await service.verify_email_code("person@example.com", code, "192.0.2.1", "test-agent")
    assert issued.session.principal.roles == frozenset({Role.CREDENTIAL_HOLDER})
    assert issued.token not in issued.session.token_hash
    assert issued.csrf_token not in issued.session.csrf_hash
    with pytest.raises(InvalidAuthentication):
        await service.verify_email_code("person@example.com", code, "192.0.2.1", "test-agent")


@pytest.mark.asyncio
async def test_email_challenge_attempt_and_rate_limits_fail_closed(
    service: AuthService, sender: CapturingSender
) -> None:
    await service.request_email_code("person@example.com", "192.0.2.1")
    with pytest.raises(InvalidAuthentication):
        await service.verify_email_code("person@example.com", "000000", "192.0.2.2", "agent")
    await service.request_email_code("person@example.com", "192.0.2.1")
    with pytest.raises(RateLimited) as error:
        await service.request_email_code("person@example.com", "192.0.2.1")
    assert error.value.retry_after_seconds > 0
    assert len(sender.messages) == 2


@pytest.mark.asyncio
async def test_delivery_failure_preserves_previous_usable_challenge(
    service: AuthService,
    repository: MemoryAuthRepository,
    sender: CapturingSender,
) -> None:
    await service.request_email_code("person@example.com", "192.0.2.1")
    original = repository.challenge
    sender.unavailable = True
    with pytest.raises(AuthenticationUnavailable):
        await service.request_email_code("person@example.com", "192.0.2.2")
    assert repository.challenge is original


@pytest.mark.asyncio
async def test_expired_code_and_inactive_principal_are_rejected(
    service: AuthService,
    repository: MemoryAuthRepository,
    sender: CapturingSender,
    clock: Clock,
) -> None:
    await service.request_email_code("person@example.com", "192.0.2.1")
    code = sender.messages[0][1]
    clock.now += timedelta(minutes=11)
    with pytest.raises(InvalidAuthentication):
        await service.verify_email_code("person@example.com", code, "192.0.2.2", "agent")
    clock.now = NOW
    await service.request_email_code("blocked@example.com", "192.0.2.3")
    repository.identities[(AuthProvider.EMAIL, "blocked@example.com")] = Principal(
        "prn_blocked123", None, None, frozenset(), active=False
    )
    with pytest.raises(InvalidAuthentication):
        await service.verify_email_code(
            "blocked@example.com", sender.messages[-1][1], "192.0.2.3", "agent"
        )


@pytest.mark.asyncio
async def test_oauth_state_pkce_nonce_and_replay_protection(
    service: AuthService,
    repository: MemoryAuthRepository,
    oauth: FakeOAuth,
) -> None:
    start = await service.start_oauth(
        AuthProvider.GOOGLE, "https://app.example/callback", "192.0.2.4"
    )
    assert "challenge=" in start.authorization_url
    assert "nonce=" in start.authorization_url
    state = re.search(r"state=([^&]+)", start.authorization_url)
    nonce = re.search(r"nonce=([^&]+)", start.authorization_url)
    assert state and nonce
    oauth.identity = replace(oauth.identity, nonce=nonce.group(1))
    issued = await service.complete_oauth(
        AuthProvider.GOOGLE,
        state.group(1),
        start.flow_token,
        "auth-code",
        "192.0.2.4",
        "agent",
    )
    assert issued.session.principal.id == "prn_abcdefgh"
    assert repository.transactions == {}
    with pytest.raises(InvalidAuthentication):
        await service.complete_oauth(
            AuthProvider.GOOGLE,
            state.group(1),
            start.flow_token,
            "replay",
            "192.0.2.4",
            "agent",
        )


@pytest.mark.asyncio
async def test_oauth_rejects_disabled_mismatch_and_bad_nonce(
    service: AuthService, oauth: FakeOAuth
) -> None:
    with pytest.raises(ProviderDisabled):
        await service.start_oauth(AuthProvider.GITHUB, "https://app.example/callback", "192.0.2.1")
    with pytest.raises(ProviderDisabled):
        await service.start_oauth(AuthProvider.EMAIL, "https://app.example/callback", "192.0.2.1")
    with pytest.raises(ProviderDisabled):
        await service.complete_oauth(
            AuthProvider.EMAIL, "state", "state", "code", "192.0.2.1", "agent"
        )
    start = await service.start_oauth(
        AuthProvider.GOOGLE, "https://app.example/callback", "192.0.2.1"
    )
    state = re.search(r"state=([^&]+)", start.authorization_url)
    assert state
    oauth.identity = OAuthIdentity(AuthProvider.GITHUB, "subject", None)
    with pytest.raises(InvalidAuthentication):
        await service.complete_oauth(
            AuthProvider.GOOGLE,
            state.group(1),
            start.flow_token,
            "code",
            "192.0.2.1",
            "agent",
        )


@pytest.mark.asyncio
async def test_oauth_flow_cookie_binding_and_rate_limit(
    service: AuthService, repository: MemoryAuthRepository, oauth: FakeOAuth
) -> None:
    start = await service.start_oauth(
        AuthProvider.GOOGLE, "https://app.example/callback", "192.0.2.8"
    )
    state = re.search(r"state=([^&]+)", start.authorization_url)
    assert state
    with pytest.raises(InvalidAuthentication):
        await service.complete_oauth(
            AuthProvider.GOOGLE,
            state.group(1),
            "different-browser",
            "code",
            "192.0.2.8",
            "agent",
        )
    assert repository.transactions
    for _ in range(20):
        await service.start_oauth(AuthProvider.GOOGLE, "https://app.example/callback", "192.0.2.9")
    with pytest.raises(RateLimited):
        await service.start_oauth(AuthProvider.GOOGLE, "https://app.example/callback", "192.0.2.9")
    start = await service.start_oauth(
        AuthProvider.GOOGLE, "https://app.example/callback", "192.0.2.1"
    )
    state = re.search(r"state=([^&]+)", start.authorization_url)
    assert state
    oauth.identity = OAuthIdentity(AuthProvider.GOOGLE, "subject", None, nonce="wrong")
    with pytest.raises(InvalidAuthentication):
        await service.complete_oauth(
            AuthProvider.GOOGLE,
            state.group(1),
            start.flow_token,
            "code",
            "192.0.2.1",
            "agent",
        )


@pytest.mark.asyncio
async def test_session_expiry_csrf_and_logout(
    service: AuthService,
    repository: MemoryAuthRepository,
    sender: CapturingSender,
    clock: Clock,
) -> None:
    await service.request_email_code("person@example.com", "192.0.2.1")
    issued = await service.verify_email_code(
        "person@example.com", sender.messages[0][1], "192.0.2.1", "agent"
    )
    session = await service.authenticate(issued.token)
    assert repository.touches == [session.id]
    rotated = await service.renew_session(issued.token)
    with pytest.raises(InvalidAuthentication):
        await service.authenticate(issued.token)
    assert (await service.authenticate(rotated.token)).id == rotated.session.id
    await service.validate_csrf(
        session,
        issued.csrf_token,
        issued.csrf_token,
        "https://app.example",
        frozenset({"https://app.example"}),
    )
    for cookie, header, origin in (
        (None, issued.csrf_token, "https://app.example"),
        (issued.csrf_token, "wrong", "https://app.example"),
        (issued.csrf_token, issued.csrf_token, "https://evil.example"),
        ("same-wrong-value", "same-wrong-value", "https://app.example"),
    ):
        with pytest.raises(CsrfRejected):
            await service.validate_csrf(
                session, cookie, header, origin, frozenset({"https://app.example"})
            )
    await service.logout(rotated.session)
    with pytest.raises(InvalidAuthentication):
        await service.authenticate(issued.token)
    with pytest.raises(InvalidAuthentication):
        await service.authenticate(None)
    repository.sessions[issued.session.token_hash] = replace(issued.session, revoked=False)
    clock.now += timedelta(days=8)
    with pytest.raises(InvalidAuthentication):
        await service.authenticate(issued.token)
    with pytest.raises(ValueError):
        issued.session.is_active(datetime(2026, 9, 9), 60)
    with pytest.raises(InvalidAuthentication):
        await service.renew_session(None)
    with pytest.raises(InvalidAuthentication):
        await service.renew_session("unknown-session")


@pytest.fixture
def client(service: AuthService) -> Generator[TestClient]:
    settings = Settings(
        environment="test",
        auth_allowed_origins="https://app.example",
        auth_success_redirect_url="https://app.example/signed-in",
        auth_google_enabled=True,
        auth_google_client_id="client",
        auth_google_client_secret=TEST_OAUTH_SECRET,
        auth_google_redirect_uri="https://api.example/v1/auth/oauth/google/callback",
        _env_file=None,
    )
    app = create_app(settings, store=None, auth_service=service)
    with TestClient(app, base_url="https://api.example") as test_client:
        yield test_client


def test_auth_openapi_binds_closed_responses_statuses_and_problem_media(
    client: TestClient,
) -> None:
    schema = client.app.openapi()  # type: ignore[attr-defined]
    providers = schema["paths"]["/v1/auth/providers"]["get"]
    assert providers["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ProvidersResponse"
    )
    assert providers["security"] == []
    request_code = schema["paths"]["/v1/auth/email/code"]["post"]
    assert set(request_code["responses"]) == {"202", "400", "429", "503"}
    assert "application/problem+json" in request_code["responses"]["503"]["content"]
    session = schema["paths"]["/v1/auth/session"]["get"]
    assert session["security"] == [{"cookieAuth": []}]
    assert session["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AuthSessionResponse"
    )
    assert schema["components"]["schemas"]["AuthSessionResponse"]["additionalProperties"] is False
    oauth_start = schema["paths"]["/v1/auth/oauth/{provider}/start"]["get"]
    oauth_callback = schema["paths"]["/v1/auth/oauth/{provider}/callback"]["get"]
    assert "302" in oauth_start["responses"] and "200" not in oauth_start["responses"]
    assert "302" in oauth_callback["responses"] and "200" not in oauth_callback["responses"]


def test_http_email_session_and_csrf_flow(
    client: TestClient, sender: CapturingSender, repository: MemoryAuthRepository
) -> None:
    repository.identities[(AuthProvider.EMAIL, "person@example.com")] = Principal(
        "prn_abcdefgh",
        "ten_abcdefgh",
        "acct_abcdefgh",
        frozenset({Role.CREDENTIAL_HOLDER}),
    )
    assert client.get("/v1/auth/providers").json() == {"providers": ["email", "google"]}
    request = client.post("/v1/auth/email/code", json={"email": "Person@Example.com"})
    assert request.status_code == 202
    code = sender.messages[0][1]
    verify = client.post(
        "/v1/auth/email/verify", json={"email": "person@example.com", "code": code}
    )
    assert verify.status_code == 200
    assert verify.json() == {
        "principal_id": "prn_abcdefgh",
        "tenant_id": "ten_abcdefgh",
        "account_id": "acct_abcdefgh",
        "roles": ["credential_holder"],
        "expires_at": verify.json()["expires_at"],
    }
    cookie_headers = verify.headers.get_list("set-cookie")
    assert any("HttpOnly" in header and "Secure" in header for header in cookie_headers)
    assert any("och_csrf=" in header and "HttpOnly" not in header for header in cookie_headers)
    prior_session = client.cookies.get("och_session")
    current = client.get("/v1/auth/session")
    assert current.status_code == 200
    assert current.json()["account_id"] == "acct_abcdefgh"
    assert client.cookies.get("och_session") == prior_session
    prior_csrf = client.cookies.get("och_csrf")
    refresh = client.post(
        "/v1/auth/session/refresh",
        headers={"Origin": "https://app.example", "X-CSRF-Token": str(prior_csrf)},
    )
    assert refresh.status_code == 200
    assert refresh.json()["account_id"] == "acct_abcdefgh"
    assert client.cookies.get("och_session") != prior_session
    rejected = client.delete(
        "/v1/auth/session", headers={"Origin": "https://app.example", "X-CSRF-Token": "bad"}
    )
    assert rejected.status_code == 403
    csrf = client.cookies.get("och_csrf")
    logout = client.delete(
        "/v1/auth/session",
        headers={"Origin": "https://app.example", "X-CSRF-Token": str(csrf)},
    )
    assert logout.status_code == 204
    assert client.get("/v1/auth/session").status_code == 401


def test_http_email_verification_omits_unscoped_session_fields(
    client: TestClient, sender: CapturingSender
) -> None:
    assert (
        client.post("/v1/auth/email/code", json={"email": "new-person@example.com"}).status_code
        == 202
    )

    verify = client.post(
        "/v1/auth/email/verify",
        json={"email": "new-person@example.com", "code": sender.messages[0][1]},
    )

    assert verify.status_code == 200
    assert verify.json() == {
        "principal_id": verify.json()["principal_id"],
        "roles": ["credential_holder"],
        "expires_at": verify.json()["expires_at"],
    }
    assert verify.json()["principal_id"].startswith("prn_")
    assert "tenant_id" not in verify.json()
    assert "account_id" not in verify.json()


def test_http_uniform_failures_and_rate_limit(
    client: TestClient, sender: CapturingSender, clock: Clock
) -> None:
    assert client.post("/v1/auth/email/code", json={"email": "not-an-email"}).status_code == 400
    assert (
        client.post(
            "/v1/auth/email/verify",
            json={"email": "nobody@example.com", "code": "000000"},
        ).status_code
        == 400
    )
    sender.unavailable = True
    unavailable = client.post("/v1/auth/email/code", json={"email": "mail@example.com"})
    assert unavailable.status_code == 503
    sender.unavailable = False
    clock.now += timedelta(hours=1)
    assert (
        client.post("/v1/auth/email/code", json={"email": "flood@example.com"}).status_code == 202
    )
    assert (
        client.post("/v1/auth/email/code", json={"email": "flood@example.com"}).status_code == 202
    )
    limited = client.post("/v1/auth/email/code", json={"email": "flood@example.com"})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_http_oauth_redirects_and_invalid_callbacks(client: TestClient, oauth: FakeOAuth) -> None:
    start = client.get("/v1/auth/oauth/google/start", follow_redirects=False)
    assert start.status_code == 302
    location = start.headers["location"]
    state = re.search(r"state=([^&]+)", location)
    nonce = re.search(r"nonce=([^&]+)", location)
    assert state and nonce
    flow_cookie = client.cookies.get("och_oauth_flow")
    assert flow_cookie == state.group(1)
    client.cookies.delete("och_oauth_flow")
    unbound = client.get(
        "/v1/auth/oauth/google/callback",
        params={"state": state.group(1), "code": "provider-code"},
        follow_redirects=False,
    )
    assert unbound.status_code == 400
    start = client.get("/v1/auth/oauth/google/start", follow_redirects=False)
    location = start.headers["location"]
    state = re.search(r"state=([^&]+)", location)
    nonce = re.search(r"nonce=([^&]+)", location)
    assert state and nonce
    oauth.identity = replace(oauth.identity, nonce=nonce.group(1))
    callback = client.get(
        "/v1/auth/oauth/google/callback",
        params={"state": state.group(1), "code": "provider-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "https://app.example/signed-in"
    assert client.get("/v1/auth/oauth/github/start").status_code == 404
    assert client.get("/v1/auth/oauth/unknown/start").status_code == 404
    assert (
        client.get(
            "/v1/auth/oauth/google/callback", params={"state": "bad", "code": "bad"}
        ).status_code
        == 400
    )


def test_cors_allows_exact_configured_origin_only(client: TestClient) -> None:
    allowed = client.options(
        "/v1/auth/email/code",
        headers={
            "Origin": "https://app.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = client.options(
        "/v1/auth/email/code",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert denied.status_code == 400


@pytest.mark.asyncio
async def test_reusable_auth_dependency_maps_failures(
    service: AuthService,
) -> None:
    settings = Settings(environment="test", _env_file=None)
    app = create_app(settings, store=None, auth_service=service)
    dependency = app.state.auth
    with pytest.raises(HTTPException) as unauthenticated:
        await dependency.current_session(None)
    assert unauthenticated.value.status_code == 401
