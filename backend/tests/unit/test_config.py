"""Configuration boundary tests."""

from typing import Any, cast

import pytest
from pydantic import ValidationError

from clearinghouse.infrastructure.config import (
    MeteringWorkerSettings,
    Settings,
    get_metering_worker_settings,
    get_settings,
)


def test_settings_apply_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.database_pool_size == 10
    assert settings.otel_exporter_otlp_endpoint is None
    assert settings.otel_metric_export_interval_millis == 60_000


def test_settings_factory_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLEARINGHOUSE_ENVIRONMENT", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_metering_worker_settings_are_minimal_cached_and_validate_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "CLEARINGHOUSE_DATABASE_URL": "postgresql+asyncpg://worker.invalid/db",
        "CLEARINGHOUSE_SIGNER_ID": "signer_00000000",
        "CLEARINGHOUSE_KAFKA_BOOTSTRAP_SERVERS": "broker:9092",
        "CLEARINGHOUSE_KAFKA_METERING_TOPIC": "events",
        "CLEARINGHOUSE_KAFKA_METERING_GROUP_ID": "worker-group",
        "CLEARINGHOUSE_KAFKA_METERING_CLIENT_ID": "worker-client",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_metering_worker_settings.cache_clear()
    try:
        settings = get_metering_worker_settings()
        assert settings is get_metering_worker_settings()
        assert settings.environment == "development"
        assert settings.otel_exporter_otlp_endpoint is None
        assert not hasattr(settings, "auth_resend_api_key")
        with pytest.raises(ValidationError, match="at least two intervals"):
            cast(Any, MeteringWorkerSettings)(
                **{
                    key.removeprefix("CLEARINGHOUSE_").lower(): value
                    for key, value in values.items()
                },
                metering_heartbeat_interval_seconds=20,
                metering_heartbeat_stale_seconds=30,
                _env_file=None,
            )
    finally:
        get_metering_worker_settings.cache_clear()


@pytest.mark.parametrize("settings_type", [Settings, MeteringWorkerSettings])
def test_telemetry_configuration_is_bounded_and_rejects_secret_bearing_urls(
    settings_type: type[Settings] | type[MeteringWorkerSettings],
) -> None:
    common: dict[str, object] = {"_env_file": None}
    if settings_type is MeteringWorkerSettings:
        common.update(
            database_url="postgresql+asyncpg://worker.invalid/db",
            signer_id="signer_00000000",
            kafka_bootstrap_servers="broker:9092",
            kafka_metering_topic="events",
            kafka_metering_group_id="worker-group",
            kafka_metering_client_id="worker-client",
        )
    with pytest.raises(ValidationError, match="otel_exporter_otlp_endpoint"):
        cast(Any, settings_type)(
            **common,
            otel_exporter_otlp_endpoint="https://collector.invalid?token=secret",
        )
    with pytest.raises(ValidationError):
        cast(Any, settings_type)(**common, otel_metric_export_interval_millis=999)
    configured = cast(Any, settings_type)(
        **common,
        otel_exporter_otlp_endpoint="http://observability:4318",
        otel_metric_export_interval_millis=5_000,
    )
    assert configured.otel_exporter_otlp_endpoint == "http://observability:4318"
    disabled = cast(Any, settings_type)(**common, otel_exporter_otlp_endpoint="")
    assert disabled.otel_exporter_otlp_endpoint is None


def test_settings_decode_prefixed_environment(monkeypatch: object) -> None:
    monkeypatch.setenv("CLEARINGHOUSE_ENVIRONMENT", "production")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_DATABASE_POOL_SIZE", "20")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_AUTH_PEPPER", "a" * 32)  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_CREDENTIAL_PEPPER", "b" * 32)  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET", "0123456789abcdefghijklmnopqrstuv")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_SIGNER_SESSION_PEPPER", "zyxwvutsrqponmlkjihgfedcba987654")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_SIGNER_URL", "https://signer.example")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_SIGNER_DISCOVERY_URL", "https://signer.example")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_AUTH_RESEND_API_KEY", "re_abcdefghijklmnopqrstuvwxyz")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_AUTH_RESEND_FROM", "auth@livepeer.org")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_AUTH_ALLOWED_ORIGINS", "https://app.example")  # type: ignore[attr-defined]
    monkeypatch.setenv("CLEARINGHOUSE_AUTH_SUCCESS_REDIRECT_URL", "https://app.example/")  # type: ignore[attr-defined]
    settings = Settings(_env_file=None)
    assert settings.environment == "production"
    assert settings.database_pool_size == 20


def test_production_rejects_weak_nondefault_credential_pepper() -> None:
    with pytest.raises(ValidationError, match="credential pepper"):
        Settings(
            environment="production",
            auth_pepper="a" * 32,
            credential_pepper="unique-but-short",
            signer_webhook_secret="0123456789abcdefghijklmnopqrstuv",  # noqa: S106
            signer_session_pepper="zyxwvutsrqponmlkjihgfedcba987654",
            auth_resend_api_key="configured",
            auth_allowed_origins="https://app.example",
            auth_success_redirect_url="https://app.example/",
            _env_file=None,
        )


def production_settings(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": "production",
        "auth_pepper": "a" * 32,
        "credential_pepper": "b" * 32,
        "signer_webhook_secret": "0123456789abcdefghijklmnopqrstuv",
        "signer_session_pepper": "zyxwvutsrqponmlkjihgfedcba987654",
        "signer_url": "https://signer.example",
        "signer_discovery_url": "https://signer.example",
        "auth_resend_api_key": "re_abcdefghijklmnopqrstuvwxyz",
        "auth_resend_from": "auth@livepeer.org",
        "auth_allowed_origins": "https://app.example",
        "auth_success_redirect_url": "https://app.example/",
        "_env_file": None,
    }
    values.update(updates)
    return values


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"metering_heartbeat_interval_seconds": 20, "metering_heartbeat_stale_seconds": 30},
            "at least two intervals",
        ),
        (
            {"operator_bootstrap_email": "ops@livepeer.org", "operator_bootstrap_secret": "short"},
            "at least 32 characters",
        ),
        ({"signer_webhook_secret": "weak-example-secret-that-is-long-enough"}, "webhook secret"),
        ({"signer_session_pepper": "change-this-signer-pepper-xxxxxxxx"}, "session pepper"),
        (
            {
                "operator_bootstrap_email": "ops@livepeer.org",
                "operator_bootstrap_secret": "bootstrap-password-placeholder-123456789",
            },
            "bootstrap secret",
        ),
        ({"signer_url": "http://signer.example"}, "signer client URLs"),
    ],
)
def test_settings_reject_unsafe_metering_and_production_combinations(
    updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        cast(Any, Settings)(**production_settings(**updates))


def test_production_accepts_a_nonplaceholder_bootstrap_secret() -> None:
    settings = cast(Any, Settings)(
        **production_settings(
            operator_bootstrap_email="ops@livepeer.org",
            operator_bootstrap_secret="9qZ!vN4@tR7#xK2$mP8&wC5*eH3-yL6+",  # noqa: S106
        )
    )
    assert settings.operator_bootstrap_email_value == "ops@livepeer.org"
