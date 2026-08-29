from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.aws_api.dependencies import FeatureDependencies, build_feature_dependencies, build_sns_feature_dependencies
from backend.aws_api.management.router import create_management_router
from backend.aws_api.profile.router import InMemoryProfileClient, S3ProfileClient, create_profile_router
from backend.aws_api.media import MediaLibraryService
from backend.aws_api.media.local_objects import LocalObjectUrlSigner, create_local_object_router
from backend.aws_api.media.local_processing import LocalMediaProcessingService
from backend.aws_api.media.s3_storage import S3Storage
from backend.aws_api.media.s3_urls import S3SignedUrlNormalizer
from backend.aws_api.media.router import create_media_router
from backend.aws_api.queries.gateway import QueryGateway
from backend.aws_api.queries.azure_client import AzureDataApiClient
from backend.aws_api.queries.temporary_worker import WorkerTemporaryInferenceService
from backend.aws_api.queries.router import QueryDependencies, create_query_dependencies, create_query_router
from backend.aws_api.subscriptions.router import create_subscription_router
from backend.aws_api.uploads.router import create_upload_router
from backend.aws_api.uploads.s3 import S3ObjectUrlSigner
from backend.aws_api.uploads.service import UploadReservationService
from backend.azure_api.management.service import InvalidSignedUrl
from backend.azure_api.media.repository import InMemoryPagedMediaRepository
from backend.azure_api.media.cosmos_repository import CosmosPagedMediaRepository
from backend.azure_api.subscriptions.cosmos_repository import CosmosSubscriptionRepository
from backend.azure_api.operations.cosmos import CosmosDeliveryLedger, CosmosDeletionOperationStore
from backend.azure_api.queries.service import QueryService
from backend.azure_api.subscriptions.repository import InMemorySubscriptionRepository
from backend.common.auth.dependencies import require_auth
from backend.common.auth.jwt import CognitoJwtVerifier, HttpJwksProvider, LocalJwtVerifier
from backend.common.auth.models import AuthContext, TokenVerifier
from backend.common.azure_cosmos_credential import load_cosmos_credential
from backend.common.config.settings import AppSettings
from backend.common.errors.models import ApiError
from backend.common.media_limits import (
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    MAX_VIDEO_DURATION_SECONDS,
    MAX_VIDEO_FRAMES,
    VIDEO_PROCESSING_TIMEOUT_SECONDS,
)
from backend.common.providers.fakes import FixedClock, InMemoryObjectStorage, RecordingNotifier
from backend.media_processor.images.thumbnail import PillowThumbnailer, ThumbnailConfig
from backend.media_processor.videos import FfmpegVideoBackend
from backend.media_processor.videos.processing import VideoLimits, VideoProcessor
from backend.tagging.inference.local_runtime import LocalWildlifeInferenceService
from backend.temporary_query.service import TemporaryQueryService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BUCKET = "pba-local-media"
LOCAL_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _video_limits() -> VideoLimits:
    return VideoLimits(
        max_input_bytes=MAX_VIDEO_BYTES,
        max_duration_seconds=MAX_VIDEO_DURATION_SECONDS,
        max_frames=MAX_VIDEO_FRAMES,
        timeout_seconds=VIDEO_PROCESSING_TIMEOUT_SECONDS,
        supported_containers=("mp4", "mov"),
        supported_codecs=("h264", "hevc"),
    )


def load_error_schema() -> dict[str, Any]:
    schema_path = PROJECT_ROOT / "contracts" / "schemas" / "error-response.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _request_id(raw_value: str | None) -> UUID:
    if raw_value:
        try:
            return UUID(raw_value)
        except ValueError:
            pass
    return uuid4()


def _default_verifier(settings: AppSettings) -> TokenVerifier:
    if settings.local_auth_enabled:
        return LocalJwtVerifier(settings)
    issuer = f"https://cognito-idp.{settings.aws_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    return CognitoJwtVerifier(settings, HttpJwksProvider(f"{issuer}/.well-known/jwks.json"))


class _DeterministicIds:
    def __init__(self) -> None:
        self._next = 1

    def new_uuid(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class _LocalObjectUrlNormalizer:
    """Accept only exact local-object URLs for owner-scoped local services."""

    def __init__(self, *, base_url: str, bucket_name: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("local API base URL must be absolute HTTP(S)")
        self._scheme = parsed.scheme
        self._host = parsed.netloc.casefold()
        self._prefix = f"{parsed.path.rstrip('/')}/_local/objects/"
        self._bucket_name = bucket_name

    def canonical_storage_uri(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != self._scheme or parsed.netloc.casefold() != self._host:
            raise InvalidSignedUrl("URL was not issued by the configured local object endpoint")
        path = unquote(parsed.path)
        if not path.startswith(self._prefix):
            raise InvalidSignedUrl("URL path is outside the local object endpoint")
        key = path[len(self._prefix) :]
        if not key or any(segment in {"", ".", ".."} for segment in key.split("/")):
            raise InvalidSignedUrl("URL does not contain a safe object key")
        return f"s3://{self._bucket_name}/{key}"

    def normalize(self, url: str) -> str:
        uri = self.canonical_storage_uri(url)
        if not uri.split("/", 3)[3].startswith("derived/"):
            raise InvalidSignedUrl("URL does not identify a local thumbnail object")
        return uri


class _LocalQueryClient:
    def __init__(self, service: QueryService, verifier: TokenVerifier) -> None:
        self._service = service
        self._verifier = verifier

    def query_tags(self, access_token: str, payload: object):
        return self._service.query_tags(self._owner(access_token), payload)

    def query_species(self, access_token: str, payload: object):
        return self._service.query_species(self._owner(access_token), payload)

    def query_thumbnail(self, access_token: str, payload: object):
        return self._service.query_thumbnail(self._owner(access_token), payload)

    def _owner(self, access_token: str) -> str:
        return self._verifier.verify(access_token).sub


class _UnavailableInferenceService:
    def infer(self, storage_uris: list[str]):
        del storage_uris
        raise ValueError("Local model inference is not configured")


class _UtcClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class _UuidIds:
    def new_uuid(self) -> UUID:
        return uuid4()


class _UnavailableTemporaryQueryService:
    def query(self, **kwargs):
        del kwargs
        raise ApiError(
            "TEMPORARY_QUERY_UNAVAILABLE",
            "Temporary ML query is not configured for the cloud API",
            501,
        )


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    upload_service: UploadReservationService
    media_service: MediaLibraryService
    query_dependencies: QueryDependencies
    feature_dependencies: FeatureDependencies
    local_object_router: APIRouter
    profile_client: object


def build_local_dependencies(settings: AppSettings, verifier: TokenVerifier) -> ApplicationDependencies:
    """Build an isolated local/test composition without cloud adapters or credentials."""

    clock = FixedClock(LOCAL_TIME)
    ids = _DeterministicIds()
    repository = InMemoryPagedMediaRepository()
    storage = InMemoryObjectStorage()
    subscriptions = InMemorySubscriptionRepository()
    base_url = str(settings.api_base_url).rstrip("/")
    seed = settings.local_auth_secret.get_secret_value() if settings.local_auth_secret else "pba-local-capability"
    signer = LocalObjectUrlSigner(
        base_url=base_url,
        secret=hashlib.sha256(seed.encode("utf-8")).digest(),
        clock=clock,
    )
    normalizer = _LocalObjectUrlNormalizer(base_url=base_url, bucket_name=LOCAL_BUCKET)
    inference = (
        LocalWildlifeInferenceService(
            storage=storage,
            model_dir=settings.local_ml_model_dir,
            device=settings.local_ml_device,
            detection_threshold=settings.local_ml_detection_threshold,
            classification_threshold=settings.local_ml_classification_threshold,
        )
        if settings.local_ml_model_dir is not None
        else _UnavailableInferenceService()
    )
    features = build_feature_dependencies(
        media_repository=repository,
        storage=storage,
        subscription_repository=subscriptions,
        notifier=RecordingNotifier(),
        clock=clock,
        ids=ids,
        download_base_url="https://local.invalid",
        bucket_name=LOCAL_BUCKET,
        application_base_url="https://local.invalid",
        url_normalizer=normalizer,
    )
    query_service = QueryService(repository, normalizer)
    gateway = QueryGateway(
        client=_LocalQueryClient(query_service, verifier),
        signer=signer,
        storage_bucket=LOCAL_BUCKET,
    )
    temporary_service = TemporaryQueryService(
        storage=storage,
        repository=repository,
        inference=inference,
        video_processor=VideoProcessor(
            FfmpegVideoBackend(),
            _video_limits(),
        ),
        signer=signer,
        bucket_name=LOCAL_BUCKET,
        max_bytes=MAX_IMAGE_BYTES,
    )
    processing = LocalMediaProcessingService(
        bucket_name=LOCAL_BUCKET,
        repository=repository,
        storage=storage,
        thumbnailer=PillowThumbnailer(ThumbnailConfig()),
        video_processor=VideoProcessor(
            FfmpegVideoBackend(),
            _video_limits(),
        ),
        clock=clock,
        inference=(inference if settings.local_ml_model_dir is not None else None),
    )
    return ApplicationDependencies(
        upload_service=UploadReservationService(
            repository=repository,
            storage=storage,
            url_signer=signer,
            clock=clock,
            ids=ids,
            bucket_name=LOCAL_BUCKET,
            max_size_bytes=MAX_VIDEO_BYTES,
        ),
        media_service=MediaLibraryService(repository=repository, url_signer=signer),
        query_dependencies=create_query_dependencies(gateway=gateway, temporary_service=temporary_service),
        feature_dependencies=features,
        local_object_router=create_local_object_router(storage, signer, processing),
        profile_client=InMemoryProfileClient(),
    )


def build_runtime_dependencies(settings: AppSettings, verifier: TokenVerifier) -> ApplicationDependencies:
    if settings.app_env in {"local", "test"}:
        return build_local_dependencies(settings, verifier)

    import boto3
    from azure.cosmos import CosmosClient

    bucket = os.environ["MEDIA_BUCKET"]
    s3 = boto3.client("s3", region_name=settings.aws_region)
    storage = S3Storage(s3, bucket)
    signer = S3ObjectUrlSigner(client=s3, bucket_name=bucket)
    cosmos_secret_arn = os.environ.get("AZURE_COSMOS_SECRET_ARN") or os.environ["AZURE_WORKER_SECRET_ARN"]
    cosmos_credential = load_cosmos_credential(
        boto3.client("secretsmanager", region_name=settings.aws_region),
        cosmos_secret_arn,
    )
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cosmos_credential)
    database = cosmos.get_database_client(os.environ.get("COSMOS_DATABASE", "bioarchive"))
    repository = CosmosPagedMediaRepository(
        database.get_container_client(os.environ.get("COSMOS_MEDIA_CONTAINER", "media"))
    )
    subscriptions = CosmosSubscriptionRepository(
        database.get_container_client(os.environ.get("COSMOS_SUBSCRIPTIONS_CONTAINER", "subscriptions"))
    )
    ledger = CosmosDeliveryLedger(
        database.get_container_client(os.environ.get("COSMOS_DELIVERY_LEDGER_CONTAINER", "delivery-ledger"))
    )
    operations = CosmosDeletionOperationStore(
        database.get_container_client(os.environ.get("COSMOS_DELETION_OPERATIONS_CONTAINER", "deletion-operations"))
    )
    clock = _UtcClock()
    ids = _UuidIds()
    normalizer = S3SignedUrlNormalizer(bucket_name=bucket, region=settings.aws_region)
    temporary_service = TemporaryQueryService(
        storage=storage,
        repository=repository,
        inference=WorkerTemporaryInferenceService(
            boto3.client("lambda", region_name=settings.aws_region),
            os.environ["MEDIA_WORKER_FUNCTION_NAME"],
            bucket=bucket,
        ),
        signer=signer,
        bucket_name=bucket,
        max_bytes=MAX_IMAGE_BYTES,
        defer_video_processing=True,
    )
    features = build_sns_feature_dependencies(
        settings=settings,
        sns_client=boto3.client("sns", region_name=settings.aws_region),
        media_repository=repository,
        storage=storage,
        subscription_repository=subscriptions,
        clock=clock,
        ids=ids,
        download_base_url=f"https://{bucket}.s3.{settings.aws_region}.amazonaws.com",
        bucket_name=bucket,
        application_base_url=os.environ.get("FRONTEND_BASE_URL", str(settings.api_base_url)).rstrip("/"),
        url_normalizer=normalizer,
        ledger=ledger,
        operations=operations,
    )
    gateway = QueryGateway(
        client=AzureDataApiClient(str(settings.azure_data_api_base_url)),
        signer=signer,
        storage_bucket=bucket,
    )
    return ApplicationDependencies(
        upload_service=UploadReservationService(
            repository=repository,
            storage=storage,
            url_signer=signer,
            clock=clock,
            ids=ids,
            bucket_name=bucket,
            max_size_bytes=MAX_VIDEO_BYTES,
        ),
        media_service=MediaLibraryService(repository=repository, url_signer=signer),
        query_dependencies=create_query_dependencies(
            gateway=gateway,
            temporary_service=temporary_service,
        ),
        feature_dependencies=features,
        local_object_router=APIRouter(),
        profile_client=S3ProfileClient(s3, bucket),
    )


def create_app(
    settings: AppSettings | None = None,
    verifier: TokenVerifier | None = None,
    container: ApplicationDependencies | None = None,
) -> FastAPI:
    config = settings or AppSettings()
    token_verifier = verifier or _default_verifier(config)
    dependencies = container or build_runtime_dependencies(config, token_verifier)
    app = FastAPI(title="Pacific BioArchive Platform API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        # The browser sends the checksum as signed metadata during local direct uploads.
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "x-amz-meta-sha256"],
    )
    app.state.settings = config
    app.state.auth_verifier = token_verifier
    app.state.dependencies = dependencies

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", uuid4())
        return JSONResponse(
            status_code=error.status_code,
            content=error.to_response(request_id).model_dump(mode="json"),
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/config")
    def auth_config() -> dict[str, object]:
        return config.public_auth_config()

    if config.local_auth_enabled:

        @app.post("/auth/local-token")
        def local_token() -> dict[str, object]:
            now = datetime.now(UTC)
            secret = config.local_auth_secret
            if secret is None:
                raise ApiError("LOCAL_AUTH_DISABLED", "Local authentication is disabled", 404)
            token = jwt.encode(
                {
                    "sub": "local-developer",
                    "email": "local@example.test",
                    "iss": "pacific-bioarchive-local",
                    "aud": config.cognito_app_client_id,
                    "token_use": "access",
                    "iat": int(now.timestamp()),
                    "exp": int((now + timedelta(hours=1)).timestamp()),
                },
                secret.get_secret_value(),
                algorithm="HS256",
            )
            return {"access_token": token, "expires_in": 3600, "token_type": "Bearer"}

    @app.get("/protected/ping")
    def protected_ping(auth: AuthContext = Depends(require_auth)) -> dict[str, str]:
        return {"owner_sub": auth.sub}

    app.include_router(create_upload_router(dependencies.upload_service))
    app.include_router(create_media_router(dependencies.media_service))
    app.include_router(create_query_router(dependencies.query_dependencies))
    app.include_router(create_management_router(dependencies.feature_dependencies))
    app.include_router(create_subscription_router(dependencies.feature_dependencies))
    app.include_router(create_profile_router(dependencies.profile_client))
    if config.app_env in {"local", "test"}:
        app.include_router(dependencies.local_object_router)
    return app


app = create_app()
