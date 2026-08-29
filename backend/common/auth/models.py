from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class AuthContext(BaseModel):
    """Normalized identity passed to business handlers after token validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sub: str
    username: str | None = None
    email: str | None = None
    groups: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    token_use: Literal["access"] = "access"


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AuthContext: ...
