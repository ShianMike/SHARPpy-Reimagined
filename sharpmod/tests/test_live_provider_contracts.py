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


# A bounded terrain/geography matrix for the arbitrary-point HRRR surface
# contract. These are validation anchors, not special cases in production
# code: every requested CONUS point follows the same nearest-gridpoint path.
_CONUS_HRRR_SURFACE_POINTS = (
    ("Seattle-Pacific-Northwest", 47.6062, -122.3321, -100.0, 500.0, 0),
    ("Salt-Lake-Intermountain", 40.7608, -111.8910, 900.0, 2000.0, 1),
    ("Denver-Rockies", 39.7392, -104.9903, 1400.0, 2000.0, 1),
    ("Norman-Central-Plains", 35.1818, -97.4395, 200.0, 600.0, 0),
    ("Miami-Southeast-Coast", 25.7617, -80.1918, -100.0, 200.0, 0),
    ("New-York-Northeast", 40.7128, -74.0060, -100.0, 400.0, 0),
)


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


def _assert_hrrr_surface_contract(
        output, *, label, latitude, longitude, minimum_height,
        maximum_height, minimum_removed):
    metadata = json.loads(
        output.with_suffix(".json").read_text(encoding="utf-8")
    )
    with np.load(output, allow_pickle=False) as payload:
        pressure = np.asarray(payload["pres"], dtype=float)
        height = np.asarray(payload["hght"], dtype=float)

    assert metadata["surface_merged"] is True, label
    assert metadata["surface_contract_version"] == 1, label
    assert metadata["below_ground_levels_removed"] >= minimum_removed, label
    assert pressure[0] == pytest.approx(metadata["surface_pressure_hpa"]), label
    assert 500.0 < pressure[0] < 1050.0, label
    assert minimum_height < height[0] < maximum_height, label
    assert metadata["selected_lat"] == pytest.approx(latitude, abs=0.1), label
    assert metadata["selected_lon"] == pytest.approx(longitude, abs=0.1), label
    assert np.all(pressure[1:] < pressure[0]), label
    assert np.all(np.diff(pressure) < 0.0), label
    assert np.all(np.diff(height) > 0.0), label


@pytest.mark.timeout(900)
def test_live_hrrr_conus_profiles_start_at_local_verified_ground(
        tmp_path, monkeypatch):
    """Catch provider drift across representative CONUS terrain and regions."""
    monkeypatch.setenv("SHARPMOD_BACKEND", "python")
    monkeypatch.setenv("SHARPMOD_HRRR_BACKEND", "zarr")
    now = datetime.now(timezone.utc)
    failures = []
    first = _CONUS_HRRR_SURFACE_POINTS[0]
    first_output = tmp_path / f"hrrr-{first[0].casefold()}.npz"
    selected_run = None

    # The current wall-clock cycle can precede archive publication.  Try a
    # bounded set of completed analyses at the first point, then use that one
    # cycle for the rest of the matrix so every region tests identical source
    # metadata without multiplying publication probes.
    for age_hours in range(2, 8):
        candidate_run = now - timedelta(hours=age_hours)
        try:
            model_extract.extract(
                "hrrr",
                first[1],
                first[2],
                run_time=candidate_run,
                fxx=0,
                out_path=first_output,
                loc=f"{first[0]} live surface contract",
                live_regional_guidance=False,
            )
            selected_run = candidate_run
            break
        except model_extract.RetrievalError as exc:
            failures.append(str(exc))
    else:
        pytest.fail(
            "no recent HRRR analysis passed the verified-surface contract: "
            + " | ".join(failures)
        )

    _assert_hrrr_surface_contract(
        first_output,
        label=first[0],
        latitude=first[1],
        longitude=first[2],
        minimum_height=first[3],
        maximum_height=first[4],
        minimum_removed=first[5],
    )
    for point in _CONUS_HRRR_SURFACE_POINTS[1:]:
        label, latitude, longitude, min_height, max_height, min_removed = point
        output = tmp_path / f"hrrr-{label.casefold()}.npz"
        try:
            model_extract.extract(
                "hrrr",
                latitude,
                longitude,
                run_time=selected_run,
                fxx=0,
                out_path=output,
                loc=f"{label} live surface contract",
                live_regional_guidance=False,
            )
        except model_extract.RetrievalError as exc:
            pytest.fail(f"{label} failed the shared live HRRR cycle: {exc}")
        _assert_hrrr_surface_contract(
            output,
            label=label,
            latitude=latitude,
            longitude=longitude,
            minimum_height=min_height,
            maximum_height=max_height,
            minimum_removed=min_removed,
        )
