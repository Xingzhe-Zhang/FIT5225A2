from __future__ import annotations

import importlib
import io
import random

import pytest
from PIL import Image


def thumbnail_module():
    return importlib.import_module("backend.media_processor.images.thumbnail")


def noisy_png(width: int, height: int) -> bytes:
    randomizer = random.Random(5225)
    pixels = bytes(randomizer.randrange(256) for _ in range(width * height * 3))
    image = Image.frombytes("RGB", (width, height), pixels)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def oriented_jpeg() -> bytes:
    image = Image.new("RGB", (120, 60), "navy")
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95, exif=exif)
    return output.getvalue()


def mpo_jpeg() -> bytes:
    first = Image.new("RGB", (120, 60), "navy")
    second = Image.new("RGB", (120, 60), "green")
    output = io.BytesIO()
    first.save(output, format="MPO", save_all=True, append_images=[second])
    return output.getvalue()


def test_thumbnail_preserves_ratio_fits_bounds_and_is_smaller() -> None:
    module = thumbnail_module()
    original = noisy_png(800, 400)
    processor = module.PillowThumbnailer(
        module.ThumbnailConfig(max_width=200, max_height=200, jpeg_quality=75)
    )

    result = processor.create(original)

    assert (result.width, result.height) == (200, 100)
    assert len(result.data) < len(original)
    with Image.open(io.BytesIO(result.data)) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size == (200, 100)
        assert not thumbnail.getexif()


def test_thumbnail_applies_exif_orientation_before_resizing() -> None:
    module = thumbnail_module()
    processor = module.PillowThumbnailer(
        module.ThumbnailConfig(max_width=100, max_height=100, jpeg_quality=80)
    )

    result = processor.create(oriented_jpeg())

    assert (result.width, result.height) == (50, 100)


def test_thumbnail_accepts_mpo_as_jpeg_and_uses_first_frame() -> None:
    module = thumbnail_module()
    processor = module.PillowThumbnailer(
        module.ThumbnailConfig(max_width=60, max_height=60, jpeg_quality=80)
    )

    result = processor.create(mpo_jpeg())

    assert (result.width, result.height) == (60, 30)
    with Image.open(io.BytesIO(result.data)) as thumbnail:
        assert thumbnail.format == "JPEG"


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"not-an-image", "IMAGE_CORRUPT"),
        (lambda: _gif_bytes(), "IMAGE_FORMAT_UNSUPPORTED"),
    ],
)
def test_invalid_images_have_stable_failure_codes(payload: bytes | object, expected_code: str) -> None:
    module = thumbnail_module()
    processor = module.PillowThumbnailer(module.ThumbnailConfig())
    data = payload() if callable(payload) else payload

    with pytest.raises(module.ImageProcessingError) as raised:
        processor.create(data)

    assert raised.value.code == expected_code


def _gif_bytes() -> bytes:
    image = Image.new("RGB", (20, 20), "green")
    output = io.BytesIO()
    image.save(output, format="GIF")
    return output.getvalue()
