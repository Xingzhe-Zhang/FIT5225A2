from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.common.media_limits import MAX_IMAGE_BYTES


@dataclass(frozen=True, slots=True)
class ThumbnailConfig:
    max_width: int = 320
    max_height: int = 320
    jpeg_quality: int = 80
    max_input_bytes: int = MAX_IMAGE_BYTES
    max_pixels: int = 40_000_000

    def __post_init__(self) -> None:
        if self.max_width < 1 or self.max_height < 1:
            raise ValueError("thumbnail bounds must be positive")
        if not 1 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 1 and 95")
        if self.max_input_bytes < 1 or self.max_pixels < 1:
            raise ValueError("image safety limits must be positive")


@dataclass(frozen=True, slots=True)
class ThumbnailResult:
    data: bytes
    width: int
    height: int


class ImageProcessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PillowThumbnailer:
    def __init__(self, config: ThumbnailConfig) -> None:
        self._config = config

    @property
    def max_input_bytes(self) -> int:
        return self._config.max_input_bytes

    def create(self, source_bytes: bytes) -> ThumbnailResult:
        if not source_bytes or len(source_bytes) > self._config.max_input_bytes:
            raise ImageProcessingError("IMAGE_SIZE_INVALID", "Image byte size is outside the configured limit")

        try:
            with Image.open(io.BytesIO(source_bytes)) as probe:
                # Pillow identifies some JPEG camera files as MPO because they
                # contain multiple JPEG frames. The first frame remains a
                # standards-compatible JPEG image and is safe to thumbnail.
                if probe.format not in {"JPEG", "MPO", "PNG"}:
                    raise ImageProcessingError(
                        "IMAGE_FORMAT_UNSUPPORTED",
                        "Only JPEG and PNG images are supported",
                    )
                if probe.width * probe.height > self._config.max_pixels:
                    raise ImageProcessingError("IMAGE_DIMENSIONS_TOO_LARGE", "Image dimensions exceed the safety limit")
                probe.verify()

            with Image.open(io.BytesIO(source_bytes)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                rgb = self._to_rgb(oriented)
                rgb.thumbnail(
                    (self._config.max_width, self._config.max_height),
                    Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                rgb.save(
                    output,
                    format="JPEG",
                    quality=self._config.jpeg_quality,
                    optimize=True,
                )
                return ThumbnailResult(output.getvalue(), rgb.width, rgb.height)
        except ImageProcessingError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
            raise ImageProcessingError("IMAGE_CORRUPT", "Image could not be decoded") from error

    @staticmethod
    def _to_rgb(image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB")
