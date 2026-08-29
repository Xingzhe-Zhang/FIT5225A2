from __future__ import annotations

import re


_SEPARATORS = re.compile(r"[\s_]+")


def canonical_species_name(value: str) -> str:
    """Normalize display spaces and model underscores to one lookup key."""

    return _SEPARATORS.sub("_", value.strip()).casefold()
