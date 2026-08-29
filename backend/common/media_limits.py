"""Single source of truth for media limits enforced by the API and worker.

The video cap is deliberately well below Lambda's 10 GiB ephemeral storage.
This leaves room for ffmpeg working files and derived frames while the 3,008 MiB
worker never holds the source video in memory.
"""

MIB = 1024 * 1024

MAX_IMAGE_BYTES = 25 * MIB
MAX_VIDEO_BYTES = 512 * MIB
MAX_VIDEO_DURATION_SECONDS = 120.0
MAX_VIDEO_FRAMES = 120
VIDEO_PROCESSING_TIMEOUT_SECONDS = 780


def max_bytes_for(media_type: str) -> int:
    if media_type == "image":
        return MAX_IMAGE_BYTES
    if media_type == "video":
        return MAX_VIDEO_BYTES
    raise ValueError(f"unsupported media type: {media_type}")
