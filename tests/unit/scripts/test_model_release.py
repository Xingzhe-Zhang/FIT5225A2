from __future__ import annotations

import hashlib

from scripts.project_tasks import _model_release_manifest


def test_model_release_manifest_uses_versioned_s3_uris_and_real_checksums(tmp_path) -> None:
    artifacts = {
        "mdv5a.pt": b"detector",
        "model.pt": b"classifier",
        "labels.txt": b"dingo\n",
    }
    for name, payload in artifacts.items():
        (tmp_path / name).write_bytes(payload)

    manifest = _model_release_manifest("media-bucket", "2.0.0", tmp_path)

    assert manifest["model_version"] == "2.0.0"
    assert manifest["detector"] == {
        "uri": "s3://media-bucket/models/releases/2.0.0/mdv5a.pt",
        "sha256": hashlib.sha256(artifacts["mdv5a.pt"]).hexdigest(),
    }
    assert manifest["classifier"]["sha256"] == hashlib.sha256(artifacts["model.pt"]).hexdigest()
    assert manifest["labels"]["sha256"] == hashlib.sha256(artifacts["labels.txt"]).hexdigest()
