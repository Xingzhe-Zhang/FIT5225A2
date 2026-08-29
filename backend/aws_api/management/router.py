from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from backend.aws_api.dependencies import FeatureDependencies
from backend.common.auth.dependencies import require_auth
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import BulkTagOperation, DeleteRequest
from backend.common.errors.models import ApiError


def create_management_router(dependencies: FeatureDependencies) -> APIRouter:
    router = APIRouter(tags=["management"])

    @router.post("/media/tags")
    def update_tags(
        request: BulkTagOperation,
        auth: AuthContext = Depends(require_auth),
    ) -> dict[str, list[dict[str, Any]]]:
        try:
            outcomes = dependencies.bulk_tags.update(
                owner_sub=auth.sub,
                urls=[str(url) for url in request.urls],
                tags=request.tags,
                operation=request.operation,
            )
        except ValueError as error:
            raise ApiError("MEDIA_TAG_REQUEST_INVALID", str(error), 422) from error
        return {"results": [_tag_outcome(outcome) for outcome in outcomes]}

    @router.delete("/media")
    def delete_media(
        request: DeleteRequest,
        auth: AuthContext = Depends(require_auth),
    ) -> dict[str, list[dict[str, Any]]]:
        try:
            outcomes = dependencies.deletion.delete(
                owner_sub=auth.sub,
                urls=[str(url) for url in request.urls],
            )
        except ValueError as error:
            raise ApiError("MEDIA_DELETE_REQUEST_INVALID", str(error), 422) from error
        return {"results": [_delete_outcome(outcome) for outcome in outcomes]}

    @router.delete("/media/{media_id}")
    def delete_media_by_id(
        media_id: UUID,
        auth: AuthContext = Depends(require_auth),
    ) -> dict[str, dict[str, Any]]:
        try:
            outcome = dependencies.deletion.delete_by_id(
                owner_sub=auth.sub,
                media_id=media_id,
            )
        except ValueError as error:
            raise ApiError("MEDIA_DELETE_REQUEST_INVALID", str(error), 422) from error
        return {"result": _delete_outcome(outcome)}

    return router


def _tag_outcome(outcome: object) -> dict[str, Any]:
    return {
        "url": outcome.url,
        "media_id": str(outcome.media_id) if outcome.media_id else None,
        "status": outcome.status,
    }


def _delete_outcome(outcome: object) -> dict[str, Any]:
    return {
        "url": outcome.url,
        "media_id": str(outcome.media_id) if outcome.media_id else None,
        "operation_id": str(outcome.operation_id) if outcome.operation_id else None,
        "status": outcome.status,
        "error": outcome.error,
    }
