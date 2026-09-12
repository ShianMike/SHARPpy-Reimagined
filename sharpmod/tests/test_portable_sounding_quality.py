"""Which physical problems may stop a portable sounding being opened at all."""

from __future__ import annotations

import json

import numpy as np
import pytest

from sharpmod import portable_sounding as ps

LEVELS = 12


def _columns() -> dict[str, np.ndarray]:
    """A clean, strictly ordered profile with a realistic dewpoint depression."""
    pres = np.linspace(1000.0, 200.0, LEVELS)
    hght = np.linspace(100.0, 12_000.0, LEVELS)
    tmpc = np.linspace(24.0, -60.0, LEVELS)
    return {
        "pres": pres,
        "hght": hght,
        "tmpc": tmpc,
        "dwpc": tmpc - 4.0,
        "wdir": np.full(LEVELS, 240.0),
        "wspd": np.linspace(5.0, 60.0, LEVELS),
        "omeg": np.zeros(LEVELS),
    }


def _write_pair(tmp_path, columns):
    path = tmp_path / "sounding.npz"
    np.savez(
        path,
        valid="2026-05-01 18:00",
        run="2026-05-01 12:00",
        loc="Test Point",
        lat=35.18,
        lon=-97.44,
        **columns,
    )
    path.with_suffix(".json").write_text(
        json.dumps({"loc": "Test Point"}), encoding="utf-8"
    )
    return path


def test_a_clean_profile_is_accepted(tmp_path):
    """Guards the fixture itself, so the tests below mean what they say."""
    assert ps.validate_portable_sounding_pair(_write_pair(tmp_path, _columns()))


def test_a_dewpoint_above_temperature_aloft_still_opens(tmp_path):
    """Contaminated is not unusable.

    A humidity sensor reading above saturation near the top of the troposphere
    is real recorded data. Refusing the archive meant the sounding could not be
    inspected at all, which hid the contamination rather than showing it.
    """
    columns = _columns()
    # Around 10 km, where this genuinely shows up.
    columns["dwpc"][-3] = columns["tmpc"][-3] + 2.5

    assert ps.validate_portable_sounding_pair(_write_pair(tmp_path, columns))
    assert "dewpoint_above_temperature" in ps._physical_profile_issues(columns), \
        "it still has to be reported, just not fatally"


def test_the_issue_is_classified_as_advisory_rather_than_dropped(tmp_path):
    """The distinction is the whole fix, so pin it directly."""
    assert "dewpoint_above_temperature" in ps.ADVISORY_ISSUES
    assert ps.fatal_issues(("dewpoint_above_temperature",)) == ()
    assert ps.fatal_issues(
        ("dewpoint_above_temperature", "negative_wind_speed")
    ) == ("negative_wind_speed",)


@pytest.mark.parametrize(
    ("field", "mutate", "code"),
    [
        ("pres", lambda column: column.__setitem__(5, column[4] + 10.0),
         "pressure_not_strictly_decreasing"),
        ("hght", lambda column: column.__setitem__(5, column[4] - 10.0),
         "height_not_strictly_increasing"),
        ("wspd", lambda column: column.__setitem__(3, -12.0),
         "negative_wind_speed"),
        ("wdir", lambda column: column.__setitem__(3, 512.0),
         "wind_direction_out_of_range"),
    ],
)
def test_data_no_calculation_can_consume_is_still_refused(
        tmp_path, field, mutate, code):
    """Relaxing the dewpoint rule must not relax the structural ones."""
    columns = _columns()
    mutate(columns[field])

    with pytest.raises(ValueError, match=code):
        ps.validate_portable_sounding_pair(_write_pair(tmp_path, columns))


def test_a_contaminated_dewpoint_does_not_mask_a_fatal_problem(tmp_path):
    """The advisory code must not be the one reported when both are present."""
    columns = _columns()
    columns["dwpc"][-3] = columns["tmpc"][-3] + 2.5
    columns["wspd"][3] = -12.0

    with pytest.raises(ValueError) as failure:
        ps.validate_portable_sounding_pair(_write_pair(tmp_path, columns))

    message = str(failure.value)
    assert "negative_wind_speed" in message
    assert "dewpoint_above_temperature" not in message
