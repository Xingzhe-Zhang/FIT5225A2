from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError
from typing import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from backend.azure_api.queries.service import MediaNotFoundError, ThumbnailUrlError
from backend.common.auth.dependencies import require_auth
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import QueryResponse, SpeciesQuery, TagQuery, ThumbnailQuery
from backend.common.errors.models import ApiError
from backend.temporary_query.service import TemporaryFileValidationError, TemporaryQueryService
from backend.media_processor.videos.processing import VideoProcessingError

from .gateway import QueryGateway, StorageUriError


@dataclass(frozen=True, slots=True)
class QueryDependencies:
    """Query collaborators supplied by the final application composition."""

    gateway: QueryGateway
    temporary_service: TemporaryQueryService


def create_query_dependencies(
    *,
    gateway: QueryGateway,
    temporary_service: TemporaryQueryService,
) -> QueryDependencies:
    return QueryDependencies(gateway=gateway, temporary_service=temporary_service)


def create_query_router(dependencies: QueryDependencies) -> APIRouter:
    router = APIRouter(tags=["queries"])

    @router.post("/queries/tags", response_model=QueryResponse)
    async def query_tags(
        request: Request,
        auth: AuthContext = Depends(require_auth),
    ) -> QueryResponse:
        del auth
        payload = (await _json_payload(request, TagQuery)).root
        return await _run_query(
            lambda: dependencies.gateway.query_tags(_access_token(request), payload)
        )

    @router.post("/queries/species", response_model=QueryResponse)
    async def query_species(
        request: Request,
        auth: AuthContext = Depends(require_auth),
    ) -> QueryResponse:
        del auth
        payload = (await _json_payload(request, SpeciesQuery)).model_dump(mode="json")
        return await _run_query(
            lambda: dependencies.gateway.query_species(_access_token(request), payload)
        )

    @router.post("/queries/thumbnail", response_model=QueryResponse)
    async def query_thumbnail(
        request: Request,
        auth: AuthContext = Depends(require_auth),
    ) -> QueryResponse:
        del auth
        payload = (await _json_payload(request, ThumbnailQuery)).model_dump(mode="json")
        return await _run_query(
            lambda: dependencies.gateway.query_thumbnail(_access_token(request), payload)
        )

    @router.post("/queries/by-file", response_model=QueryResponse)
    async def query_by_file(
        request: Request,
        auth: AuthContext = Depends(require_auth),
    ) -> QueryResponse:
        form = await _multipart_form(request)
        try:
            file = _single_query_file(form)
            data = await file.read()
            return await run_in_threadpool(
                dependencies.temporary_service.query,
                owner_sub=auth.sub,
                request_id=_request_id(request),
                file_name=file.filename or "",
                content_type=file.content_type or "",
                data=data,
            )
        except TemporaryFileValidationError as error:
            raise ApiError("QUERY_FILE_INVALID", str(error), 422) from error
        except VideoProcessingError as error:
            raise ApiError(error.code, str(error), 422) from error
        except ValueError as error:
            raise ApiError("QUERY_VALIDATION_FAILED", str(error), 422) from error
        finally:
            await form.close()

    return router


async def _json_payload(request: Request, model):
    try:
        return model.model_validate(await request.json())
    except (JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
        raise ApiError("QUERY_VALIDATION_FAILED", "Request body must be valid JSON", 422) from error


async def _multipart_form(request: Request) -> FormData:
    content_type = request.headers.get("content-type", "")
    if not content_type.casefold().startswith("multipart/form-data"):
        raise ApiError(
            "QUERY_FILE_INVALID",
            "Request body must use multipart/form-data",
            422,
        )
    try:
        return await request.form()
    except (HTTPException, MultiPartException, ValueError) as error:
        raise ApiError("QUERY_FILE_INVALID", "Multipart request body is malformed", 422) from error


def _single_query_file(form: FormData) -> UploadFile:
    fields = list(form.multi_items())
    if (
        len(fields) != 1
        or fields[0][0] != "file"
        or not isinstance(fields[0][1], UploadFile)
    ):
        raise ApiError(
            "QUERY_FILE_INVALID",
            "Multipart body must contain exactly one file field",
            422,
        )
    return fields[0][1]


def _access_token(request: Request) -> str:
    _, _, token = request.headers["authorization"].partition(" ")
    return token.strip()


def _request_id(request: Request) -> UUID:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, UUID) else uuid4()


async def _run_query(operation: Callable[[], QueryResponse]) -> QueryResponse:
    try:
        return await run_in_threadpool(operation)
    except MediaNotFoundError as error:
        raise ApiError("QUERY_NOT_FOUND", str(error), 404) from error
    except (ThumbnailUrlError, StorageUriError, ValidationError, ValueError) as error:
        raise ApiError("QUERY_VALIDATION_FAILED", str(error), 422) from error
