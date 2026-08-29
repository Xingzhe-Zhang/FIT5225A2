from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Any, Mapping, Protocol

import httpx
import jwt

from backend.common.auth.models import AuthContext
from backend.common.config.settings import AppSettings
from backend.common.errors.models import ApiError


class JwksProvider(Protocol):
    def get_jwk(self, kid: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class StaticJwksProvider:
    keys: Mapping[str, Mapping[str, Any]]

    def get_jwk(self, kid: str) -> Mapping[str, Any]:
        try:
            return self.keys[kid]
        except KeyError as exc:
            raise ApiError("AUTH_UNKNOWN_KEY", "Token signing key is not trusted", 401) from exc


@dataclass(slots=True)
class HttpJwksProvider:
    url: str
    timeout_seconds: float = 5.0
    cache_seconds: float = 300.0
    _keys: dict[str, Mapping[str, Any]] = field(default_factory=dict, init=False)
    _loaded_at: float = field(default=0.0, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def _refresh(self) -> None:
        try:
            response = httpx.get(self.url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            keys = payload.get("keys", [])
            self._keys = {
                str(key["kid"]): key
                for key in keys
                if isinstance(key, dict) and isinstance(key.get("kid"), str)
            }
            self._loaded_at = monotonic()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ApiError("AUTH_JWKS_UNAVAILABLE", "Token key service is unavailable", 503) from exc

    def get_jwk(self, kid: str) -> Mapping[str, Any]:
        with self._lock:
            if not self._keys or monotonic() - self._loaded_at >= self.cache_seconds:
                self._refresh()
            key = self._keys.get(kid)
            if key is None:
                # Rotate once immediately before rejecting an unfamiliar key.
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise ApiError("AUTH_UNKNOWN_KEY", "Token signing key is not trusted", 401)
            return key


def _claims_to_context(claims: Mapping[str, Any]) -> AuthContext:
    groups = claims.get("cognito:groups", ())
    if isinstance(groups, str):
        groups = (groups,)
    elif not isinstance(groups, (list, tuple)):
        groups = ()

    raw_scope = claims.get("scope", "")
    scopes = tuple(part for part in raw_scope.split() if part) if isinstance(raw_scope, str) else ()
    return AuthContext(
        sub=str(claims["sub"]),
        username=claims.get("username") or claims.get("cognito:username"),
        email=claims.get("email"),
        groups=tuple(str(group) for group in groups),
        scopes=scopes,
        token_use="access",
    )


@dataclass(frozen=True, slots=True)
class CognitoJwtVerifier:
    settings: AppSettings
    jwks_provider: JwksProvider

    @property
    def issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.settings.aws_region}.amazonaws.com/"
            f"{self.settings.cognito_user_pool_id}"
        )

    def verify(self, token: str) -> AuthContext:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ApiError("AUTH_TOKEN_INVALID", "Token header is invalid", 401)
            jwk = self.jwks_provider.get_jwk(header["kid"])
            key = jwt.PyJWK.from_dict(dict(jwk), algorithm="RS256").key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={"verify_aud": False, "require": ["exp", "iat", "iss", "sub", "token_use"]},
            )
        except ApiError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise ApiError("AUTH_TOKEN_EXPIRED", "Access token has expired", 401) from exc
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401) from exc

        if claims.get("client_id") != self.settings.cognito_app_client_id:
            raise ApiError("AUTH_WRONG_CLIENT", "Token was issued for another client", 401)
        if claims.get("token_use") != "access":
            raise ApiError("AUTH_WRONG_TOKEN_USE", "An access token is required", 401)
        return _claims_to_context(claims)


@dataclass(frozen=True, slots=True)
class LocalJwtVerifier:
    settings: AppSettings

    def __post_init__(self) -> None:
        if not self.settings.local_auth_enabled:
            raise ApiError("LOCAL_AUTH_DISABLED", "Local authentication is disabled", 500)

    def verify(self, token: str) -> AuthContext:
        secret = self.settings.local_auth_secret
        if secret is None:  # Defensive: __post_init__ already enforces this.
            raise ApiError("LOCAL_AUTH_DISABLED", "Local authentication is disabled", 500)
        try:
            claims = jwt.decode(
                token,
                secret.get_secret_value(),
                algorithms=["HS256"],
                issuer="pacific-bioarchive-local",
                audience=self.settings.cognito_app_client_id,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ApiError("AUTH_TOKEN_EXPIRED", "Access token has expired", 401) from exc
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401) from exc

        if claims.get("token_use") != "access":
            raise ApiError("AUTH_WRONG_TOKEN_USE", "An access token is required", 401)
        return _claims_to_context(claims)
