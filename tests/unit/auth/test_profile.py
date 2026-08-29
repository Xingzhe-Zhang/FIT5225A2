from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

from backend.common.config.settings import AppSettings


def settings() -> AppSettings:
    return AppSettings(
        app_env="local",
        aws_region="ap-southeast-2",
        cognito_user_pool_id="local-pool",
        cognito_app_client_id="local-client",
        cognito_oauth_domain="https://local.invalid",
        cognito_redirect_uri="http://localhost:5173/auth/callback",
        api_base_url="http://localhost:8000",
        local_auth_secret="test-profile-secret-32-characters!!",
    )


def token(config: AppSettings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "profile-user",
            "iss": "pacific-bioarchive-local",
            "aud": config.cognito_app_client_id,
            "token_use": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        config.local_auth_secret.get_secret_value(),
        algorithm="HS256",
    )


def test_profile_is_required_at_business_layer_and_can_be_completed() -> None:
    config = settings()
    client = TestClient(importlib.import_module("backend.aws_api.app").create_app(config))
    auth = {"Authorization": f"Bearer {token(config)}"}

    assert client.get("/profile").status_code == 401
    assert client.get("/profile", headers=auth).json() == {
        "given_name": None,
        "family_name": None,
        "complete": False,
    }

    invalid = client.put("/profile", headers=auth, json={"given_name": " ", "family_name": "Lee"})
    assert invalid.status_code == 422

    updated = client.put(
        "/profile",
        headers=auth,
        json={"given_name": "Kai", "family_name": "Lee"},
    )
    assert updated.status_code == 200
    assert updated.json() == {"given_name": "Kai", "family_name": "Lee", "complete": True}
    assert client.get("/profile", headers=auth).json()["complete"] is True
