from __future__ import annotations

import base64
import importlib
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.common.config.settings import AppSettings
from backend.common.errors.models import ApiError


def jwt_module():
    return importlib.import_module("backend.common.auth.jwt")


def encode_int(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def rsa_fixture() -> tuple[object, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "use": "sig",
        "alg": "RS256",
        "n": encode_int(numbers.n),
        "e": encode_int(numbers.e),
    }
    return private_key, jwk


def settings(app_env: str = "test", local_secret: str | None = None) -> AppSettings:
    return AppSettings(
        app_env=app_env,
        aws_region="ap-southeast-2",
        cognito_user_pool_id="ap-southeast-2_example",
        cognito_app_client_id="client-id",
        cognito_oauth_domain="https://example.auth.ap-southeast-2.amazoncognito.com",
        cognito_redirect_uri="http://localhost:5173/auth/callback",
        api_base_url="http://localhost:8000",
        azure_data_api_base_url="http://localhost:8001",
        local_auth_secret=local_secret,
    )


def make_cognito_token(
    private_key: object,
    *,
    client_id: str = "client-id",
    token_use: str = "access",
    expires_delta: timedelta = timedelta(minutes=5),
    kid: str = "test-key",
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "user-123",
        "username": "student@example.com",
        "email": "student@example.com",
        "iss": "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_example",
        "client_id": client_id,
        "token_use": token_use,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "scope": "openid email",
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def test_cognito_verifier_accepts_valid_access_token() -> None:
    module = jwt_module()
    private_key, jwk = rsa_fixture()
    verifier = module.CognitoJwtVerifier(settings(), module.StaticJwksProvider({"test-key": jwk}))

    context = verifier.verify(make_cognito_token(private_key))

    assert context.sub == "user-123"
    assert context.email == "student@example.com"
    assert context.token_use == "access"


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"client_id": "wrong-client"}, "AUTH_WRONG_CLIENT"),
        ({"token_use": "id"}, "AUTH_WRONG_TOKEN_USE"),
        ({"expires_delta": timedelta(seconds=-1)}, "AUTH_TOKEN_EXPIRED"),
        ({"kid": "unknown"}, "AUTH_UNKNOWN_KEY"),
    ],
)
def test_cognito_verifier_rejects_invalid_tokens(changes: dict[str, object], expected_code: str) -> None:
    module = jwt_module()
    private_key, jwk = rsa_fixture()
    verifier = module.CognitoJwtVerifier(settings(), module.StaticJwksProvider({"test-key": jwk}))

    with pytest.raises(ApiError) as raised:
        verifier.verify(make_cognito_token(private_key, **changes))

    assert raised.value.code == expected_code
    assert raised.value.status_code == 401


def test_local_verifier_works_only_with_explicit_local_settings() -> None:
    module = jwt_module()
    local_settings = settings("local", "test-only-secret-32-characters!!")
    verifier = module.LocalJwtVerifier(local_settings)
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "local-user",
            "email": "local@example.test",
            "iss": "pacific-bioarchive-local",
            "aud": "client-id",
            "token_use": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        local_settings.local_auth_secret.get_secret_value(),
        algorithm="HS256",
    )

    assert verifier.verify(token).sub == "local-user"

    with pytest.raises(ApiError) as raised:
        module.LocalJwtVerifier(settings("development"))
    assert raised.value.code == "LOCAL_AUTH_DISABLED"
