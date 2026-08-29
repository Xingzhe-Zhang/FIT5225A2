import os
from pathlib import Path

import backend.tagging.inference.local_runtime as local_runtime
from backend.common.providers.fakes import InMemoryObjectStorage
from backend.common.providers.interfaces import InferenceResult
from backend.tagging.inference.manifest import LoadedModelBundle
from backend.tagging.inference.local_runtime import (
    LocalWildlifeInferenceService,
    _load_labels,
    _temporary_environment,
)


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, bytes]]] = []

    def infer(self, inputs: list[tuple[str, bytes]]) -> InferenceResult:
        self.calls.append(inputs)
        return InferenceResult(tag_counts={"Bos_taurus": 2}, model_version="test-model")


def test_service_reads_storage_and_reuses_one_runtime(tmp_path: Path) -> None:
    storage = InMemoryObjectStorage()
    storage.put_bytes("temporary-query/one.jpg", b"image", content_type="image/jpeg")
    runtime = RecordingRuntime()
    service = LocalWildlifeInferenceService(
        storage=storage,
        model_dir=tmp_path,
        runtime_factory=lambda: runtime,
    )

    first = service.infer(["s3://media/temporary-query/one.jpg"])
    second = service.infer(["s3://media/temporary-query/one.jpg"])

    assert first == second == InferenceResult(tag_counts={"Bos_taurus": 2}, model_version="test-model")
    assert runtime.calls == [
        [("s3://media/temporary-query/one.jpg", b"image")],
        [("s3://media/temporary-query/one.jpg", b"image")],
    ]


def test_manifest_bundle_configures_runtime_paths_and_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    detector = tmp_path / "detector"
    classifier = tmp_path / "classifier"
    labels = tmp_path / "labels"
    for path in (detector, classifier, labels):
        path.write_bytes(b"artifact")
    bundle = LoadedModelBundle(
        model_version="2.0.0",
        detector_path=detector,
        classifier_path=classifier,
        labels_path=labels,
        labels=("Dingo",),
        input_width=320,
        input_height=256,
        detection_threshold=0.2,
        classification_threshold=0.6,
        device="cpu",
    )
    captured: dict[str, object] = {}
    runtime = RecordingRuntime()

    def build_runtime(**kwargs: object) -> RecordingRuntime:
        captured.update(kwargs)
        return runtime

    monkeypatch.setattr(local_runtime, "TorchWildlifeRuntime", build_runtime)
    service = LocalWildlifeInferenceService.from_manifest_bundle(
        storage=InMemoryObjectStorage(),
        bundle=bundle,
    )

    assert service._get_runtime() is runtime
    assert captured["detector_path"] == detector
    assert captured["classifier_path"] == classifier
    assert captured["labels_path"] == labels
    assert captured["model_version"] == "2.0.0"
    assert captured["input_width"] == 320
    assert captured["input_height"] == 256


def test_taxonomy_file_maps_to_classifier_species_names(tmp_path: Path) -> None:
    labels = tmp_path / "labels.txt"
    labels.write_text(
        "id;mammalia;order;family;bos;taurus;cattle\n"
        "id;aves;order;family;casuarius;casuarius;southern cassowary\n",
        encoding="utf-8",
    )

    assert _load_labels(labels) == ("Bos_taurus", "Casuarius_casuarius")


def test_temporary_environment_restores_existing_values(monkeypatch) -> None:
    monkeypatch.setenv("PBA_EXISTING", "before")
    monkeypatch.delenv("PBA_NEW", raising=False)

    with _temporary_environment(PBA_EXISTING="during", PBA_NEW="created"):
        assert os.environ["PBA_EXISTING"] == "during"
        assert os.environ["PBA_NEW"] == "created"

    assert os.environ["PBA_EXISTING"] == "before"
    assert "PBA_NEW" not in os.environ
