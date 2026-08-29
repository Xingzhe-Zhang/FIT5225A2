from __future__ import annotations

import hashlib
from pathlib import Path

from backend.common.providers.interfaces import ObjectStorage

from .processing import VideoProcessingError


STREAM_CHUNK_BYTES = 8 * 1024 * 1024


def stream_object_to_path(
    storage: ObjectStorage,
    key: str,
    destination: Path,
    *,
    max_bytes: int,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> tuple[int, str]:
    """Download an object without materialising it, validating it while writing."""

    if expected_size is not None and (expected_size < 1 or expected_size > max_bytes):
        raise VideoProcessingError(
            "VIDEO_SIZE_EXCEEDED",
            "Video byte size exceeds the configured limit",
        )

    digest = hashlib.sha256()
    total = 0
    with destination.open("xb") as output:
        for chunk in storage.iter_bytes(key, chunk_size=STREAM_CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise VideoProcessingError(
                    "VIDEO_SIZE_EXCEEDED",
                    "Video byte size exceeds the configured limit",
                )
            digest.update(chunk)
            output.write(chunk)

    if total < 1:
        raise VideoProcessingError("VIDEO_SIZE_EXCEEDED", "Uploaded video is empty")
    if expected_size is not None and total != expected_size:
        raise VideoProcessingError(
            "VIDEO_CONTENT_LENGTH_MISMATCH",
            "Streamed video size does not match S3 ContentLength",
        )
    actual_sha256 = digest.hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise VideoProcessingError(
            "VIDEO_CHECKSUM_MISMATCH",
            "Uploaded video checksum does not match its reservation",
        )
    return total, actual_sha256
