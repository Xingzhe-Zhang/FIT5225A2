from __future__ import annotations

import pytest

from backend.azure_api.function_app import _query_thumbnail_for_owner
from backend.azure_api.queries.service import MediaNotFoundError, ThumbnailUrlError
from backend.common.contracts.models import ThumbnailQuery
from backend.common.errors.models import ApiError


class _FailingThumbnailService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def query_thumbnail(self, owner_sub: str, payload: object):
        del owner_sub, payload
        raise self._error


@pytest.mark.parametrize(
    ("failure", "code", "status"),
    [
        (MediaNotFoundError("thumbnail was not found"), "QUERY_NOT_FOUND", 404),
        (ThumbnailUrlError("thumbnail URL is invalid"), "QUERY_VALIDATION_FAILED", 422),
    ],
)
def test_azure_thumbnail_boundary_returns_structured_query_errors(
    failure: Exception,
    code: str,
    status: int,
) -> None:
    payload = ThumbnailQuery(
        thumbnail_url="https://media.example.test/derived/1/thumbnail.jpg"
    )

    with pytest.raises(ApiError) as captured:
        _query_thumbnail_for_owner(  # type: ignore[arg-type]
            _FailingThumbnailService(failure),
            "owner",
            payload,
        )

    assert captured.value.code == code
    assert captured.value.status_code == status
