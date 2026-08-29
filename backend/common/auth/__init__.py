"""Authentication primitives shared by all HTTP adapters."""

from backend.common.auth.models import AuthContext, TokenVerifier

__all__ = ["AuthContext", "TokenVerifier"]
