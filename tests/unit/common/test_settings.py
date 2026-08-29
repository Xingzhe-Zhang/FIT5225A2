from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError


def settings_module():
    return importlib.import_module("backend.common.config.settings")


def base_values() -> dict[str, object]:
    return {
        "app_env": "local",
        "aws_region": "ap-southeast-2",
        "cognito_user_pool_id": "ap-southeast-2_example",
        "cognito_app_client_id": "client-id",
        "cognito_oauth_domain": "https://example.auth.ap-southeast-2.amazoncognito.com",
        "cognito_redirect_uri": "http://localhost:5173/auth/callback",
        "api_base_url": "http://localhost:8000",
        "azure_data_api_base_url": "http://localhost:8001",
    }


def test_local_auth_is_enabled_only_in_local_environment() -> None:
    module = settings_module()
    local = module.AppSettings(**base_values(), local_auth_secret="test-only-secret-32-characters!!")
    assert local.local_auth_enabled is True

    production_values = {**base_values(), "app_env": "production"}
    with pytest.raises(ValidationError):
        module.AppSettings(**production_values, local_auth_secret="must-not-be-accepted-in-prod")


def test_external_providers_are_normalized_and_deduplicated() -> None:
    module = settings_module()
    settings = module.AppSettings(
        **base_values(), external_providers="Google,microsoft,Google"
    )
    assert settings.external_provider_names == ("Google", "Microsoft")


def test_public_auth_config_contains_no_secret() -> None:
    module = settings_module()
    settings = module.AppSettings(**base_values(), local_auth_secret="test-only-secret-32-characters!!")
    public = settings.public_auth_config()
    assert "secret" not in str(public).lower()
    assert public["app_client_id"] == "client-id"


def test_notification_topic_is_loaded_from_external_environment_and_not_exposed(monkeypatch) -> None:
    module = settings_module()
    topic_arn = "arn:aws:sns:ap-southeast-2:123456789012:pba-notifications"
    monkeypatch.setenv("NOTIFICATION_TOPIC", topic_arn)

    settings = module.AppSettings(**base_values(), _env_file=None)

    assert settings.notification_topic == topic_arn
    assert "notification" not in str(settings.public_auth_config()).lower()


def test_notification_topic_rejects_non_sns_values() -> None:
    module = settings_module()

    with pytest.raises(ValidationError):
        module.AppSettings(**base_values(), notification_topic="embedded-secret-or-wrong-resource")
