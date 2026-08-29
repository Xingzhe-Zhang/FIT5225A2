"""AWS API authentication adapter exports.

Feature modules should import authentication dependencies from this package and
must use ``AuthContext.sub`` as the owner identity.
"""

from backend.common.auth.dependencies import require_auth
from backend.common.auth.models import AuthContext

__all__ = ["AuthContext", "require_auth"]
