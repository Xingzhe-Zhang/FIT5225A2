from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.tagging.inference.entrypoint import run_deterministic_inference
from backend.tagging.inference.manifest import ManifestValidationError


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_deterministic_entrypoint_loads_manifest_and_predicts_local_input(tmp_path: Path) -> None:
    image = b"representative-image-fixture"
    image_path = tmp_path / "camera.jpg"
    image_path.write_bytes(image)
    detector = json.dumps(
        {
            "inputs": {
                digest(image): [
                    {"class_index": 0, "confidence": 0.91},
                    {"class_index": 0, "confidence": 0.89},
                    {"class_index": 1, "confidence": 0.20},
                ]
            }
        }
    ).encode("utf-8")
    classifier = b'{"format":"deterministic-fixture-v1"}'
    labels = b"dingo\nwombat\n"
    artifacts = {
        "detector": detector,
        "classifier": classifier,
        "labels": labels,
    }
    for name, value in artifacts.items():
        (tmp_path / name).write_bytes(value)
    manifest = {
        "schema_version": "1.0",
        "model_version": "3.1.4",
        **{
            name: {"uri": str(tmp_path / name), "sha256": digest(value)}
            for name, value in artifacts.items()
        },
        "input": {"width": 480, "height": 480},
        "thresholds": {"detection": 0.2, "classification": 0.5},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_deterministic_inference(
        environ={"MODEL_MANIFEST_URI": manifest_path.as_uri()},
        input_uris=[image_path.as_uri()],
        cache_dir=tmp_path / "cache",
    )

    assert result == {"model_version": "3.1.4", "tag_counts": {"dingo": 2}}


def test_deterministic_entrypoint_rejects_negative_class_index(tmp_path: Path) -> None:
    image = b"negative-index-fixture"
    image_path = tmp_path / "camera.jpg"
    image_path.write_bytes(image)
    detector = json.dumps(
        {"inputs": {digest(image): [{"class_index": -1, "confidence": 0.91}]}}
    ).encode("utf-8")
    classifier = b'{"format":"deterministic-fixture-v1"}'
    labels = b"dingo\nwombat\n"
    artifacts = {
        "detector": detector,
        "classifier": classifier,
        "labels": labels,
    }
    for name, value in artifacts.items():
        (tmp_path / name).write_bytes(value)
    manifest = {
        "schema_version": "1.0",
        "model_version": "3.1.4",
        **{
            name: {"uri": str(tmp_path / name), "sha256": digest(value)}
            for name, value in artifacts.items()
        },
        "input": {"width": 480, "height": 480},
        "thresholds": {"detection": 0.2, "classification": 0.5},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="class_index"):
        run_deterministic_inference(
            environ={"MODEL_MANIFEST_URI": manifest_path.as_uri()},
            input_uris=[image_path.as_uri()],
            cache_dir=tmp_path / "cache",
        )
