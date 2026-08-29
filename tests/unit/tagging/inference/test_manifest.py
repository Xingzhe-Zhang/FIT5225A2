from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.tagging.inference.manifest import (
    ArtifactChecksumError,
    LocalArtifactReader,
    ManifestBundleLoader,
    ManifestValidationError,
    S3ArtifactReader,
    load_configured_bundle,
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_manifest(root: Path, *, version: str, classifier: bytes) -> Path:
    detector = b"detector-v1"
    labels = b"dingo\nwombat\n"
    (root / "mdv5a.pt").write_bytes(detector)
    (root / f"classifier-{version}.pt").write_bytes(classifier)
    (root / "labels.txt").write_bytes(labels)
    manifest = {
        "schema_version": "1.0",
        "model_version": version,
        "detector": {"uri": str(root / "mdv5a.pt"), "sha256": sha256(detector)},
        "classifier": {
            "uri": str(root / f"classifier-{version}.pt"),
            "sha256": sha256(classifier),
        },
        "labels": {"uri": str(root / "labels.txt"), "sha256": sha256(labels)},
        "input": {"width": 480, "height": 480},
        "thresholds": {"detection": 0.2, "classification": 0.5},
    }
    path = root / f"manifest-{version}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_loader_validates_checksums_and_caches_by_version(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, version="1.2.3", classifier=b"classifier-v1")
    loader = ManifestBundleLoader(
        readers={"file": LocalArtifactReader()},
        cache_dir=tmp_path / "cache",
    )

    bundle = loader.load(manifest_path.as_uri(), device="cpu")

    assert bundle.model_version == "1.2.3"
    assert bundle.device == "cpu"
    assert bundle.labels == ("dingo", "wombat")
    assert bundle.detector_path.read_bytes() == b"detector-v1"
    assert bundle.classifier_path.read_bytes() == b"classifier-v1"
    assert bundle.detector_path.suffix == ".pt"
    assert bundle.classifier_path.suffix == ".pt"
    assert bundle.labels_path.suffix == ".txt"
    assert bundle.detector_path.parent == bundle.classifier_path.parent


def test_corrupt_artifact_is_rejected_before_model_loading(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, version="1.2.3", classifier=b"classifier-v1")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["classifier"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    loader = ManifestBundleLoader(
        readers={"file": LocalArtifactReader()},
        cache_dir=tmp_path / "cache",
    )

    with pytest.raises(ArtifactChecksumError, match="classifier"):
        loader.load(manifest_path.as_uri(), device="cpu")


def test_model_replacement_requires_configuration_change_only(tmp_path: Path) -> None:
    first = write_manifest(tmp_path, version="1.2.3", classifier=b"classifier-v1")
    second = write_manifest(tmp_path, version="2.0.0", classifier=b"classifier-v2")
    loader = ManifestBundleLoader(
        readers={"file": LocalArtifactReader()},
        cache_dir=tmp_path / "cache",
    )

    first_bundle = load_configured_bundle(loader, {"MODEL_MANIFEST_URI": first.as_uri()})
    second_bundle = load_configured_bundle(loader, {"MODEL_MANIFEST_URI": second.as_uri()})

    assert first_bundle.model_version == "1.2.3"
    assert second_bundle.model_version == "2.0.0"
    assert first_bundle.classifier_path != second_bundle.classifier_path
    assert second_bundle.classifier_path.read_bytes() == b"classifier-v2"


def test_manifest_rejects_incompatible_versions_and_implicit_devices(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, version="latest", classifier=b"classifier")
    loader = ManifestBundleLoader(
        readers={"file": LocalArtifactReader()},
        cache_dir=tmp_path / "cache",
    )

    with pytest.raises(ManifestValidationError, match="semantic version"):
        loader.load(manifest_path.as_uri(), device="cpu")
    with pytest.raises(ManifestValidationError, match="device"):
        loader.load(manifest_path.as_uri(), device="auto")


def test_manifest_gives_extensionless_artifacts_runtime_compatible_suffixes(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, version="1.2.3", classifier=b"classifier")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    extensionless = tmp_path / "detector"
    extensionless.write_bytes(b"detector-v1")
    document["detector"]["uri"] = str(extensionless)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    loader = ManifestBundleLoader(
        readers={"file": LocalArtifactReader()},
        cache_dir=tmp_path / "cache",
    )

    bundle = loader.load(manifest_path.as_uri(), device="cpu")

    assert bundle.detector_path.suffix == ".pt"
    assert bundle.classifier_path.suffix == ".pt"
    assert bundle.labels_path.suffix == ".txt"


class FakeBody:
    def read(self) -> bytes:
        return b"artifact"


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeBody]:
        self.calls.append((Bucket, Key))
        return {"Body": FakeBody()}


def test_s3_reader_uses_injected_client_without_network() -> None:
    client = FakeS3Client()

    assert S3ArtifactReader(client).read("s3://models/releases/manifest.json") == b"artifact"
    assert client.calls == [("models", "releases/manifest.json")]
