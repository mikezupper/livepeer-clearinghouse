from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse.application.access import AccessDenied, AccessService
from clearinghouse.domain.auth import AuthProvider, OAuthIdentity
from clearinghouse.infrastructure.sqlite import SqliteStore


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, int]] = []

    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
        self.sent.append((email, code, expires_in_minutes))


@pytest.fixture
async def access(tmp_path):  # type: ignore[no-untyped-def]
    store = SqliteStore(tmp_path / "auth.db")
    await store.initialize()
    sender = RecordingSender()
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    service = AccessService(
        store,
        sender,
        pepper="test-pepper-is-long-enough",
        admin_email="admin@example.com",
        enabled_oauth=frozenset({"github"}),
        clock=lambda: now,
        code_factory=lambda: "123456",
        secret_factory=iter(
            ("session", "csrf", "session-id", "credential-token", "credential-id")
        ).__next__,
    )
    yield service, sender, store, now
    await store.close()


async def test_first_otp_creates_personal_admin_account_and_session(access) -> None:  # type: ignore[no-untyped-def]
    service, sender, _store, _now = access
    assert service.providers == ("email", "github")
    await service.request_code(" Admin@Example.com ")
    assert sender.sent == [("admin@example.com", "123456", 10)]
    issued = await service.verify_code("admin@example.com", "123456")
    assert issued.session.identity.is_admin
    assert (await service.authenticate_session(issued.token)).identity == issued.session.identity
    await service.logout(issued.token)
    with pytest.raises(AccessDenied, match="authentication required"):
        await service.authenticate_session(issued.token)


async def test_invalid_and_expired_codes_are_rejected(access) -> None:  # type: ignore[no-untyped-def]
    service, _sender, _store, now = access
    await service.request_code("person@example.com")
    with pytest.raises(AccessDenied, match="invalid or expired"):
        await service.verify_code("person@example.com", "000000")
    service.clock = lambda: now + timedelta(hours=1)
    with pytest.raises(AccessDenied, match="invalid or expired"):
        await service.verify_code("person@example.com", "123456")


async def test_sdk_credential_is_returned_once_and_revocable(access) -> None:  # type: ignore[no-untyped-def]
    service, _sender, _store, _now = access
    await service.request_code("person@example.com")
    issued = await service.verify_code("person@example.com", "123456")
    created = await service.create_credential(issued.session.identity, " Python gateway ")
    assert created.token.startswith("och_live_")
    assert created.credential.name == "Python gateway"
    assert await service.authenticate_credential(created.token) == created.credential
    assert list(await service.list_credentials(issued.session.identity)) == [created.credential]
    await service.revoke_credential(issued.session.identity, created.credential.id)
    with pytest.raises(AccessDenied, match="invalid account API credential"):
        await service.authenticate_credential(created.token)
    with pytest.raises(AccessDenied, match="credential not found"):
        await service.revoke_credential(issued.session.identity, created.credential.id)


async def test_optional_oauth_is_disabled_by_default_and_uses_verified_email(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class OAuthClient:
        def authorization_url(self, provider, **values):  # type: ignore[no-untyped-def]
            assert provider is AuthProvider.GOOGLE
            assert values["nonce"] == "nonce"
            return "https://accounts.google.com/authorize"

        async def exchange(self, transaction, *, code):  # type: ignore[no-untyped-def]
            assert code == "provider-code"
            assert transaction.pkce_verifier == "verifier-averifier-b"
            return OAuthIdentity(AuthProvider.GOOGLE, "subject-1", "oauth@example.com", "nonce")

    store = SqliteStore(tmp_path / "oauth.db")
    await store.initialize()
    disabled = AccessService(store, RecordingSender(), pepper="test-pepper-is-long-enough")
    with pytest.raises(AccessDenied, match="disabled"):
        await disabled.start_oauth("google", "https://app.example.com/callback")

    secrets = iter(("state", "verifier-a", "verifier-b", "nonce", "token", "csrf", "id"))
    service = AccessService(
        store,
        RecordingSender(),
        pepper="test-pepper-is-long-enough",
        enabled_oauth=frozenset({"google"}),
        oauth_client=OAuthClient(),
        secret_factory=secrets.__next__,
    )
    start = await service.start_oauth("google", "https://app.example.com/callback")
    assert start.authorization_url.startswith("https://accounts.google.com")
    issued = await service.complete_oauth("google", start.state, start.state, "provider-code")
    assert issued.session.identity.email == "oauth@example.com"
    with pytest.raises(AccessDenied, match="invalid OAuth"):
        await service.complete_oauth("google", start.state, start.state, "provider-code")
    await store.close()
