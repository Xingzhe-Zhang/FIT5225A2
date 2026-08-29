"""Load an Azure Cosmos credential from an AWS Secrets Manager payload."""

from __future__ import annotations

import json
from typing import Any


def cosmos_credential_from_secret_string(secret_string: object) -> object:
    """Prefer a component service principal and retain account-key fallback."""

    if not isinstance(secret_string, str):
        raise RuntimeError("Cosmos credential secret is invalid")
    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError as error:
        raise RuntimeError("Cosmos credential secret is invalid") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Cosmos credential secret is invalid")

    identity_fields = ("tenant_id", "client_id", "client_secret")
    identity_values = [payload.get(name) for name in identity_fields]
    if all(isinstance(value, str) and value for value in identity_values):
        return _client_secret_credential(
            tenant_id=identity_values[0],
            client_id=identity_values[1],
            client_secret=identity_values[2],
        )
    if any(value is not None for value in identity_values):
        raise RuntimeError("Cosmos service-principal credential is incomplete")

    cosmos_key = payload.get("cosmos_key")
    if isinstance(cosmos_key, str) and cosmos_key:
        return cosmos_key
    raise RuntimeError("Cosmos credential secret is invalid")


def load_cosmos_credential(secret_client: Any, secret_id: str) -> object:
    response = secret_client.get_secret_value(SecretId=secret_id)
    return cosmos_credential_from_secret_string(response.get("SecretString"))


def _client_secret_credential(*, tenant_id: str, client_id: str, client_secret: str) -> object:
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
