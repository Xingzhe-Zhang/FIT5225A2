"""Read-only Cognito federation deployment check.

Never creates or updates resources and never prints secrets, tokens, user
names, or user attributes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def check_provider(
    client: Any,
    *,
    user_pool_id: str,
    app_client_id: str,
    provider: str,
    require_user: bool,
) -> dict[str, object]:
    try:
        identity_provider = client.describe_identity_provider(
            UserPoolId=user_pool_id, ProviderName=provider
        )["IdentityProvider"]
    except client.exceptions.ResourceNotFoundException as exc:
        raise RuntimeError(
            f"Cognito identity provider {provider} is not deployed in user pool "
            f"{user_pool_id}. Run and approve the reviewed Terraform apply first."
        ) from exc
    app_client = client.describe_user_pool_client(
        UserPoolId=user_pool_id, ClientId=app_client_id
    )["UserPoolClient"]

    if provider not in app_client.get("SupportedIdentityProviders", []):
        raise RuntimeError(f"{provider} exists but is not enabled on the Cognito app client")
    if "code" not in app_client.get("AllowedOAuthFlows", []):
        raise RuntimeError("Cognito app client does not enable the authorization-code flow")

    mapping = identity_provider.get("AttributeMapping", {})
    missing = sorted({"email", "given_name", "family_name"} - set(mapping))
    if missing:
        raise RuntimeError(f"{provider} is missing Cognito attribute mappings: {', '.join(missing)}")

    federated_users = 0
    pagination_token: str | None = None
    while True:
        request: dict[str, object] = {"UserPoolId": user_pool_id, "Limit": 60}
        if pagination_token:
            request["PaginationToken"] = pagination_token
        response = client.list_users(**request)
        for user in response.get("Users", []):
            status = str(user.get("UserStatus", ""))
            username = str(user.get("Username", ""))
            if status == "EXTERNAL_PROVIDER" and username.casefold().startswith(f"{provider.casefold()}_"):
                federated_users += 1
        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            break
    if require_user and federated_users == 0:
        raise RuntimeError(f"{provider} is configured, but no federated Cognito user record was found")

    return {
        "provider": provider,
        "provider_type": identity_provider.get("ProviderType"),
        "authorization_code_flow": True,
        "callback_url_count": len(app_client.get("CallbackURLs", [])),
        "logout_url_count": len(app_client.get("LogoutURLs", [])),
        "required_attribute_mappings": True,
        "federated_user_count": federated_users,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployed Cognito external identity provider")
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--app-client-id", required=True)
    parser.add_argument("--provider", choices=("Google", "Microsoft"), default="Google")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--profile")
    parser.add_argument("--require-user", action="store_true")
    args = parser.parse_args()

    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    try:
        summary = check_provider(
            session.client("cognito-idp"),
            user_pool_id=args.user_pool_id,
            app_client_id=args.app_client_id,
            provider=args.provider,
            require_user=args.require_user,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
