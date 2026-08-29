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


def test_deterministic_entrypoint_uses_maximum_species_count_per_frame(tmp_path: Path) -> None:
    frames = [b"cat-one", b"cat-two", b"cattle-one", b"cattle-two", b"cattle-three"]
    frame_paths = []
    predictions: dict[str, list[dict[str, object]]] = {}
    for index, frame in enumerate(frames):
        path = tmp_path / f"frame-{index}.jpg"
        path.write_bytes(frame)
        frame_paths.append(path)
        species_index = 0 if index < 2 else 1
        detection_count = 1 if index < 2 else 6
        predictions[digest(frame)] = [
            {"class_index": species_index, "confidence": 0.99}
            for _ in range(detection_count)
        ]

    artifacts = {
        "detector": json.dumps({"inputs": predictions}).encode("utf-8"),
        "classifier": b'{"format":"deterministic-fixture-v1"}',
        "labels": b"Felis_catus\nBos_taurus\n",
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
        input_uris=[path.as_uri() for path in frame_paths],
        cache_dir=tmp_path / "cache",
    )

    assert result == {
        "model_version": "3.1.4",
        "tag_counts": {"Felis_catus": 1, "Bos_taurus": 6},
    }


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
