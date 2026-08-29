from __future__ import annotations

import json

import pytest

from backend.common import azure_cosmos_credential


def test_service_principal_is_preferred_over_account_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    credential = object()

    def create_credential(**kwargs: str) -> object:
        captured.update(kwargs)
        return credential

    monkeypatch.setattr(azure_cosmos_credential, "_client_secret_credential", create_credential)

    result = azure_cosmos_credential.cosmos_credential_from_secret_string(
        json.dumps(
            {
                "tenant_id": "tenant",
                "client_id": "client",
                "client_secret": "secret",
                "cosmos_key": "legacy-key",
            }
        )
    )

    assert result is credential
    assert captured == {
        "tenant_id": "tenant",
        "client_id": "client",
        "client_secret": "secret",
    }


def test_account_key_remains_a_migration_fallback() -> None:
    result = azure_cosmos_credential.cosmos_credential_from_secret_string(
        json.dumps({"cosmos_key": "legacy-key"})
    )

    assert result == "legacy-key"


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        json.dumps({}),
        json.dumps({"tenant_id": "tenant", "client_id": "client"}),
    ),
)
def test_invalid_or_incomplete_credentials_are_rejected(payload: str) -> None:
    with pytest.raises(RuntimeError, match="credential"):
        azure_cosmos_credential.cosmos_credential_from_secret_string(payload)
