from __future__ import annotations

from urllib.parse import urlparse

from backend.common.contracts.models import QueryResponse, QueryResult
from backend.common.providers.interfaces import MediaRepository, ObjectUrlSigner


class MediaLibraryService:
    def __init__(
        self,
        *,
        repository: MediaRepository,
        url_signer: ObjectUrlSigner,
        download_url_ttl_seconds: int = 900,
    ) -> None:
        if download_url_ttl_seconds < 1:
            raise ValueError("download_url_ttl_seconds must be positive")
        self._repository = repository
        self._url_signer = url_signer
        self._download_url_ttl_seconds = download_url_ttl_seconds

    def list_for_owner(self, owner_sub: str) -> QueryResponse:
        records = sorted(self._repository.list_for_owner(owner_sub), key=lambda record: str(record.media_id))
        return QueryResponse(results=[self._signed_result(record) for record in records])

    def _signed_result(self, record: object) -> QueryResult:
        from backend.common.contracts.models import MediaRecord

        if not isinstance(record, MediaRecord):
            raise TypeError("media repository must return MediaRecord values")
        available = record.status in {"prepared", "ready"}
        return QueryResult(
            media_id=record.media_id,
            file_name=record.file_name,
            media_type=record.media_type,
            status=record.status,
            original_url=(
                self._url_signer.create_download_url(
                    self._object_key(str(record.original_storage_uri)),
                    expires_in_seconds=self._download_url_ttl_seconds,
                )
                if available
                else None
            ),
            thumbnail_url=(
                self._url_signer.create_download_url(
                    self._object_key(str(record.thumbnail_storage_uri)),
                    expires_in_seconds=self._download_url_ttl_seconds,
                )
                if available and record.thumbnail_storage_uri is not None
                else None
            ),
            tag_counts=record.tag_counts,
            manual_tags=record.manual_tags,
            failure_code=record.failure_code,
            failure_message=record.failure_message,
        )

    @staticmethod
    def _object_key(storage_uri: str) -> str:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
            raise ValueError("storage URI must be an S3 object URI")
        return parsed.path.lstrip("/")
