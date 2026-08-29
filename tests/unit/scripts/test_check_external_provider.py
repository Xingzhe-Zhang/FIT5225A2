from __future__ import annotations

from scripts.check_external_provider import check_provider


class CognitoClient:
    def describe_identity_provider(self, **_kwargs):
        return {"IdentityProvider": {"ProviderType": "Google", "AttributeMapping": {
            "email": "email", "given_name": "given_name", "family_name": "family_name",
        }}}

    def describe_user_pool_client(self, **_kwargs):
        return {"UserPoolClient": {
            "SupportedIdentityProviders": ["COGNITO", "Google"],
            "AllowedOAuthFlows": ["code"],
            "CallbackURLs": ["http://localhost:5173/auth/callback"],
            "LogoutURLs": ["http://localhost:5173"],
        }}

    def list_users(self, **_kwargs):
        return {"Users": [{"Username": "Google_opaque", "UserStatus": "EXTERNAL_PROVIDER"}]}


def test_read_only_check_requires_real_app_client_wiring_and_federated_record() -> None:
    result = check_provider(
        CognitoClient(),
        user_pool_id="ap-southeast-2_example",
        app_client_id="client",
        provider="Google",
        require_user=True,
    )
    assert result["authorization_code_flow"] is True
    assert result["required_attribute_mappings"] is True
    assert result["federated_user_count"] == 1
