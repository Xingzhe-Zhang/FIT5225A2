from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.common.config.settings import AppSettings
from backend.common.auth.models import AuthContext
from backend.common.errors.models import ApiError


COMPOSED_BUSINESS_ROUTES = (
    ("post", "/uploads/reservations"),
    ("delete", "/uploads/reservations/11111111-1111-4111-8111-111111111111"),
    ("post", "/queries/tags"),
    ("post", "/queries/species"),
    ("post", "/queries/thumbnail"),
    ("post", "/queries/by-file"),
    ("post", "/media/tags"),
    ("delete", "/media"),
    ("post", "/subscriptions"),
)

ALL_BUSINESS_ROUTES = (
    ("post", "/uploads/reservations"),
    ("delete", "/uploads/reservations/11111111-1111-4111-8111-111111111111"),
    ("post", "/queries/tags"),
    ("post", "/queries/species"),
    ("post", "/queries/thumbnail"),
    ("post", "/queries/by-file"),
    ("get", "/media"),
    ("post", "/media/tags"),
    ("delete", "/media"),
    ("delete", "/media/11111111-1111-4111-8111-111111111111"),
    ("get", "/subscriptions"),
    ("post", "/subscriptions"),
    ("put", "/subscriptions/11111111-1111-4111-8111-111111111111"),
    ("delete", "/subscriptions/11111111-1111-4111-8111-111111111111"),
    ("get", "/profile"),
    ("put", "/profile"),
)


def app_module():
    return importlib.import_module("backend.aws_api.app")


def settings() -> AppSettings:
    return AppSettings(
        app_env="local",
        aws_region="ap-southeast-2",
        cognito_user_pool_id="ap-southeast-2_example",
        cognito_app_client_id="client-id",
        cognito_oauth_domain="https://example.auth.ap-southeast-2.amazoncognito.com",
        cognito_redirect_uri="http://localhost:5173/auth/callback",
        api_base_url="http://localhost:8000",
        azure_data_api_base_url="http://localhost:8001",
        external_providers="Google,Microsoft",
        local_auth_secret="test-only-secret-32-characters!!",
    )


def local_token(config: AppSettings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "local-user",
            "email": "local@example.test",
            "iss": "pacific-bioarchive-local",
            "aud": config.cognito_app_client_id,
            "token_use": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        config.local_auth_secret.get_secret_value(),
        algorithm="HS256",
    )


def test_public_routes_and_authenticated_context() -> None:
    config = settings()
    client = TestClient(app_module().create_app(config))

    assert client.get("/health").json() == {"status": "ok"}
    public_config = client.get("/auth/config")
    assert public_config.status_code == 200
    assert public_config.json()["external_providers"] == ["Google", "Microsoft"]
    assert "secret" not in public_config.text.lower()

    unauthenticated = client.get("/protected/ping")
    assert unauthenticated.status_code == 401

    authenticated = client.get(
        "/protected/ping",
        headers={"Authorization": f"Bearer {local_token(config)}"},
    )
    assert authenticated.status_code == 200
    assert authenticated.json() == {"owner_sub": "local-user"}


def test_local_token_entry_point_supports_credential_free_development() -> None:
    client = TestClient(app_module().create_app(settings()))

    issued = client.post("/auth/local-token")
    assert issued.status_code == 200
    token = issued.json()["access_token"]
    authenticated = client.get("/protected/ping", headers={"Authorization": f"Bearer {token}"})
    assert authenticated.json() == {"owner_sub": "local-developer"}


def test_local_frontend_origin_is_allowed_without_wildcard_cors() -> None:
    client = TestClient(app_module().create_app(settings()))
    response = client.options(
        "/auth/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers.get("access-control-allow-credentials") != "true"


def test_local_upload_preflight_allows_signed_checksum_header() -> None:
    client = TestClient(app_module().create_app(settings()))
    response = client.options(
        "/_local/objects/originals/test/object.jpg",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,x-amz-meta-sha256",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-amz-meta-sha256" in response.headers["access-control-allow-headers"].lower()


def test_unauthorized_error_matches_contract() -> None:
    client = TestClient(app_module().create_app(settings()))
    response = client.get("/protected/ping", headers={"X-Request-ID": "11111111-1111-4111-8111-111111111111"})
    schema = app_module().load_error_schema()

    assert response.status_code == 401
    Draft202012Validator(schema).validate(response.json())
    assert response.json()["error"]["request_id"] == "11111111-1111-4111-8111-111111111111"


def test_every_business_route_is_protected_and_composed() -> None:
    config = settings()
    client = TestClient(app_module().create_app(config))
    auth_header = {"Authorization": f"Bearer {local_token(config)}"}

    for method, route in COMPOSED_BUSINESS_ROUTES:
        unauthenticated = client.request(method, route)
        assert unauthenticated.status_code == 401, route

        authenticated = client.request(method, route, headers=auth_header)
        assert authenticated.status_code != 501, route
        if authenticated.headers.get("content-type", "").startswith("application/json"):
            payload = authenticated.json()
            assert payload.get("error", {}).get("code") != "NOT_IMPLEMENTED_IN_THIS_MODULE", route


@pytest.mark.parametrize(("method", "route"), ALL_BUSINESS_ROUTES)
def test_every_exposed_business_route_rejects_anonymous_requests(method: str, route: str) -> None:
    client = TestClient(app_module().create_app(settings()))

    response = client.request(method, route)

    assert response.status_code == 401, route
    assert response.json()["error"]["code"] == "AUTH_HEADER_MISSING", route


def test_malformed_authorization_header_is_rejected() -> None:
    client = TestClient(app_module().create_app(settings()))
    response = client.get("/protected/ping", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_HEADER_INVALID"


def test_owner_authorization_returns_typed_403() -> None:
    authorization = importlib.import_module("backend.common.auth.authorization")
    auth = AuthContext(sub="owner-a")

    authorization.ensure_owner(auth, "owner-a")
    with pytest.raises(ApiError) as raised:
        authorization.ensure_owner(auth, "owner-b")

    assert raised.value.status_code == 403
    assert raised.value.code == "AUTH_FORBIDDEN"
