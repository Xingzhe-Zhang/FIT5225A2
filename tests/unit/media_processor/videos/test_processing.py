from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def processing_module():
    return importlib.import_module("backend.media_processor.videos.processing")


class DeterministicSession:
    def __init__(self, probe: object, frames: list[bytes], *, fail_extract: bool = False) -> None:
        self._probe = probe
        self._frames = frames
        self._fail_extract = fail_extract
        self.requested_timestamps: tuple[int, ...] | None = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.closed = True

    def probe(self):
        return self._probe

    def extract_frames(self, timestamps: tuple[int, ...]) -> list[bytes]:
        self.requested_timestamps = timestamps
        if self._fail_extract:
            raise TimeoutError("extractor timeout")
        return list(self._frames)


class DeterministicBackend:
    def __init__(self, session: DeterministicSession) -> None:
        self.session = session
        self.open_calls: list[tuple[Path, int]] = []

    def open(self, source: Path, *, timeout_seconds: int) -> DeterministicSession:
        self.open_calls.append((source, timeout_seconds))
        return self.session


def limits(**changes: object):
    module = processing_module()
    values: dict[str, object] = {
        "max_input_bytes": 1024,
        "max_duration_seconds": 30.0,
        "max_frames": 30,
        "timeout_seconds": 15,
        "supported_containers": ("mp4", "mov"),
        "supported_codecs": ("h264", "hevc"),
    }
    values.update(changes)
    return module.VideoLimits(**values)


def test_known_duration_extracts_exactly_one_frame_per_elapsed_second(tmp_path: Path) -> None:
    module = processing_module()
    probe = module.VideoProbe(
        duration_seconds=2.4,
        container="mp4",
        video_codec="h264",
        width=640,
        height=360,
    )
    session = DeterministicSession(probe, [b"frame-0", b"frame-1", b"frame-2"])
    backend = DeterministicBackend(session)

    source = tmp_path / "video.mp4"
    source.write_bytes(b"tiny-video")
    result = module.VideoProcessor(backend, limits()).process(source)

    assert result.timestamps == (0, 1, 2)
    assert result.frames == (b"frame-0", b"frame-1", b"frame-2")
    assert result.representative_thumbnail == b"frame-0"
    assert session.requested_timestamps == (0, 1, 2)
    assert backend.open_calls == [(source, 15)]
    assert session.closed is True


@pytest.mark.parametrize(
    ("probe_changes", "limit_changes", "source", "expected_code"),
    [
        ({"container": "avi"}, {}, b"video", "VIDEO_CONTAINER_UNSUPPORTED"),
        ({"video_codec": "mpeg2"}, {}, b"video", "VIDEO_CODEC_UNSUPPORTED"),
        ({"duration_seconds": 31.0}, {}, b"video", "VIDEO_DURATION_EXCEEDED"),
        ({"duration_seconds": 3.1}, {"max_frames": 3}, b"video", "VIDEO_FRAME_LIMIT_EXCEEDED"),
        ({}, {"max_input_bytes": 4}, b"video", "VIDEO_SIZE_EXCEEDED"),
    ],
)
def test_video_limits_fail_with_stable_codes(
    probe_changes: dict[str, object],
    limit_changes: dict[str, object],
    source: bytes,
    expected_code: str,
    tmp_path: Path,
) -> None:
    module = processing_module()
    probe_values = {
        "duration_seconds": 2.0,
        "container": "mp4",
        "video_codec": "h264",
        "width": 640,
        "height": 360,
        **probe_changes,
    }
    session = DeterministicSession(module.VideoProbe(**probe_values), [b"0", b"1"])

    source_path = tmp_path / "video.mp4"
    source_path.write_bytes(source)
    with pytest.raises(module.VideoProcessingError) as raised:
        module.VideoProcessor(DeterministicBackend(session), limits(**limit_changes)).process(source_path)

    assert raised.value.code == expected_code


def test_extractor_session_closes_when_extraction_fails(tmp_path: Path) -> None:
    module = processing_module()
    session = DeterministicSession(
        module.VideoProbe(2.0, "mp4", "h264", 640, 360),
        [],
        fail_extract=True,
    )

    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    with pytest.raises(TimeoutError, match="extractor timeout"):
        module.VideoProcessor(DeterministicBackend(session), limits()).process(source)

    assert session.closed is True
