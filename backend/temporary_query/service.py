"""Temporary uploaded-file query orchestration."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path, PurePath
from uuid import UUID

from backend.common.contracts.models import QueryResponse, QueryResult
from backend.common.providers.interfaces import (
    InferenceService,
    MediaRepository,
    ObjectStorage,
    ObjectUrlSigner,
)
from backend.media_processor.videos.processing import VideoProcessingError, VideoProcessor


LOGGER = logging.getLogger(__name__)


class TemporaryFileValidationError(ValueError):
    """Raised when a query upload is unsafe or unsupported."""


class TemporaryQueryService:
    """Infer a request-scoped object, query owned media, and always clean up."""

    _SUPPORTED: dict[str, tuple[set[str], tuple[bytes, ...]]] = {
        "image/jpeg": ({".jpg", ".jpeg"}, (b"\xff\xd8\xff",)),
        "image/png": ({".png"}, (b"\x89PNG\r\n\x1a\n",)),
        "video/mp4": ({".mp4"}, (b"ftyp",)),
        "video/quicktime": ({".mov"}, (b"ftyp",)),
    }

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        repository: MediaRepository,
        inference: InferenceService,
        signer: ObjectUrlSigner,
        bucket_name: str,
        max_bytes: int,
        video_processor: VideoProcessor | None = None,
        defer_video_processing: bool = False,
        url_expiry_seconds: int = 900,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._inference = inference
        self._signer = signer
        self._bucket_name = bucket_name
        self._max_bytes = max_bytes
        self._video_processor = video_processor
        self._defer_video_processing = defer_video_processing
        self._url_expiry_seconds = url_expiry_seconds

    def query(
        self,
        *,
        owner_sub: str,
        request_id: UUID,
        file_name: str,
        content_type: str,
        data: bytes,
    ) -> QueryResponse:
        safe_name = PurePath(file_name).name
        key = f"temporary-query/{request_id}/{safe_name}"
        uri = f"s3://{self._bucket_name}/{key}"
        temporary_keys = [key]
        outcome = "failed"
        try:
            self._validate(file_name=file_name, content_type=content_type, data=data)
            self._storage.put_bytes(key, data, content_type=content_type)
            inference_uris = [uri]
            if content_type.startswith("video/"):
                if self._video_processor is None and not self._defer_video_processing:
                    raise TemporaryFileValidationError("video query processing is not configured")
                if self._video_processor is not None:
                    with tempfile.TemporaryDirectory(prefix="pba-local-query-") as temporary:
                        source_path = Path(temporary) / "source.video"
                        source_path.write_bytes(data)
                        video = self._video_processor.process(source_path, size_bytes=len(data))
                    inference_uris = []
                    for timestamp, frame in zip(video.timestamps, video.frames, strict=True):
                        frame_key = f"temporary-query/{request_id}/frames/{timestamp:06d}.jpg"
                        self._storage.put_bytes(frame_key, frame, content_type="image/jpeg")
                        temporary_keys.append(frame_key)
                        inference_uris.append(f"s3://{self._bucket_name}/{frame_key}")
            inferred = self._inference.infer(inference_uris)
            required_tags = {
                tag.strip().casefold(): 1
                for tag in inferred.tag_counts
                if tag.strip()
            }
            records = (
                self._repository.query_by_tags(owner_sub, required_tags)
                if required_tags
                else []
            )
            results = [self._result(record) for record in records]
            outcome = "success"
            return QueryResponse(results=results)
        finally:
            try:
                # A cloud worker may create temporary video frames on our behalf.
                self._storage.delete_keys(self._storage.list_keys(f"temporary-query/{request_id}/"))
            finally:
                LOGGER.info(
                    "temporary query completed",
                    extra={
                        "request_id": str(request_id),
                        "byte_size": len(data),
                        "media_type": content_type,
                        "outcome": outcome,
                    },
                )

    def _validate(self, *, file_name: str, content_type: str, data: bytes) -> None:
        if not data or len(data) > self._max_bytes:
            raise TemporaryFileValidationError("file size is outside the accepted range")
        if PurePath(file_name).name != file_name or "/" in file_name or "\\" in file_name:
            raise TemporaryFileValidationError("file name must not contain a path")
        specification = self._SUPPORTED.get(content_type)
        if specification is None:
            raise TemporaryFileValidationError("unsupported media type")
        extensions, signatures = specification
        if PurePath(file_name).suffix.casefold() not in extensions:
            raise TemporaryFileValidationError("file extension does not match media type")
        signature_matches = (
            data[4:8] == signatures[0]
            if content_type in {"video/mp4", "video/quicktime"}
            else any(data.startswith(signature) for signature in signatures)
        )
        if not signature_matches:
            raise TemporaryFileValidationError("file content does not match media type")

    def _result(self, record) -> QueryResult:
        available = record.status in {"prepared", "ready"}
        return QueryResult(
            media_id=record.media_id,
            file_name=record.file_name,
            media_type=record.media_type,
            status=record.status,
            original_url=(
                self._signer.create_download_url(
                    self._object_key(str(record.original_storage_uri)),
                    expires_in_seconds=self._url_expiry_seconds,
                )
                if available
                else None
            ),
            thumbnail_url=(
                self._signer.create_download_url(
                    self._object_key(str(record.thumbnail_storage_uri)),
                    expires_in_seconds=self._url_expiry_seconds,
                )
                if available and record.thumbnail_storage_uri
                else None
            ),
            tag_counts=record.tag_counts,
            manual_tags=record.manual_tags,
            failure_code=record.failure_code,
            failure_message=record.failure_message,
        )

    def _object_key(self, storage_uri: str) -> str:
        prefix = f"s3://{self._bucket_name}/"
        if not storage_uri.startswith(prefix):
            raise ValueError("record points outside the configured media bucket")
        return storage_uri[len(prefix) :]
