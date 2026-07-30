"""Opt-in, timeout-bounded checks against current public provider contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import numpy as np
import pytest

from sharpmod import eccc_geomet
from sharpmod.eccc_geomet import latest_reference_time
from sharpmod.tools import model_extract


pytestmark = [
    pytest.mark.live_provider,
    pytest.mark.skipif(
        os.environ.get("SHARPMOD_RUN_LIVE_PROVIDER_TESTS") != "1",
        reason="live provider checks are opt-in",
    ),
]


def test_eccc_gdps_advertises_a_recent_reference_time():
    """The real GeoMet adapter still exposes a current GDPS model cycle."""
    reference = latest_reference_time("gdps", cancelled=lambda: False)
    now = datetime.now(timezone.utc)

    assert reference.tzinfo is not None
    assert now - timedelta(days=7) <= reference <= now + timedelta(hours=6)


@pytest.mark.timeout(600)
def test_live_eccc_denver_surface_prunes_subterrain_requests():
    """Exercise the real surface-first GeoMet request plan at high terrain."""
    point = eccc_geomet.fetch_point(
        "gdps",
        39.7392,
        -104.9903,
        fxx=0,
        max_workers=8,
    )
    pressure = np.asarray(point.columns["pres"], dtype=float)
    height = np.asarray(point.columns["hght"], dtype=float)
    unpruned_requests = (
        6
        + 5 * len(point.capability.pressure_levels)
        + len(point.capability.omega_levels)
    )

    assert point.surface_merged
    assert point.below_ground_levels_removed >= 1
    assert pressure[0] == pytest.approx(point.surface_pressure_hpa)
    assert 780.0 < pressure[0] < 900.0
    assert 1400.0 < height[0] < 2000.0
    assert np.all(pressure[1:] < pressure[0])
    assert point.request_count < unpruned_requests


@pytest.mark.timeout(600)
def test_live_hrrr_denver_profile_starts_at_verified_ground(
        tmp_path, monkeypatch):
    """Catch provider-schema drift and the original high-terrain regression."""
    monkeypatch.setenv("SHARPMOD_BACKEND", "python")
    monkeypatch.setenv("SHARPMOD_HRRR_BACKEND", "zarr")
    now = datetime.now(timezone.utc)
    failures = []
    output = tmp_path / "hrrr-denver.npz"

    # The current wall-clock cycle can precede archive publication.  Try a
    # bounded set of completed analyses without weakening extraction errors.
    for age_hours in range(2, 8):
        try:
            model_extract.extract(
                "hrrr",
                39.7392,
                -104.9903,
                run_time=now - timedelta(hours=age_hours),
                fxx=0,
                out_path=output,
                loc="Denver live contract",
            )
            break
        except model_extract.RetrievalError as exc:
            failures.append(str(exc))
    else:
        pytest.fail(
            "no recent HRRR analysis passed the verified-surface contract: "
            + " | ".join(failures)
        )

    metadata = json.loads(
        output.with_suffix(".json").read_text(encoding="utf-8")
    )
    with np.load(output, allow_pickle=False) as payload:
        pressure = np.asarray(payload["pres"], dtype=float)
        height = np.asarray(payload["hght"], dtype=float)

    assert metadata["surface_merged"] is True
    assert metadata["below_ground_levels_removed"] >= 1
    assert pressure[0] == pytest.approx(metadata["surface_pressure_hpa"])
    assert 780.0 < pressure[0] < 900.0
    assert 1400.0 < height[0] < 1900.0
    assert np.all(pressure[1:] < pressure[0])
    assert np.all(np.diff(pressure) < 0.0)
    assert np.all(np.diff(height) > 0.0)
