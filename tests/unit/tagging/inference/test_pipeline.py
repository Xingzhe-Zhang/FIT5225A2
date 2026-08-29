from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.tagging.inference.pipeline import (
    Classification,
    Detection,
    ImageDecodeError,
    InferenceExecutionError,
    InferencePipeline,
)


@dataclass
class FakeImage:
    name: str

    def crop_and_resize(
        self,
        bbox: tuple[float, float, float, float],
        width: int,
        height: int,
    ) -> tuple[str, tuple[float, float, float, float], int, int]:
        return (self.name, bbox, width, height)


class FakeReader:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def read(self, uri: str) -> bytes:
        return self.values[uri]


class FakeDecoder:
    def decode(self, payload: bytes) -> FakeImage:
        if payload == b"corrupt":
            raise ValueError("not an image")
        return FakeImage(payload.decode("ascii"))


class FakeDetector:
    def detect(self, image: FakeImage, *, device: str) -> list[Detection]:
        assert device == "cpu"
        return {
            "frame-one": [
                Detection("1", 0.90, (0.0, 0.0, 0.5, 0.5)),
                Detection("2", 0.99, (0.0, 0.0, 0.5, 0.5)),
                Detection("1", 0.10, (0.5, 0.5, 0.5, 0.5)),
            ],
            "frame-two": [
                Detection("1", 0.80, (0.0, 0.0, 1.0, 1.0)),
                Detection("1", 0.70, (0.1, 0.1, 0.4, 0.4)),
            ],
            "empty": [],
        }[image.name]


class FakeClassifier:
    def classify(self, crop: object, *, device: str) -> Classification:
        assert device == "cpu"
        name, bbox, width, height = crop
        assert (width, height) == (480, 480)
        if name == "frame-one":
            return Classification(class_index=0, confidence=0.95)
        if bbox == (0.0, 0.0, 1.0, 1.0):
            return Classification(class_index=1, confidence=0.90)
        return Classification(class_index=0, confidence=0.40)


def make_pipeline(values: dict[str, bytes]) -> InferencePipeline:
    return InferencePipeline(
        object_reader=FakeReader(values),
        decoder=FakeDecoder(),
        detector=FakeDetector(),
        classifier=FakeClassifier(),
        labels=("dingo", "wombat"),
        model_version="1.2.3",
        input_width=480,
        input_height=480,
        detection_threshold=0.2,
        classification_threshold=0.5,
        device="cpu",
    )


def test_video_inputs_aggregate_one_count_per_accepted_animal_detection() -> None:
    pipeline = make_pipeline({"frame://1": b"frame-one", "frame://2": b"frame-two"})

    detailed = pipeline.infer_detailed(["frame://1", "frame://2"])

    assert detailed.tag_counts == {"dingo": 1, "wombat": 1}
    assert [item.source_uri for item in detailed.evidence] == ["frame://1", "frame://2"]
    assert pipeline.infer(["frame://1", "frame://2"]).tag_counts == {
        "dingo": 1,
        "wombat": 1,
    }


def test_video_inputs_use_maximum_species_count_from_any_single_frame() -> None:
    frame_counts = {
        "cat-one": 1,
        "cat-two": 1,
        "cattle-one": 6,
        "cattle-two": 6,
        "cattle-three": 6,
    }

    class RepeatedAnimalDetector:
        def detect(self, image: FakeImage, *, device: str) -> list[Detection]:
            assert device == "cpu"
            return [
                Detection("1", 0.95, (0.0, 0.0, 1.0, 1.0))
                for _ in range(frame_counts[image.name])
            ]

    class FrameSpeciesClassifier:
        def classify(self, crop: object, *, device: str) -> Classification:
            assert device == "cpu"
            frame_name, _, _, _ = crop
            class_index = 0 if frame_name.startswith("cat-") else 1
            return Classification(class_index=class_index, confidence=0.99)

    values = {
        f"frame://{index}": frame_name.encode("ascii")
        for index, frame_name in enumerate(frame_counts)
    }
    pipeline = InferencePipeline(
        object_reader=FakeReader(values),
        decoder=FakeDecoder(),
        detector=RepeatedAnimalDetector(),
        classifier=FrameSpeciesClassifier(),
        labels=("Felis_catus", "Bos_taurus"),
        model_version="1.2.3",
        input_width=480,
        input_height=480,
        detection_threshold=0.2,
        classification_threshold=0.5,
        device="cpu",
    )

    result = pipeline.infer(list(values))

    assert result.tag_counts == {"Felis_catus": 1, "Bos_taurus": 6}


def test_no_animal_returns_defined_empty_result() -> None:
    result = make_pipeline({"image://empty": b"empty"}).infer(["image://empty"])
    assert result.tag_counts == {}
    assert result.model_version == "1.2.3"


def test_corrupt_input_has_clear_source_specific_failure() -> None:
    with pytest.raises(ImageDecodeError, match="image://broken"):
        make_pipeline({"image://broken": b"corrupt"}).infer(["image://broken"])


def test_classifier_index_must_exist_in_manifest_labels() -> None:
    class InvalidClassifier(FakeClassifier):
        def classify(self, crop: object, *, device: str) -> Classification:
            return Classification(class_index=99, confidence=0.99)

    pipeline = make_pipeline({"image://one": b"frame-one"})
    pipeline.classifier = InvalidClassifier()

    with pytest.raises(InferenceExecutionError, match="class index 99"):
        pipeline.infer(["image://one"])


def test_classifier_rejects_negative_class_index() -> None:
    class NegativeIndexClassifier(FakeClassifier):
        def classify(self, crop: object, *, device: str) -> Classification:
            return Classification(class_index=-1, confidence=0.99)

    pipeline = make_pipeline({"image://one": b"frame-one"})
    pipeline.classifier = NegativeIndexClassifier()

    with pytest.raises(InferenceExecutionError, match="class index -1"):
        pipeline.infer(["image://one"])
