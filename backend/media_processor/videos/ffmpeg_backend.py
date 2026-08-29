from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from types import TracebackType

from .processing import VideoProbe, VideoProcessingError


class FfmpegVideoBackend:
    """Open request-scoped FFmpeg sessions without retaining source media."""

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe

    def open(self, source: Path, *, timeout_seconds: int) -> "FfmpegVideoSession":
        return FfmpegVideoSession(
            source=source,
            timeout_seconds=timeout_seconds,
            ffmpeg=self._ffmpeg,
            ffprobe=self._ffprobe,
        )


class FfmpegVideoSession:
    def __init__(
        self,
        *,
        source: Path,
        timeout_seconds: int,
        ffmpeg: str,
        ffprobe: str,
    ) -> None:
        self._source_path = source
        self._timeout_seconds = timeout_seconds
        self._ffmpeg_name = ffmpeg
        self._ffprobe_name = ffprobe
        self._deadline: float | None = None

    def __enter__(self) -> "FfmpegVideoSession":
        ffmpeg = shutil.which(self._ffmpeg_name)
        ffprobe = shutil.which(self._ffprobe_name)
        if ffmpeg is None or ffprobe is None:
            raise VideoProcessingError(
                "VIDEO_BACKEND_UNAVAILABLE",
                "Local video processing requires ffmpeg and ffprobe",
            )
        self._ffmpeg_name = ffmpeg
        self._ffprobe_name = ffprobe
        if not self._source_path.is_file():
            raise VideoProcessingError("VIDEO_CORRUPT", "Uploaded video file is unavailable")
        self._deadline = time.monotonic() + self._timeout_seconds
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self._deadline = None

    def probe(self) -> VideoProbe:
        source = self._require_open_source()
        completed = self._run(
            [
                self._ffprobe_name,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(source),
            ],
            failure_code="VIDEO_CORRUPT",
            failure_message="ffprobe could not inspect the uploaded video",
        )
        try:
            payload = json.loads(completed.stdout)
            stream = next(item for item in payload["streams"] if item.get("codec_type") == "video")
            format_data = payload["format"]
            return VideoProbe(
                duration_seconds=float(format_data["duration"]),
                container=_container_name(str(format_data["format_name"])),
                video_codec=str(stream["codec_name"]),
                width=int(stream["width"]),
                height=int(stream["height"]),
            )
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
            raise VideoProcessingError(
                "VIDEO_CORRUPT",
                "ffprobe returned incomplete video metadata",
            ) from error

    def extract_frames(self, timestamps: tuple[int, ...]) -> list[bytes]:
        source = self._require_open_source()
        frames: list[bytes] = []
        for timestamp in timestamps:
            completed = self._run(
                [
                    self._ffmpeg_name,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    str(timestamp),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "pipe:1",
                ],
                failure_code="VIDEO_FRAME_EXTRACTION_FAILED",
                failure_message=f"ffmpeg could not extract the frame at second {timestamp}",
            )
            frames.append(completed.stdout)
        return frames

    def _run(
        self,
        command: list[str],
        *,
        failure_code: str,
        failure_message: str,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=self._remaining_timeout(),
            )
        except subprocess.TimeoutExpired as error:
            raise VideoProcessingError(
                "VIDEO_PROCESSING_TIMEOUT",
                "Local video processing exceeded its configured timeout",
            ) from error
        except OSError as error:
            raise VideoProcessingError(
                "VIDEO_BACKEND_UNAVAILABLE",
                "Local video processing could not execute ffmpeg",
            ) from error
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            message = f"{failure_message}: {detail}" if detail else failure_message
            raise VideoProcessingError(failure_code, message)
        return completed

    def _remaining_timeout(self) -> float:
        if self._deadline is None:
            raise RuntimeError("video session is not open")
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise VideoProcessingError(
                "VIDEO_PROCESSING_TIMEOUT",
                "Local video processing exceeded its configured timeout",
            )
        return remaining

    def _require_open_source(self) -> Path:
        if not self._source_path.is_file():
            raise RuntimeError("video session is not open")
        return self._source_path


def _container_name(raw_name: str) -> str:
    names = {name.strip().casefold() for name in raw_name.split(",")}
    if "mp4" in names:
        return "mp4"
    if "mov" in names:
        return "mov"
    return next(iter(names), "")
