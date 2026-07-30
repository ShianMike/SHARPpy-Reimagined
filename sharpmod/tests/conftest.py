"""Shared pytest / Hypothesis configuration for the SHARPpy Reimagined test suite.

Registers full and fast Hypothesis settings profiles. The full profile remains
the default and pins a minimum of 100 examples per property test, satisfying
the design's correctness requirement. ``SHARPMOD_HYPOTHESIS_PROFILE=fast`` is
an explicit local/CI feedback lane with 10 examples; it never replaces the full
property lane.

Because this profile is loaded here (at collection time), any test module that
uses ``@given(profiles(...))`` automatically runs at least 100 examples without
needing to configure Hypothesis itself. Individual tests may still raise the
count with their own ``@settings(max_examples=...)`` decorator.
"""

from __future__ import annotations

import os

# Select Qt's headless platform before pytest imports any GUI test modules.
# Module-local ``setdefault`` calls are too late when an earlier collection
# import has already loaded ``sharpmod.gui_common`` on Windows.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Unit and render-smoke tests must never depend on the public geocoder. Tests
# that exercise reverse lookup replace this default with a controlled endpoint.
os.environ.setdefault("SHARPMOD_GEOCODER_URL", "off")

from hypothesis import HealthCheck, is_hypothesis_test, settings

#: Full correctness and short feedback profile sizes.
FULL_MAX_EXAMPLES = 100
FAST_MAX_EXAMPLES = 10


def _profile(max_examples):
    return settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.data_too_large,
        ],
    )


settings.register_profile("sharpmod-full", _profile(FULL_MAX_EXAMPLES))
settings.register_profile("sharpmod-fast", _profile(FAST_MAX_EXAMPLES))

_requested_profile = os.environ.get(
    "SHARPMOD_HYPOTHESIS_PROFILE", "full"
).strip().casefold()
if _requested_profile not in {"fast", "full"}:
    raise RuntimeError(
        "SHARPMOD_HYPOTHESIS_PROFILE must be either 'fast' or 'full'"
    )
settings.load_profile(f"sharpmod-{_requested_profile}")


def pytest_collection_modifyitems(items):
    """Mark Hypothesis tests by behavior instead of filename convention."""
    for item in items:
        if is_hypothesis_test(getattr(item, "obj", None)):
            item.add_marker("property")
