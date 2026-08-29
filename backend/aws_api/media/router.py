from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.common.auth.dependencies import require_auth
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import QueryResponse

from .service import MediaLibraryService


def create_media_router(service: MediaLibraryService) -> APIRouter:
    router = APIRouter(tags=["media"])

    @router.get("/media", response_model=QueryResponse)
    def list_media(auth: AuthContext = Depends(require_auth)) -> QueryResponse:
        return service.list_for_owner(auth.sub)

    return router
