from __future__ import annotations

import importlib


def s3_module():
    return importlib.import_module("backend.aws_api.uploads.s3")


class RecordingS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], int]] = []

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, object],
        ExpiresIn: int,
    ) -> str:
        self.calls.append((operation, Params, ExpiresIn))
        return "https://signed.example.test/upload"


def test_upload_signature_requires_content_type_and_sha256_metadata() -> None:
    client = RecordingS3Client()
    signer = s3_module().S3ObjectUrlSigner(client=client, bucket_name="pba-media")

    url = signer.create_upload_url(
        "originals/abc/camera.jpg",
        content_type="image/jpeg",
        checksum_sha256="a" * 64,
        expires_in_seconds=900,
    )

    assert url == "https://signed.example.test/upload"
    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "pba-media",
                "Key": "originals/abc/camera.jpg",
                "ContentType": "image/jpeg",
                "Metadata": {"sha256": "a" * 64},
            },
            900,
        )
    ]
