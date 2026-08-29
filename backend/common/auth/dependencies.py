from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from backend.common.auth.models import AuthContext, TokenVerifier
from backend.common.errors.models import ApiError


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    if authorization is None:
        raise ApiError("AUTH_HEADER_MISSING", "Bearer access token is required", 401)

    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token.strip():
        raise ApiError("AUTH_HEADER_INVALID", "Authorization header must use Bearer authentication", 401)

    verifier: TokenVerifier = request.app.state.auth_verifier
    access_token = token.strip()
    auth = verifier.verify(access_token)
    # Downstream user-scoped Cognito APIs require the original access token.
    request.state.access_token = access_token
    return auth
