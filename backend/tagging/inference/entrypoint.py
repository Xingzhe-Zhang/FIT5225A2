from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from .manifest import (
    LocalArtifactReader,
    ManifestBundleLoader,
    ManifestValidationError,
    load_configured_bundle,
)


def run_deterministic_inference(
    *,
    input_uris: Sequence[str],
    cache_dir: Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run the local deterministic adapter used for tests and offline demos.

    This adapter deliberately does not execute supplied PyTorch weights. Its JSON
    artifact maps input SHA-256 values to repeatable class predictions, while the
    same manifest loader/checksum/cache path is shared with a production backend.
    """

    reader = LocalArtifactReader()
    bundle = load_configured_bundle(
        ManifestBundleLoader(readers={"file": reader}, cache_dir=cache_dir),
        environ,
    )
    try:
        classifier_config = json.loads(bundle.classifier_path.read_text(encoding="utf-8"))
        if classifier_config.get("format") != "deterministic-fixture-v1":
            raise ValueError("unsupported classifier format")
        detector_config = json.loads(bundle.detector_path.read_text(encoding="utf-8"))
        predictions = detector_config["inputs"]
        if not isinstance(predictions, dict):
            raise TypeError("inputs must be an object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ManifestValidationError(f"invalid deterministic model artifact: {exc}") from exc

    counts: Counter[str] = Counter()
    for uri in input_uris:
        frame_counts: Counter[str] = Counter()
        input_digest = hashlib.sha256(reader.read(uri)).hexdigest()
        for prediction in predictions.get(input_digest, []):
            try:
                class_index = prediction["class_index"]
                confidence = prediction["confidence"]
                if isinstance(class_index, bool) or not isinstance(class_index, int):
                    raise TypeError("class_index must be an integer")
                if not 0 <= class_index < len(bundle.labels):
                    raise IndexError("class_index is outside manifest labels")
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                    raise TypeError("confidence must be numeric")
                if confidence < bundle.classification_threshold:
                    continue
                frame_counts[bundle.labels[class_index]] += 1
            except (KeyError, TypeError, IndexError) as exc:
                raise ManifestValidationError(f"invalid deterministic prediction: {exc}") from exc
        for species, frame_count in frame_counts.items():
            counts[species] = max(counts[species], frame_count)
    return {"model_version": bundle.model_version, "tag_counts": dict(counts)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline deterministic model entrypoint")
    parser.add_argument("--input-uri", action="append", required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path(".model-cache"))
    args = parser.parse_args(argv)
    result = run_deterministic_inference(
        environ=os.environ,
        input_uris=args.input_uri,
        cache_dir=args.cache_dir,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
