from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse

from backend.common.contracts.models import MediaRecord, QueryResponse, QueryResult
from backend.common.providers.interfaces import ObjectUrlSigner


class StorageUriError(ValueError):
    pass


class InternalQueryClient(Protocol):
    def query_tags(self, access_token: str, payload: object) -> list[MediaRecord]: ...
    def query_species(self, access_token: str, payload: object) -> list[MediaRecord]: ...
    def query_thumbnail(self, access_token: str, payload: object) -> MediaRecord: ...


class QueryGateway:
    """AWS-side token-forwarding and short-lived URL signing boundary."""

    def __init__(
        self,
        *,
        client: InternalQueryClient,
        signer: ObjectUrlSigner,
        storage_bucket: str,
        expires_in_seconds: int = 300,
    ) -> None:
        if not 1 <= expires_in_seconds <= 3600:
            raise ValueError("download URL lifetime must be between 1 and 3600 seconds")
        self._client = client
        self._signer = signer
        self._storage_bucket = storage_bucket
        self._expires_in_seconds = expires_in_seconds

    def query_tags(self, access_token: str, payload: object) -> QueryResponse:
        return self._sign(self._client.query_tags(access_token, payload))

    def query_species(self, access_token: str, payload: object) -> QueryResponse:
        return self._sign(self._client.query_species(access_token, payload))

    def query_thumbnail(self, access_token: str, payload: object) -> QueryResponse:
        return self._sign([self._client.query_thumbnail(access_token, payload)])

    def _sign(self, records: list[MediaRecord]) -> QueryResponse:
        unique: dict[object, MediaRecord] = {}
        for record in records:
            unique.setdefault(record.media_id, record)
        results = [self._sign_record(record) for record in unique.values()]
        return QueryResponse(results=results)

    def _sign_record(self, record: MediaRecord) -> QueryResult:
        available = record.status in {"prepared", "ready"}
        original_url = (
            self._signer.create_download_url(
                self._key(str(record.original_storage_uri)),
                expires_in_seconds=self._expires_in_seconds,
            )
            if available
            else None
        )
        thumbnail_url = None
        if available and record.media_type == "image" and record.thumbnail_storage_uri is not None:
            thumbnail_url = self._signer.create_download_url(
                self._key(str(record.thumbnail_storage_uri)),
                expires_in_seconds=self._expires_in_seconds,
            )
        return QueryResult(
            media_id=record.media_id,
            media_type=record.media_type,
            status=record.status,
            original_url=original_url,
            thumbnail_url=thumbnail_url,
            tag_counts=record.tag_counts,
            manual_tags=record.manual_tags,
            failure_code=record.failure_code,
            failure_message=record.failure_message,
        )

    def _key(self, storage_uri: str) -> str:
        parsed = urlparse(storage_uri)
        key = parsed.path.lstrip("/")
        if (
            parsed.scheme != "s3"
            or parsed.netloc != self._storage_bucket
            or not key
            or parsed.query
            or parsed.fragment
        ):
            raise StorageUriError("canonical storage URI is outside the configured bucket")
        return key
