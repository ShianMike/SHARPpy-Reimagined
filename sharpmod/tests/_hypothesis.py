"""Helpers that preserve full property counts while bounding smoke lanes."""

from __future__ import annotations

import os

FAST_MAX_EXAMPLES = 10


def profiled_examples(full_max_examples: int) -> int:
    """Return the explicit example count for the selected suite profile."""

    full = int(full_max_examples)
    if full < FAST_MAX_EXAMPLES:
        raise ValueError("full_max_examples cannot be below the fast profile")
    profile = os.environ.get("SHARPMOD_HYPOTHESIS_PROFILE", "full")
    return FAST_MAX_EXAMPLES if profile.strip().casefold() == "fast" else full
