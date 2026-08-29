from __future__ import annotations

import hashlib

import pytest

from backend.common.providers.fakes import InMemoryObjectStorage
from backend.media_processor.videos.processing import VideoProcessingError
from backend.media_processor.videos.streaming import stream_object_to_path


def test_streams_multiple_chunks_and_accepts_the_exact_limit(tmp_path) -> None:
    source = b"abcdefghij"
    storage = InMemoryObjectStorage()
    storage.put_bytes("video", source, content_type="video/mp4")
    destination = tmp_path / "source.video"

    size, digest = stream_object_to_path(
        storage,
        "video",
        destination,
        max_bytes=len(source),
        expected_size=len(source),
        expected_sha256=hashlib.sha256(source).hexdigest(),
    )

    assert size == len(source)
    assert digest == hashlib.sha256(source).hexdigest()
    assert destination.read_bytes() == source


@pytest.mark.parametrize(
    ("max_bytes", "expected_size", "expected_sha256", "expected_code"),
    [
        (9, 10, None, "VIDEO_SIZE_EXCEEDED"),
        (20, 11, None, "VIDEO_CONTENT_LENGTH_MISMATCH"),
        (20, 10, "0" * 64, "VIDEO_CHECKSUM_MISMATCH"),
    ],
)
def test_rejects_size_and_checksum_mismatches(
    tmp_path,
    max_bytes: int,
    expected_size: int,
    expected_sha256: str | None,
    expected_code: str,
) -> None:
    storage = InMemoryObjectStorage()
    storage.put_bytes("video", b"abcdefghij", content_type="video/mp4")

    with pytest.raises(VideoProcessingError) as raised:
        stream_object_to_path(
            storage,
            "video",
            tmp_path / "source.video",
            max_bytes=max_bytes,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )

    assert raised.value.code == expected_code
