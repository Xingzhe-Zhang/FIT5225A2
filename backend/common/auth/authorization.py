from backend.common.auth.models import AuthContext
from backend.common.errors.models import ApiError


def ensure_owner(auth: AuthContext, owner_sub: str) -> None:
    """Reject cross-owner access using identity derived from the verified token."""

    if auth.sub != owner_sub:
        raise ApiError("AUTH_FORBIDDEN", "The resource belongs to another user", 403)
