"""Validated process configuration."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clearinghouse.domain.email import normalize_email


class Settings(BaseSettings):
    """Environment settings parsed once at process startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CLEARINGHOUSE_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    otel_exporter_otlp_endpoint: str | None = None
    otel_metric_export_interval_millis: int = Field(default=60_000, ge=1_000, le=300_000)
    database_url: str = Field(
        default="postgresql+asyncpg://clearinghouse:clearinghouse@localhost:5432/clearinghouse",
        min_length=1,
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)
    auth_pepper: SecretStr = SecretStr("dev-auth-pepper-change-me")
    credential_pepper: SecretStr = SecretStr("dev-credential-pepper-change-me")
    auth_resend_api_url: str = "https://api.resend.com"
    auth_resend_api_key: SecretStr = SecretStr("re_dev_replace_me")
    auth_resend_from: str = "Livepeer Clearinghouse <auth@example.invalid>"
    auth_otp_ttl_seconds: int = Field(default=600, ge=60, le=900)
    auth_otp_max_attempts: int = Field(default=5, ge=1, le=10)
    auth_otp_send_limit: int = Field(default=5, ge=1, le=100)
    auth_otp_send_window_seconds: int = Field(default=3600, ge=60, le=86400)
    auth_otp_verify_limit: int = Field(default=10, ge=1, le=100)
    auth_otp_verify_window_seconds: int = Field(default=900, ge=60, le=86400)
    auth_oauth_attempt_limit: int = Field(default=20, ge=1, le=100)
    auth_oauth_attempt_window_seconds: int = Field(default=900, ge=60, le=86400)
    auth_session_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    auth_session_absolute_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    auth_session_inactivity_seconds: int = Field(default=3600, ge=60, le=86400)
    auth_cookie_secure: bool = True
    auth_allowed_origins: str = "http://127.0.0.1:4173,http://127.0.0.1:4174"
    auth_success_redirect_url: str = "http://127.0.0.1:4174/"
    auth_google_enabled: bool = False
    auth_google_client_id: str | None = None
    auth_google_client_secret: SecretStr | None = None
    auth_google_redirect_uri: str | None = None
    auth_github_enabled: bool = False
    auth_github_client_id: str | None = None
    auth_github_client_secret: SecretStr | None = None
    auth_github_redirect_uri: str | None = None
    identity_invitation_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    operator_bootstrap_email: SecretStr | None = None
    operator_bootstrap_secret: SecretStr | None = None
    signer_webhook_secret: SecretStr = SecretStr("dev-signer-webhook-secret-change-me")
    signer_session_pepper: SecretStr = SecretStr("dev-signer-session-pepper-change-me")
    signer_global_exposure_cap: int = Field(default=0, ge=0, le=10**78 - 1)
    signer_id: str = Field(
        default="signer_default0000", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$"
    )
    signer_url: str = "http://127.0.0.1:8935"
    signer_discovery_url: str = "http://127.0.0.1:8935"
    kafka_bootstrap_servers: str = Field(default="127.0.0.1:9092", min_length=1, max_length=2048)
    kafka_metering_topic: str = Field(default="gateway-events", min_length=1, max_length=249)
    kafka_metering_group_id: str = Field(
        default="clearinghouse-metering-v1", min_length=1, max_length=255
    )
    kafka_metering_client_id: str = Field(
        default="clearinghouse-metering", min_length=1, max_length=255
    )
    metering_max_payload_bytes: int = Field(default=1_048_576, ge=1024, le=8_388_608)
    metering_reconciliation_batch_size: int = Field(default=100, ge=1, le=1000)
    metering_reconciliation_interval_seconds: int = Field(default=30, ge=1, le=3600)
    metering_confirmation_grace_seconds: int = Field(default=30, ge=30, le=3600)
    metering_heartbeat_interval_seconds: int = Field(default=10, ge=1, le=300)
    metering_heartbeat_stale_seconds: int = Field(default=60, ge=10, le=3600)

    @field_validator("otel_exporter_otlp_endpoint", mode="before")
    @classmethod
    def empty_otel_endpoint_disables_export(cls, value: object) -> object:
        """Treat Compose's optional empty interpolation as an unset exporter."""
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_authentication(self) -> Settings:
        """Reject partial provider and unsafe production configuration."""
        self._validate_http_url("auth_resend_api_url", self.auth_resend_api_url)
        self._validate_http_url("auth_success_redirect_url", self.auth_success_redirect_url)
        self._validate_http_url("signer_url", self.signer_url)
        self._validate_http_url("signer_discovery_url", self.signer_discovery_url)
        if self.otel_exporter_otlp_endpoint is not None:
            self._validate_http_url("otel_exporter_otlp_endpoint", self.otel_exporter_otlp_endpoint)
        self._validate_provider(
            "google",
            self.auth_google_enabled,
            self.auth_google_client_id,
            self.auth_google_client_secret,
            self.auth_google_redirect_uri,
        )
        self._validate_provider(
            "github",
            self.auth_github_enabled,
            self.auth_github_client_id,
            self.auth_github_client_secret,
            self.auth_github_redirect_uri,
        )
        if self.auth_session_absolute_ttl_seconds < self.auth_session_ttl_seconds:
            raise ValueError("absolute session lifetime must not be shorter than session lifetime")
        if self.metering_heartbeat_stale_seconds < 2 * self.metering_heartbeat_interval_seconds:
            raise ValueError("metering heartbeat stale threshold must be at least two intervals")
        if not self.auth_origins:
            raise ValueError("at least one allowed authentication origin is required")
        for origin in self.auth_origins:
            parsed_origin = urlparse(origin)
            if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment:
                raise ValueError("authentication origins must not contain path, query, or fragment")
        bootstrap_values = (self.operator_bootstrap_email, self.operator_bootstrap_secret)
        if any(bootstrap_values) and not all(bootstrap_values):
            raise ValueError("operator bootstrap requires both email and secret")
        if self.operator_bootstrap_email is not None:
            try:
                normalize_email(self.operator_bootstrap_email.get_secret_value())
            except ValueError as error:
                raise ValueError("operator bootstrap email is invalid") from error
            bootstrap_secret = self.operator_bootstrap_secret
            if bootstrap_secret is None or len(bootstrap_secret.get_secret_value()) < 32:
                raise ValueError("operator bootstrap secret must be at least 32 characters")
        if self.environment == "production":
            webhook = self.signer_webhook_secret.get_secret_value()
            signer_pepper = self.signer_session_pepper.get_secret_value()
            if (
                len(webhook) < 32
                or len(set(webhook)) < 8
                or any(word in webhook.lower() for word in ("replace", "change", "example", "dev"))
            ):
                raise ValueError(
                    "production requires a signer webhook secret of at least 32 characters"
                )
            if len(signer_pepper) < 32 or "change" in signer_pepper.lower():
                raise ValueError("production requires a strong signer session pepper")
            if len(self.auth_pepper.get_secret_value()) < 32:
                raise ValueError(
                    "production requires an authentication pepper of at least 32 characters"
                )
            if len(self.credential_pepper.get_secret_value()) < 32:
                raise ValueError(
                    "production requires a credential pepper of at least 32 characters"
                )
            resend_key = self.auth_resend_api_key.get_secret_value()
            if len(resend_key) < 20 or "replace" in resend_key.lower():
                raise ValueError("production requires a non-placeholder Resend API key")
            if (
                "example.invalid" in self.auth_resend_from
                or ".example" in self.auth_resend_from
                or "@" not in self.auth_resend_from
            ):
                raise ValueError("production requires a deliverable Resend from address")
            if not self.auth_cookie_secure:
                raise ValueError("production browser cookies must be secure")
            if bootstrap_secret := self.operator_bootstrap_secret:
                raw_bootstrap_secret = bootstrap_secret.get_secret_value()
                placeholder_words = ("replace", "change", "example", "bootstrap", "password")
                if len(set(raw_bootstrap_secret)) < 8 or any(
                    word in raw_bootstrap_secret.lower() for word in placeholder_words
                ):
                    raise ValueError("production requires a non-placeholder bootstrap secret")
            production_urls = [
                self.auth_resend_api_url,
                self.auth_success_redirect_url,
                *self.auth_origins,
                *([self.auth_google_redirect_uri] if self.auth_google_redirect_uri else []),
                *([self.auth_github_redirect_uri] if self.auth_github_redirect_uri else []),
            ]
            if any(urlparse(url).scheme != "https" for url in production_urls):
                raise ValueError("production authentication URLs must use https")
            if any(
                urlparse(url).scheme != "https"
                for url in (self.signer_url, self.signer_discovery_url)
            ):
                raise ValueError("production signer client URLs must use https")
        return self

    @property
    def operator_bootstrap_email_value(self) -> str | None:
        """Return the normalized bootstrap identity only at the composition boundary."""
        if self.operator_bootstrap_email is None:
            return None
        return normalize_email(self.operator_bootstrap_email.get_secret_value())

    @property
    def auth_origins(self) -> frozenset[str]:
        """Return normalized, exact origins accepted for CSRF validation."""
        return frozenset(
            value.strip().rstrip("/")
            for value in self.auth_allowed_origins.split(",")
            if value.strip()
        )

    @staticmethod
    def _validate_http_url(name: str, value: str) -> None:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"{name} must be an HTTP(S) URL without user information, query, or fragment"
            )

    @classmethod
    def _validate_provider(
        cls,
        name: str,
        enabled: bool,
        client_id: str | None,
        client_secret: SecretStr | None,
        redirect_uri: str | None,
    ) -> None:
        values = (client_id, client_secret, redirect_uri)
        if enabled and not all(values):
            raise ValueError(f"enabled {name} OAuth requires client id, secret, and redirect URI")
        if any(values) and not all(values):
            raise ValueError(f"partial {name} OAuth configuration is not allowed")
        if redirect_uri is not None:
            cls._validate_http_url(f"auth_{name}_redirect_uri", redirect_uri)


class MeteringWorkerSettings(BaseSettings):
    """Minimal configuration boundary for the standalone metering worker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CLEARINGHOUSE_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    otel_exporter_otlp_endpoint: str | None = None
    otel_metric_export_interval_millis: int = Field(default=60_000, ge=1_000, le=300_000)
    database_url: str = Field(min_length=1)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    signer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
    kafka_bootstrap_servers: str = Field(min_length=1, max_length=2048)
    kafka_metering_topic: str = Field(min_length=1, max_length=249)
    kafka_metering_group_id: str = Field(min_length=1, max_length=255)
    kafka_metering_client_id: str = Field(min_length=1, max_length=255)
    metering_max_payload_bytes: int = Field(default=1_048_576, ge=1024, le=8_388_608)
    metering_reconciliation_batch_size: int = Field(default=100, ge=1, le=1000)
    metering_reconciliation_interval_seconds: int = Field(default=30, ge=1, le=3600)
    metering_confirmation_grace_seconds: int = Field(default=30, ge=30, le=3600)
    metering_heartbeat_interval_seconds: int = Field(default=10, ge=1, le=300)
    metering_heartbeat_stale_seconds: int = Field(default=60, ge=10, le=3600)

    @field_validator("otel_exporter_otlp_endpoint", mode="before")
    @classmethod
    def empty_otel_endpoint_disables_export(cls, value: object) -> object:
        """Treat Compose's optional empty interpolation as an unset exporter."""
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_heartbeat_window(self) -> MeteringWorkerSettings:
        if self.metering_heartbeat_stale_seconds < 2 * self.metering_heartbeat_interval_seconds:
            raise ValueError("metering heartbeat stale threshold must be at least two intervals")
        if self.otel_exporter_otlp_endpoint is not None:
            Settings._validate_http_url(
                "otel_exporter_otlp_endpoint", self.otel_exporter_otlp_endpoint
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the immutable process configuration singleton."""
    return Settings()


@lru_cache(maxsize=1)
def get_metering_worker_settings() -> MeteringWorkerSettings:
    """Return the minimal standalone-worker configuration singleton."""
    return MeteringWorkerSettings()
