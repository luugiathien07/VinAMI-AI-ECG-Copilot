"""Typed runtime configuration for the VinAMI ACS scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vinami.shared.schema import EXPECTED_ALEMBIC_HEAD


class Settings(BaseSettings):
    """Read platform configuration from environment variables or a local `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="VINAMI_", extra="ignore")

    app_name: str = "VinAMI ACS Operating System"
    app_environment: str = "development"
    database_url: str = Field(
        default="postgresql+psycopg://vinami:vinami@localhost:5432/vinami_acs"
    )
    redis_url: str = "redis://localhost:6379/0"
    dashboard_api_base_url: str = "http://api:8000"
    log_level: str = "INFO"
    identity_mode: Literal["development", "oidc"] | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_allowed_algorithms: str = "RS256"
    oidc_role_claim: str = "roles"
    oidc_permission_claim: str = "permissions"
    event_max_retries: int = Field(default=3, ge=1)
    event_retry_delay_seconds: float = Field(default=1.0, ge=0.0)
    event_claim_ttl_seconds: float = Field(default=60.0, gt=0.0)
    event_dispatch_interval_seconds: float = Field(default=1.0, gt=0.0)
    expected_schema_revision: str = EXPECTED_ALEMBIC_HEAD
    ecg_artifact_root: Path = Path("/tmp/vinami-ecg-artifacts")
    ecg_artifact_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    ecg_provider: str = "mock"
    ecg_allow_mock_provider: bool = True
    ecg_checkpoint_path: Path | None = None
    ecg_checkpoint_sha256: str | None = None
    ecg_device: str = "cpu"
    ecg_provider_timeout_seconds: float = Field(default=30.0, gt=0.0)
    ecg_allow_provider_fallback: bool = False
    ecg_fallback_provider: str = "mock"
    ecg_showcase_manifest_path: Path | None = None
    ecg_showcase_artifact_root: Path | None = None
    ecg_stemi_checkpoint_path: Path | None = None
    ecg_stemi_checkpoint_sha256: str | None = None

    @model_validator(mode="after")
    def validate_identity_configuration(self) -> Settings:
        """Reject unsafe production development mode and incomplete OIDC configuration."""
        if (
            self.app_environment.lower() in {"production", "prod"}
            and self.identity_mode == "development"
        ):
            raise ValueError("development identity mode is prohibited in production")
        if self.identity_mode == "oidc":
            required = {
                "VINAMI_OIDC_ISSUER": self.oidc_issuer,
                "VINAMI_OIDC_AUDIENCE": self.oidc_audience,
                "VINAMI_OIDC_JWKS_URL": self.oidc_jwks_url,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"OIDC identity configuration is incomplete: {', '.join(missing)}")
        if not self.oidc_algorithms:
            raise ValueError("at least one signed OIDC algorithm must be configured")
        return self

    @property
    def oidc_algorithms(self) -> tuple[str, ...]:
        """Normalize the comma-separated allow-list used by the OIDC adapter."""
        return tuple(
            algorithm.strip()
            for algorithm in self.oidc_allowed_algorithms.split(",")
            if algorithm.strip() and algorithm.strip().lower() != "none"
        )
