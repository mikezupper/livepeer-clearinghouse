"""Apply the optional one-shot operator bootstrap, then exit."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import create_async_engine

from clearinghouse.application.onboarding import OnboardingService
from clearinghouse.domain.email import normalize_email
from clearinghouse.infrastructure.database import PostgresStore
from clearinghouse.infrastructure.onboarding import PostgresOnboardingRepository


class BootstrapSettings(BaseSettings):
    """Only configuration required by the one-shot bootstrap process."""

    model_config = SettingsConfigDict(env_prefix="CLEARINGHOUSE_", extra="ignore")
    environment: Literal["development", "test", "production"]
    database_url: str = Field(min_length=1)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    auth_pepper: SecretStr
    identity_invitation_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    operator_bootstrap_email: SecretStr | None = None
    operator_bootstrap_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_bootstrap(self) -> BootstrapSettings:
        values = (self.operator_bootstrap_email, self.operator_bootstrap_secret)
        if any(values) and not all(values):
            raise ValueError("operator bootstrap requires paired email and secret")
        if self.operator_bootstrap_email is not None:
            normalize_email(self.operator_bootstrap_email.get_secret_value())
            secret = self.operator_bootstrap_secret
            if secret is None or len(secret.get_secret_value()) < 32:
                raise ValueError("operator bootstrap secret must be at least 32 characters")
        if self.environment == "production":
            if len(self.auth_pepper.get_secret_value()) < 32:
                raise ValueError("production bootstrap requires a strong authentication pepper")
            if self.operator_bootstrap_secret is not None:
                raw = self.operator_bootstrap_secret.get_secret_value()
                if len(set(raw)) < 8 or any(
                    word in raw.lower()
                    for word in ("replace", "change", "example", "bootstrap", "password")
                ):
                    raise ValueError("production requires a non-placeholder bootstrap secret")
        return self

    @property
    def email(self) -> str | None:
        if self.operator_bootstrap_email is None:
            return None
        return normalize_email(self.operator_bootstrap_email.get_secret_value())


async def run() -> None:
    settings = BootstrapSettings()
    email = settings.email
    secret = settings.operator_bootstrap_secret
    if email is None and secret is None:
        print("operator bootstrap not configured")
        return
    if email is None or secret is None:
        raise RuntimeError("operator bootstrap requires paired email and secret")
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        pool_timeout=settings.database_pool_timeout_seconds,
    )
    store = PostgresStore(engine)
    try:
        service = OnboardingService(
            PostgresOnboardingRepository(store.session_factory),
            settings.auth_pepper.get_secret_value(),
            invitation_ttl_seconds=settings.identity_invitation_ttl_seconds,
        )
        result = await service.bootstrap_operator(email, secret.get_secret_value())
        print(
            "operator bootstrap created" if result.created else "operator bootstrap already applied"
        )
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(run())
