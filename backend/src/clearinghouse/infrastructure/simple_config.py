"""Validated configuration for the simplified distribution."""

from __future__ import annotations

from functools import cached_property, lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CLEARINGHOUSE_", case_sensitive=False, extra="ignore"
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_path: Path = Path("./data/clearinghouse.db")
    database_busy_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65_535)
    public_url: str = "http://localhost:8080"
    allowed_origins: str = "http://localhost:8080,http://127.0.0.1:8080"
    cookie_secure: bool = False
    admin_email: str | None = None
    auth_pepper: SecretStr = SecretStr("dev-auth-pepper-change-me")
    workload_pepper: SecretStr = SecretStr("dev-workload-pepper-change-me")
    auth_resend_api_url: str = "https://api.resend.com"
    auth_resend_api_key: SecretStr = SecretStr("re_dev_replace_me")
    auth_resend_from: str = "Livepeer Clearinghouse <auth@example.invalid>"
    auth_google_enabled: bool = False
    auth_google_client_id: str | None = None
    auth_google_client_secret: SecretStr | None = None
    auth_github_enabled: bool = False
    auth_github_client_id: str | None = None
    auth_github_client_secret: SecretStr | None = None
    signer_id: str = "signer_default0000"
    signer_webhook_secret: SecretStr = SecretStr("dev-signer-webhook-secret-change-me")
    discovery_urls: str = "http://remote-signer:8935/discover-orchestrators"
    lv2v_offers_file: Path | None = None
    discovery_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    kafka_bootstrap_servers: str = "redpanda:9092"
    kafka_metering_topic: str = "livepeer-gateway-events"
    kafka_metering_group_id: str = "clearinghouse-core-v1"
    kafka_retention_ms: int = Field(default=604_800_000, ge=604_800_000)

    @field_validator("lv2v_offers_file", mode="before")
    @classmethod
    def empty_lv2v_file_is_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_configuration(self) -> CoreSettings:
        for value in (self.public_url, self.auth_resend_api_url, *self.discovery_url_values):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("configured service URLs must be absolute HTTP(S) URLs")
        for name in ("google", "github"):
            enabled = getattr(self, f"auth_{name}_enabled")
            values = (
                getattr(self, f"auth_{name}_client_id"),
                getattr(self, f"auth_{name}_client_secret"),
            )
            if enabled and not all(values):
                raise ValueError(f"enabled {name} OAuth requires client id and secret")
            if any(values) and not all(values):
                raise ValueError(f"partial {name} OAuth configuration is not allowed")
        if self.lv2v_offers_file is not None and not self.lv2v_offers_file.is_file():
            raise ValueError("configured LV2V offers file does not exist")
        if self.environment == "production":
            secrets_to_check = (
                self.auth_pepper.get_secret_value(),
                self.workload_pepper.get_secret_value(),
                self.signer_webhook_secret.get_secret_value(),
            )
            if any(len(value) < 32 or "change" in value.lower() for value in secrets_to_check):
                raise ValueError("production requires strong authentication secrets")
            if urlparse(self.public_url).scheme != "https" or not self.cookie_secure:
                raise ValueError("production requires HTTPS and secure cookies")
            resend_key = self.auth_resend_api_key.get_secret_value()
            if len(resend_key) < 8 or any(
                marker in resend_key.lower() for marker in ("change_me", "replace_me")
            ):
                raise ValueError("production requires a configured Resend API key")
            if not self.origin_values or any(
                urlparse(origin).scheme != "https" for origin in self.origin_values
            ):
                raise ValueError("production browser origins must use HTTPS")
        return self

    @cached_property
    def origin_values(self) -> frozenset[str]:
        return frozenset(
            value.strip().rstrip("/") for value in self.allowed_origins.split(",") if value.strip()
        )

    @cached_property
    def discovery_url_values(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.discovery_urls.split(",") if value.strip())

    @cached_property
    def enabled_oauth(self) -> frozenset[str]:
        return frozenset(
            name
            for name, enabled in (
                ("google", self.auth_google_enabled),
                ("github", self.auth_github_enabled),
            )
            if enabled
        )


@lru_cache(maxsize=1)
def get_core_settings() -> CoreSettings:
    return CoreSettings()
