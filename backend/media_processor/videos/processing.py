from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VideoProbe:
    duration_seconds: float
    container: str
    video_codec: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class VideoLimits:
    max_input_bytes: int
    max_duration_seconds: float
    max_frames: int
    timeout_seconds: int
    supported_containers: tuple[str, ...]
    supported_codecs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.max_input_bytes < 1 or self.max_duration_seconds <= 0:
            raise ValueError("video size and duration limits must be positive")
        if self.max_frames < 1 or self.timeout_seconds < 1:
            raise ValueError("video frame and timeout limits must be positive")
        if not self.supported_containers or not self.supported_codecs:
            raise ValueError("at least one video container and codec must be supported")


@dataclass(frozen=True, slots=True)
class VideoProcessingResult:
    probe: VideoProbe
    timestamps: tuple[int, ...]
    frames: tuple[bytes, ...]
    representative_thumbnail: bytes


class VideoProcessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VideoSession(Protocol):
    def probe(self) -> VideoProbe: ...
    def extract_frames(self, timestamps: tuple[int, ...]) -> list[bytes]: ...


class OpenVideoSession(Protocol):
    def __enter__(self) -> VideoSession: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class VideoBackend(Protocol):
    def open(self, source: Path, *, timeout_seconds: int) -> OpenVideoSession: ...


class VideoProcessor:
    def __init__(self, backend: VideoBackend, limits: VideoLimits) -> None:
        self._backend = backend
        self._limits = limits

    @property
    def max_input_bytes(self) -> int:
        return self._limits.max_input_bytes

    def process(self, source: Path, *, size_bytes: int | None = None) -> VideoProcessingResult:
        actual_size = source.stat().st_size if size_bytes is None else size_bytes
        if actual_size < 1 or actual_size > self._limits.max_input_bytes:
            raise VideoProcessingError("VIDEO_SIZE_EXCEEDED", "Video byte size exceeds the configured limit")

        with self._backend.open(source, timeout_seconds=self._limits.timeout_seconds) as session:
            probe = session.probe()
            timestamps = self._validate_and_plan(probe)
            frames = tuple(session.extract_frames(timestamps))
            if len(frames) != len(timestamps) or any(not frame for frame in frames):
                raise VideoProcessingError(
                    "VIDEO_FRAME_EXTRACTION_INVALID",
                    "Extractor did not return one non-empty frame per requested timestamp",
                )
            return VideoProcessingResult(
                probe=probe,
                timestamps=timestamps,
                frames=frames,
                representative_thumbnail=frames[0],
            )

    def _validate_and_plan(self, probe: VideoProbe) -> tuple[int, ...]:
        container = probe.container.casefold()
        codec = probe.video_codec.casefold()
        if container not in {value.casefold() for value in self._limits.supported_containers}:
            raise VideoProcessingError("VIDEO_CONTAINER_UNSUPPORTED", "Video container is not supported")
        if codec not in {value.casefold() for value in self._limits.supported_codecs}:
            raise VideoProcessingError("VIDEO_CODEC_UNSUPPORTED", "Video codec is not supported")
        if (
            not math.isfinite(probe.duration_seconds)
            or probe.duration_seconds <= 0
            or probe.width < 1
            or probe.height < 1
        ):
            raise VideoProcessingError("VIDEO_CORRUPT", "Video probe returned invalid properties")
        if probe.duration_seconds > self._limits.max_duration_seconds:
            raise VideoProcessingError("VIDEO_DURATION_EXCEEDED", "Video duration exceeds the configured limit")

        timestamps = tuple(range(math.ceil(probe.duration_seconds)))
        if len(timestamps) > self._limits.max_frames:
            raise VideoProcessingError("VIDEO_FRAME_LIMIT_EXCEEDED", "Video requires too many extracted frames")
        return timestamps
