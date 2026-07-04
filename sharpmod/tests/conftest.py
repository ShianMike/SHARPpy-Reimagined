"""Shared pytest / Hypothesis configuration for the SHARPpy Reimagined test suite.

Registers a single shared Hypothesis settings profile -- ``"sharpmod"`` -- and
loads it as the **default** for every test in the suite. The profile pins a
minimum of 100 examples per property test, satisfying the design's requirement
that each of the correctness properties run for >= 100 iterations
(Requirement 14.2 / tasks.md Notes).

Because this profile is loaded here (at collection time), any test module that
uses ``@given(profiles(...))`` automatically runs at least 100 examples without
needing to configure Hypothesis itself. Individual tests may still raise the
count with their own ``@settings(max_examples=...)`` decorator.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

#: Shared minimum number of Hypothesis examples per property test.
SHARED_MAX_EXAMPLES = 100

# Register the shared profile. ``data``-drawing composites that do a fair
# amount of per-example work can be slow, so the default deadline is disabled
# and the "too slow" / filter health checks are relaxed to keep large-profile
# generation from flaking the suite.
settings.register_profile(
    "sharpmod",
    settings(
        max_examples=SHARED_MAX_EXAMPLES,
        deadline=None,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.data_too_large,
        ],
    ),
)

# Load it as the default for the whole suite.
settings.load_profile("sharpmod")
