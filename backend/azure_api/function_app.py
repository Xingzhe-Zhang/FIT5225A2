"""FastAPI composition used by the Azure Functions data API.

The AWS API forwards the Cognito access token to these owner-scoped internal
query endpoints. Cosmos DB is reached with the Function App's managed
identity; no Cosmos key is placed in app settings.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.azure_api.media.cosmos_repository import CosmosPagedMediaRepository
from backend.azure_api.queries.service import QueryService, TrustedThumbnailNormalizer
from backend.common.auth.dependencies import require_auth
from backend.common.auth.jwt import CognitoJwtVerifier, HttpJwksProvider
from backend.common.auth.models import AuthContext
from backend.common.config.settings import AppSettings
from backend.common.contracts.models import MediaRecord, SpeciesQuery, TagQuery, ThumbnailQuery
from backend.common.errors.models import ApiError


@lru_cache(maxsize=1)
def _build_runtime():
    issuer = os.environ["COGNITO_ISSUER"].rstrip("/")
    settings = AppSettings(
        app_env="production",
        aws_region=os.environ["AWS_REGION"],
        cognito_user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
        cognito_app_client_id=os.environ["COGNITO_APP_CLIENT_ID"],
        api_base_url="https://azure-data.invalid",
        azure_data_api_base_url="https://azure-data.invalid",
    )
    from azure.cosmos import CosmosClient  # imported only in the cloud package
    from azure.identity import DefaultAzureCredential

    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=DefaultAzureCredential())
    database = client.get_database_client(os.environ.get("COSMOS_DATABASE", "bioarchive"))
    container = database.get_container_client(os.environ.get("COSMOS_MEDIA_CONTAINER", "media"))
    repository = CosmosPagedMediaRepository(container)
    verifier = CognitoJwtVerifier(
        settings,
        HttpJwksProvider(
            f"{issuer}/.well-known/jwks.json"
        ),
    )
    return repository, verifier


def create_data_api() -> FastAPI:
    # The Function catch-all is intentionally anonymous at the host layer so
    # Cognito bearer tokens can be used. Do not expose discovery endpoints from
    # that catch-all; every business route below applies require_auth itself.
    app = FastAPI(
        title="Pacific BioArchive Azure Data API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content=error.to_response(uuid4()).model_dump(mode="json"),
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            repository, _ = _build_runtime()
            # Exercise the data-plane query permission without reading a real
            # owner's partition. This makes the health endpoint useful during
            # managed-identity/RBAC cutovers instead of merely constructing an
            # SDK client.
            repository.list_for_owner("__azure_healthcheck__")
        except Exception:
            return {"status": "degraded"}
        return {"status": "ok"}

    def repository_and_auth():
        repository, verifier = _build_runtime()
        return repository, verifier

    def query_service() -> QueryService:
        repository, _ = repository_and_auth()
        bucket = os.environ.get("AWS_MEDIA_BUCKET", "")
        region = os.environ.get("AWS_REGION", "ap-southeast-2")
        hosts = (
            {
                f"{bucket}.s3.{region}.amazonaws.com": bucket,
                f"{bucket}.s3.amazonaws.com": bucket,
            }
            if bucket
            else {}
        )
        return QueryService(repository, TrustedThumbnailNormalizer(hosts))

    @app.middleware("http")
    async def attach_runtime(request: Request, call_next):
        if request.url.path.rstrip("/").endswith("/health"):
            return await call_next(request)
        repository, verifier = repository_and_auth()
        request.app.state.repository = repository
        request.app.state.auth_verifier = verifier
        return await call_next(request)

    @app.post("/internal/query/tags", response_model=list[MediaRecord])
    def query_tags(payload: dict[str, int], auth: Annotated[AuthContext, Depends(require_auth)]) -> list[MediaRecord]:
        query = TagQuery.model_validate(payload)
        repository, _ = repository_and_auth()
        return repository.query_by_tags(auth.sub, query.root)

    @app.post("/internal/query/species", response_model=list[MediaRecord])
    def query_species(payload: SpeciesQuery, auth: Annotated[AuthContext, Depends(require_auth)]) -> list[MediaRecord]:
        repository, _ = repository_and_auth()
        return repository.query_by_species(auth.sub, payload.species)

    @app.post("/internal/query/thumbnail", response_model=MediaRecord)
    def query_thumbnail(payload: ThumbnailQuery, auth: Annotated[AuthContext, Depends(require_auth)]) -> MediaRecord:
        return query_service().query_thumbnail(auth.sub, payload)

    @app.get("/internal/media/{media_id}", response_model=MediaRecord)
    def get_media(media_id: str, auth: Annotated[AuthContext, Depends(require_auth)]) -> MediaRecord:
        from uuid import UUID
        repository, _ = repository_and_auth()
        record = repository.get(auth.sub, UUID(media_id))
        if record is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="media not found")
        return record

    return app
