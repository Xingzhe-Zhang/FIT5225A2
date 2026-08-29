"""Manifest-driven model loading and inference."""

from .manifest import LoadedModelBundle, ManifestBundleLoader, load_configured_bundle
from .pipeline import InferencePipeline, StructuredInferenceResult

__all__ = [
    "InferencePipeline",
    "LoadedModelBundle",
    "ManifestBundleLoader",
    "StructuredInferenceResult",
    "load_configured_bundle",
]
