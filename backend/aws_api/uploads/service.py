from __future__ import annotations

import re
from datetime import timedelta
from pathlib import PurePath
from threading import Lock
from urllib.parse import urlparse
from uuid import UUID

from backend.common.contracts.models import (
    MediaRecord,
    UploadReservationCancelResponse,
    UploadReservationRequest,
    UploadReservationResponse,
)
from backend.common.errors.models import ApiError
from backend.common.media_limits import max_bytes_for
from backend.common.providers.interfaces import (
    Clock,
    IdGenerator,
    MediaRepository,
    ObjectUrlSigner,
    ObjectStorage,
)


CONTENT_TYPES: dict[str, tuple[str, str]] = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
}
SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")


def safe_file_name(file_name: str) -> str:
    if not SAFE_FILE_NAME.fullmatch(file_name) or file_name.startswith("."):
        raise ApiError(
            "UPLOAD_FILE_NAME_INVALID",
            "File name must contain only safe letters, numbers, spaces, dots, dashes, or underscores",
            422,
        )
    normalized = re.sub(r"\s+", "-", file_name.strip().lower())
    if normalized in {".", ".."} or PurePath(normalized).name != normalized:
        raise ApiError("UPLOAD_FILE_NAME_INVALID", "File name must not contain a path", 422)
    return normalized


def content_type_for(file_name: str, media_type: str) -> str:
    extension = PurePath(file_name).suffix.lower()
    supported = CONTENT_TYPES.get(extension)
    if supported is None:
        raise ApiError("UPLOAD_EXTENSION_UNSUPPORTED", "File extension is not supported", 422)
    expected_media_type, content_type = supported
    if media_type != expected_media_type:
        raise ApiError(
            "UPLOAD_MEDIA_TYPE_MISMATCH",
            "File extension does not match the declared media type",
            422,
        )
    return content_type


class UploadReservationService:
    def __init__(
        self,
        *,
        repository: MediaRepository,
        storage: ObjectStorage,
        url_signer: ObjectUrlSigner,
        clock: Clock,
        ids: IdGenerator,
        bucket_name: str,
        max_size_bytes: int,
        upload_url_ttl_seconds: int = 900,
    ) -> None:
        if not bucket_name or "/" in bucket_name:
            raise ValueError("bucket_name must be an S3 bucket name")
        if max_size_bytes < 1:
            raise ValueError("max_size_bytes must be positive")
        self._repository = repository
        self._storage = storage
        self._url_signer = url_signer
        self._clock = clock
        self._ids = ids
        self._bucket_name = bucket_name
        self._max_size_bytes = max_size_bytes
        self._upload_url_ttl_seconds = upload_url_ttl_seconds
        self._reservation_lock = Lock()

    def reserve(
        self,
        owner_sub: str,
        request: UploadReservationRequest,
    ) -> UploadReservationResponse:
        if not owner_sub.strip():
            raise ApiError("AUTH_SUBJECT_INVALID", "Authenticated owner is required", 401)
        if request.size_bytes > min(self._max_size_bytes, max_bytes_for(request.media_type)):
            raise ApiError("UPLOAD_TOO_LARGE", "File exceeds the configured upload limit", 422)

        file_name = safe_file_name(request.file_name)
        content_type = content_type_for(file_name, request.media_type)
        with self._reservation_lock:
            now = self._clock.now_utc()
            expires_at = now + timedelta(seconds=self._upload_url_ttl_seconds)
            while True:
                candidate_id = self._ids.new_uuid()
                reservation = self._repository.reserve_upload(
                    owner_sub,
                    request.sha256,
                    candidate_id,
                    expires_at,
                )
                if not reservation.created:
                    existing = self._repository.get(owner_sub, reservation.media_id)
                    if existing is not None and self._is_expired_unuploaded(existing, now):
                        object_key = self._object_key(str(existing.original_storage_uri))
                        if self._storage.exists(object_key):
                            # The PUT completed even though the browser did not observe it.
                            # Preserve the record/checksum and let the S3 event finish processing.
                            recovered = existing.model_copy(
                                update={"status": "uploaded", "expires_at": None, "updated_at": now}
                            )
                            self._repository.upsert(recovered)
                            existing = recovered
                        elif self._repository.delete(owner_sub, existing.media_id):
                            # O(1) lazy cleanup: only the checksum hit is reclaimed; no owner or
                            # container scan is performed. The next loop creates a fresh reservation.
                            continue
                    return self._duplicate_response(reservation.media_id, existing)

                object_key = f"originals/{reservation.media_id}/{request.sha256}/{file_name}"
                try:
                    self._repository.upsert(
                        MediaRecord(
                            media_id=reservation.media_id,
                            owner_sub=owner_sub,
                            sha256=request.sha256,
                            file_name=file_name,
                            media_type=request.media_type,
                            original_storage_uri=f"s3://{self._bucket_name}/{object_key}",
                            thumbnail_storage_uri=None,
                            tag_counts={},
                            manual_tags=[],
                            model_version="pending",
                            status="reserved",
                            expires_at=expires_at,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    upload_url = self._url_signer.create_upload_url(
                        object_key,
                        content_type=content_type,
                        checksum_sha256=request.sha256,
                        expires_in_seconds=self._upload_url_ttl_seconds,
                    )
                    return UploadReservationResponse(
                        media_id=reservation.media_id,
                        duplicate=False,
                        status="reserved",
                        upload_url=upload_url,
                        object_key=object_key,
                        expires_in_seconds=self._upload_url_ttl_seconds,
                        upload_headers={
                            "Content-Type": content_type,
                            "x-amz-meta-sha256": request.sha256,
                        },
                    )
                except Exception:
                    # Reservation and media documents are separate in Cosmos. Compensate every
                    # failure after the checksum claim so a partial request cannot block re-upload.
                    try:
                        self._repository.delete(owner_sub, reservation.media_id)
                    finally:
                        self._repository.release_upload_reservation(
                            owner_sub,
                            request.sha256,
                            reservation.media_id,
                        )
                    raise

    def cancel(self, owner_sub: str, media_id: UUID, sha256: str) -> UploadReservationCancelResponse:
        if not owner_sub.strip():
            raise ApiError("AUTH_SUBJECT_INVALID", "Authenticated owner is required", 401)
        with self._reservation_lock:
            record = self._repository.get(owner_sub, media_id)
            if record is None:
                self._repository.release_upload_reservation(owner_sub, sha256, media_id)
                return UploadReservationCancelResponse(media_id=media_id, status="already_cancelled")
            if record.sha256 != sha256:
                raise ApiError(
                    "UPLOAD_RESERVATION_CONFLICT",
                    "Reservation checksum does not match",
                    409,
                )
            if record.status != "reserved":
                raise ApiError(
                    "UPLOAD_RESERVATION_COMMITTED",
                    "The upload has already entered processing and cannot be cancelled",
                    409,
                )
            object_key = self._object_key(str(record.original_storage_uri))
            if self._storage.exists(object_key):
                self._repository.upsert(
                    record.model_copy(
                        update={
                            "status": "uploaded",
                            "expires_at": None,
                            "updated_at": self._clock.now_utc(),
                        }
                    )
                )
                raise ApiError(
                    "UPLOAD_RESERVATION_COMMITTED",
                    "The object was received and will continue processing",
                    409,
                )
            deleted = self._repository.delete(owner_sub, media_id)
            self._repository.release_upload_reservation(owner_sub, sha256, media_id)
            return UploadReservationCancelResponse(
                media_id=media_id,
                status="cancelled" if deleted else "already_cancelled",
            )

    @staticmethod
    def _is_expired_unuploaded(record: MediaRecord, now) -> bool:
        return record.status == "reserved" and record.expires_at is not None and record.expires_at <= now

    @staticmethod
    def _object_key(storage_uri: str) -> str:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
            raise ValueError("reservation storage URI must be an S3 object URI")
        return parsed.path.lstrip("/")

    @staticmethod
    def _duplicate_response(media_id: UUID, existing: MediaRecord | None) -> UploadReservationResponse:
        return UploadReservationResponse(
            media_id=media_id,
            duplicate=True,
            status=existing.status if existing else "reserved",
            upload_url=None,
            object_key=None,
            expires_in_seconds=None,
            upload_headers=None,
        )
