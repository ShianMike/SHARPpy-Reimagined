"""Observed VWP import, coverage, comparison, and persistence contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.wind_profiles import (
    MPS_TO_KT,
    ObservedWindProfile,
    WindLevel,
    WindProfileError,
    WindProfileCancelled,
    WindProfileSeries,
    compare_model_winds,
    export_wind_profile_csv,
    import_level3,
    layer_diagnostics,
    read_wind_profile_csv,
    read_wind_profiles,
    recent_vwp_url,
    write_wind_profiles,
)


FIXTURE = Path(__file__).parent / "data" / "klot_vwp_20260913_0146.csv"
UTC = timezone.utc


def test_real_klot_product_48_tabular_fixture_imports_as_wind_only():
    profile = read_wind_profile_csv(FIXTURE)

    assert profile.radar_id == "KLOT"
    assert profile.observed_at == datetime(2026, 9, 13, 1, 46, 22, tzinfo=UTC)
    assert profile.height_reference == "MSL"
    assert len(profile.levels) == 20
    assert profile.coverage_msl_m == pytest.approx((304.8, 3352.8))
    assert profile.coverage_agl_m == pytest.approx((73.152, 3121.152))
    assert profile.levels[0].u_kt == pytest.approx(9.177, abs=0.02)
    assert not hasattr(profile, "temperature"), "wind-only data grew thermodynamics"


def test_supported_layer_diagnostics_require_coverage_and_name_storm_motion():
    profile = read_wind_profile_csv(FIXTURE)
    diagnostics = layer_diagnostics(
        profile,
        100.0,
        2800.0,
        storm_motion=(20.0, 10.0, "Bunkers right mover from model sounding"),
    )

    assert diagnostics.reason is None
    assert diagnostics.shear_magnitude_kt is not None
    assert diagnostics.storm_relative_helicity_m2_s2 is not None
    assert diagnostics.usable_level_count >= 3
    assert diagnostics.storm_motion_source == "Bunkers right mover from model sounding"


def test_storm_relative_values_stay_unknown_without_storm_motion():
    profile = read_wind_profile_csv(FIXTURE)
    diagnostics = layer_diagnostics(profile, 100.0, 2800.0)
    assert diagnostics.shear_magnitude_kt is not None
    assert diagnostics.storm_relative_helicity_m2_s2 is None
    assert "explicit storm motion" in diagnostics.reason


def test_incomplete_coverage_cannot_look_like_complete_layer_diagnostics():
    profile = ObservedWindProfile(
        "KOUN",
        35.22,
        -97.44,
        350.0,
        datetime(2026, 5, 20, 18, tzinfo=UTC),
        (
            WindLevel(500.0, 150.0, 10.0, 5.0, rms_kt=2.0),
            WindLevel(1000.0, 650.0, 20.0, 8.0, rms_kt=2.0),
        ),
    )
    diagnostics = layer_diagnostics(profile, 0.0, 6000.0)
    assert diagnostics.shear_magnitude_kt is None
    assert diagnostics.storm_relative_helicity_m2_s2 is None
    assert "coverage" in diagnostics.reason


def test_model_comparison_is_model_minus_observed_and_keeps_gaps():
    observed = read_wind_profile_csv(FIXTURE)
    heights = np.asarray([item.height_msl_m for item in observed.levels])
    u_wind = np.asarray([item.u_kt for item in observed.levels]) + 2.0
    v_wind = np.asarray([item.v_kt for item in observed.levels]) - 3.0
    model = SimpleNamespace(hght=heights, u=u_wind, v=v_wind)

    differences = compare_model_winds(observed, model)

    assert [item.u_error_kt for item in differences] == pytest.approx(
        [2.0] * len(differences)
    )
    assert [item.v_error_kt for item in differences] == pytest.approx(
        [-3.0] * len(differences)
    )


def test_portable_json_and_exported_csv_reopen(tmp_path):
    profile = read_wind_profile_csv(FIXTURE)
    json_path = write_wind_profiles(tmp_path / "vwp.json", (profile,))
    csv_path = export_wind_profile_csv(tmp_path / "vwp.csv", profile)

    assert read_wind_profiles(json_path) == (profile,)
    assert read_wind_profile_csv(csv_path) == profile


def test_series_steps_in_time_order():
    first = read_wind_profile_csv(FIXTURE)
    second = ObservedWindProfile.from_dict(
        {**first.as_dict(), "observed_at": "2026-09-13T01:53:00Z"}
    )
    series = WindProfileSeries((second, first))
    assert series.current is first
    assert series.step(1) is second
    assert series.step(99) is second


def test_recent_url_is_public_product_48_path():
    assert recent_vwp_url("KLOT").endswith("/DS.48vwp/SI.klot/sn.last")
    with pytest.raises(WindProfileError):
        recent_vwp_url("LOT")


def test_recent_retrieval_cancels_between_bounded_reads():
    class _Response:
        def __init__(self):
            self.reads = 0
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def read(self, _size):
            self.reads += 1
            return b"partial product"

    response = _Response()

    with pytest.raises(WindProfileCancelled, match="cancelled"):
        from sharpmod.wind_profiles import fetch_recent_vwp

        fetch_recent_vwp(
            "KLOT",
            opener=lambda _request, timeout: response,
            cancel=lambda: response.reads >= 1,
        )

    assert response.reads == 1
    assert response.closed


def test_level3_decoder_reads_tabular_units_and_deduplicates_height(
    monkeypatch, tmp_path
):
    class _Level3:
        header = SimpleNamespace(code=48)
        siteID = "LOT"
        lat = 41.604
        lon = -88.085
        height = 760.0
        metadata = {"vol_time": datetime(2026, 9, 13, 1, 46, 22)}
        tab_pages = [
            "ALT U V W DIR SPD RMS DIV SRNG ELEV\n"
            "010 4.7 -6.6 NA 325 016 7.4 NA 4.27 0.5\n"
            "010 4.8 -6.5 NA 326 016 2.0 NA 4.27 0.5\n"
            "020 5.5 -10.1 NA 332 022 2.3 NA 11.91 0.9"
        ]

        def __init__(self, _path):
            pass

    monkeypatch.setattr("metpy.io.Level3File", _Level3)
    source = tmp_path / "vwp.nids"
    source.write_bytes(b"representative stand-in")

    profile = import_level3(source)

    assert profile.radar_id == "KLOT"
    assert len(profile.levels) == 2
    assert profile.levels[0].u_kt == pytest.approx(4.8 * MPS_TO_KT)
    assert profile.levels[0].rms_kt == 2.0
    assert profile.levels[0].height_msl_m == pytest.approx(304.8)
    assert profile.levels[0].height_agl_m == pytest.approx(73.152)


def test_non_vwp_level3_product_is_rejected(monkeypatch, tmp_path):
    class _Level3:
        header = SimpleNamespace(code=94)

        def __init__(self, _path):
            pass

    monkeypatch.setattr("metpy.io.Level3File", _Level3)
    source = tmp_path / "not-vwp.nids"
    source.write_bytes(b"x")
    with pytest.raises(WindProfileError, match="product 48"):
        import_level3(source)
