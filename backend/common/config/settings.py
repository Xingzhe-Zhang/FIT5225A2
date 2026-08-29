from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SnsTopicArn = Annotated[
    str,
    StringConstraints(pattern=r"^arn:(aws|aws-us-gov|aws-cn):sns:[a-z0-9-]+:\d{12}:[A-Za-z0-9_-]{1,256}$"),
]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local", "test", "development", "production"] = "local"
    aws_region: str = "ap-southeast-2"
    cognito_user_pool_id: str = "local-user-pool"
    cognito_app_client_id: str = "local-client"
    cognito_oauth_domain: AnyHttpUrl = "https://local.invalid"
    cognito_redirect_uri: AnyHttpUrl = "http://localhost:5173/auth/callback"
    api_base_url: AnyHttpUrl = "http://localhost:8000"
    azure_data_api_base_url: AnyHttpUrl = "http://localhost:8001"
    external_providers: str = ""
    cors_origins: str = "http://localhost:5173"
    notification_topic: SnsTopicArn | None = None
    local_auth_secret: SecretStr | None = Field(default=None, min_length=24)
    local_ml_model_dir: Path | None = None
    local_ml_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    local_ml_detection_threshold: float = Field(default=0.05, ge=0, le=1)
    local_ml_classification_threshold: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def restrict_local_secret(self) -> "AppSettings":
        if self.local_auth_secret is not None and self.app_env not in {"local", "test"}:
            raise ValueError("local authentication is forbidden outside local/test")
        return self

    @property
    def local_auth_enabled(self) -> bool:
        return self.app_env in {"local", "test"} and self.local_auth_secret is not None

    @property
    def external_provider_names(self) -> tuple[str, ...]:
        normalized: list[str] = []
        aliases = {"google": "Google", "microsoft": "Microsoft", "outlook": "Microsoft"}
        for raw_name in self.external_providers.split(","):
            canonical = aliases.get(raw_name.strip().lower())
            if canonical and canonical not in normalized:
                normalized.append(canonical)
        return tuple(normalized)

    @property
    def allowed_cors_origins(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()))

    def public_auth_config(self) -> dict[str, object]:
        return {
            "region": self.aws_region,
            "user_pool_id": self.cognito_user_pool_id,
            "app_client_id": self.cognito_app_client_id,
            "oauth_domain": str(self.cognito_oauth_domain).rstrip("/"),
            "redirect_uri": str(self.cognito_redirect_uri),
            "external_providers": list(self.external_provider_names),
            "local_auth_enabled": self.local_auth_enabled,
        }
