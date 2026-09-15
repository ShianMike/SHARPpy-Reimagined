"""Comparison provenance and conservative interpolation contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sharpmod import vertical_interpolation
from sharpmod.comparison import (
    ComparisonContext,
    assess_compatibility,
    context_for_collection,
    vertical_profile_difference,
)


VALID = datetime(2026, 9, 13, 0, tzinfo=timezone.utc)


def _context(**changes):
    values = dict(
        collection_index=0,
        label="Reference",
        requested_time=VALID,
        selected_time=VALID,
        member="control",
        requested_lat=35.0,
        requested_lon=-97.0,
        selected_lat=35.0,
        selected_lon=-97.0,
        grid_spacing_km=3.0,
        terrain_elevation_m=320.0,
        run_time=VALID - timedelta(hours=6),
        lead_hours=6,
        parcel_convention="most-unstable-300-hPa",
        storm_motion_convention="Bunkers-right",
        profile_edited=False,
    )
    values.update(changes)
    return ComparisonContext(**values)


def test_spatial_comparison_remains_inspectable_and_discloses_basis():
    basis = assess_compatibility(
        _context(),
        _context(
            collection_index=1,
            label="Candidate",
            requested_lat=36.0,
            selected_lat=35.98,
            terrain_elevation_m=480.0,
        ),
        ("mlcape", "shear_6km"),
    )

    assert basis.compatible
    assert basis.location_relation == "intentional-spatial"
    assert any(issue.field == "terrain_elevation_m" for issue in basis.issues)


def test_convention_mismatch_blocks_only_affected_deltas():
    basis = assess_compatibility(
        _context(),
        _context(
            parcel_convention="surface",
            storm_motion_convention="user",
        ),
        ("mlcape", "shear_6km", "srh_1km"),
    )

    assert not basis.compatible
    assert basis.incompatible_metrics == {
        "mlcape": "parcel conventions differ",
        "srh_1km": "storm-motion conventions differ",
    }
    assert "shear_6km" not in basis.incompatible_metrics


def test_unknown_coordinates_remain_unknown():
    basis = assess_compatibility(
        _context(),
        _context(requested_lat=None, selected_lat=None),
    )

    assert basis.location_relation == "unknown"
    assert any(issue.severity == "unknown" for issue in basis.issues)


def test_nearest_time_is_never_treated_as_exact():
    basis = assess_compatibility(
        _context(),
        _context(selected_time=VALID + timedelta(minutes=30)),
    )

    assert not basis.compatible
    assert any(issue.field == "valid_time" for issue in basis.issues)


def test_vertical_difference_interpolates_only_through_bounded_gaps():
    reference = SimpleNamespace(
        hght=[100.0, 1000.0, 2000.0],
        pres=[1000.0, 900.0, 800.0],
        tmpc=[20.0, 10.0, 0.0],
        dwpc=[15.0, 5.0, -5.0],
        u=[0.0, 10.0, 20.0],
        v=[5.0, 15.0, 25.0],
    )
    candidate = SimpleNamespace(
        hght=[100.0, 1100.0, 4000.0],
        tmpc=[22.0, 12.0, -20.0],
        dwpc=[16.0, 6.0, -25.0],
        u=[2.0, 12.0, 40.0],
        v=[7.0, 17.0, 45.0],
    )

    difference = vertical_profile_difference(
        reference, candidate, max_gap_m=1500.0
    )

    assert difference.levels[0].temperature_c == pytest.approx(2.0)
    assert difference.levels[1].temperature_c == pytest.approx(3.0)
    assert difference.levels[2].temperature_c is None
    assert difference.levels[2].u_wind_kt is None


def test_vertical_difference_prepares_each_candidate_field_once(monkeypatch):
    heights = list(range(100, 10_100, 50))
    reference = SimpleNamespace(
        hght=heights,
        pres=list(range(1000, 1000 - len(heights), -1)),
        tmpc=heights,
        dwpc=heights,
        u=heights,
        v=heights,
    )
    candidate = SimpleNamespace(
        hght=[height + 10 for height in heights],
        tmpc=heights,
        dwpc=heights,
        u=heights,
        v=heights,
    )
    original = vertical_interpolation.np.argsort
    calls = []

    def counted(values, *args, **kwargs):
        calls.append(len(values))
        return original(values, *args, **kwargs)

    monkeypatch.setattr(vertical_interpolation.np, "argsort", counted)

    difference = vertical_profile_difference(reference, candidate)

    assert len(difference.levels) == len(heights) - 1
    assert calls == [len(heights)] * 4


def test_context_preserves_zero_metre_terrain_and_unknown_grid_spacing():
    profile = SimpleNamespace(
        pres=[1000.0],
        hght=[100.0],
    )
    collection = SimpleNamespace(
        _dates=[VALID],
        _prof_idx=0,
        _highlight="control",
        _profs={"control": [profile]},
        _meta={
            "model": "Unknown model",
            "terrain_elevation_m": 0.0,
            "requested_lat": 0.0,
            "requested_lon": 0.0,
        },
    )

    context = context_for_collection(
        collection, 0, requested_time=VALID, selected_time=VALID
    )

    assert context.terrain_elevation_m == 0.0
    assert context.grid_spacing_km is None
