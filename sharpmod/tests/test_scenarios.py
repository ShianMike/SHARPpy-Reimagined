"""Saved hypothetical scenario and sensitivity-grid contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from sharppy.sharptab import prof_collection, profile

from sharpmod.scenarios import (
    Perturbation,
    ProfileSnapshot,
    ScenarioBook,
    ScenarioError,
    read_scenario_book,
    run_sensitivity_grid,
    scenario_collection,
    scenario_from_cell,
    write_scenario_book,
)


VALID = datetime(2026, 9, 13, 0, tzinfo=timezone.utc)


def _profile():
    sounding = profile.create_profile(
        profile="raw",
        pres=[1000.0, 950.0, 900.0, 800.0],
        hght=[100.0, 500.0, 1000.0, 2000.0],
        tmpc=[25.0, 21.0, 17.0, 9.0],
        dwpc=[18.0, 15.0, 11.0, 1.0],
        wdir=[180.0, 190.0, 210.0, 230.0],
        wspd=[10.0, 15.0, 25.0, 35.0],
        location="KOUN",
        date=VALID,
        latitude=35.2,
        missing=-9999.0,
    )
    sounding.srwind = (20.0, 5.0, -10.0, 8.0)
    return sounding


class _Engine:
    def __init__(self):
        self.calls = 0

    def values(self, sounding, keys):
        self.calls += 1
        values = {
            "surface_temperature": float(sounding.tmpc[0]),
            "surface_dewpoint": float(sounding.dwpc[0]),
            "cap_temperature": float(sounding.tmpc[2]),
        }
        return {key: values.get(key) for key in keys}


def _book():
    return ScenarioBook(
        ProfileSnapshot.from_profile(_profile()),
        source={"model": "HRRR", "valid": "2026-09-13T00:00:00Z"},
    )


def test_named_scenario_applies_explicit_layers_without_mutating_baseline():
    book = _book()
    scenario = book.create(
        "Warmer and moister",
        (
            Perturbation(
                "surface_temperature", 2.0, "degC", interpolation="surface-only"
            ),
            Perturbation(
                "moisture_layer",
                4.0,
                "degC",
                bottom_m_agl=0.0,
                top_m_agl=1000.0,
                interpolation="linear-taper",
            ),
            Perturbation(
                "cap_temperature",
                3.0,
                "degC",
                bottom_m_agl=500.0,
                top_m_agl=1500.0,
                interpolation="triangle",
            ),
        ),
    )

    changed = book.profile(scenario.scenario_id)
    baseline = book.profile()

    assert float(changed.tmpc[0]) == pytest.approx(27.0)
    assert float(changed.dwpc[0]) == pytest.approx(22.0)
    assert float(changed.dwpc[1]) > float(baseline.dwpc[1])
    assert float(changed.dwpc[-1]) == float(baseline.dwpc[-1])
    assert float(changed.tmpc[2]) > float(baseline.tmpc[2])
    assert float(baseline.tmpc[0]) == 25.0
    assert "1000 m AGL" in scenario.perturbations[1].description


def test_storm_motion_delta_retains_other_vector_components():
    book = _book()
    scenario = book.create(
        "Faster storm",
        (
            Perturbation(
                "storm_motion",
                5.0,
                "kt",
                interpolation="vector-delta",
                secondary_amount=-2.0,
            ),
        ),
    )

    assert tuple(book.profile(scenario.scenario_id).srwind) == pytest.approx(
        (25.0, 3.0, -10.0, 8.0)
    )


def test_invalid_supersaturation_is_rejected_before_saving():
    book = _book()
    with pytest.raises(ScenarioError, match="dewpoint exceed temperature"):
        book.create(
            "Impossible",
            (
                Perturbation(
                    "surface_dewpoint",
                    10.0,
                    "degC",
                    interpolation="surface-only",
                ),
            ),
        )
    assert book.scenarios == ()


def test_create_rename_duplicate_reset_and_atomic_round_trip(tmp_path):
    book = _book()
    created = book.create(
        "Warm surface",
        (
            Perturbation(
                "surface_temperature", 2.0, "degC", interpolation="surface-only"
            ),
        ),
    )
    renamed = book.rename(created.scenario_id, "Warm sector")
    duplicate = book.duplicate(renamed.scenario_id, "Warm sector copy")
    book.reset(duplicate.scenario_id)
    path = write_scenario_book(tmp_path / "case.sharpmod-scenarios", book)

    restored = read_scenario_book(path)

    assert [item.name for item in restored.scenarios] == [
        "Warm sector",
        "Warm sector copy",
    ]
    assert restored.scenarios[1].perturbations == ()
    assert restored.source["model"] == "HRRR"
    assert not list(tmp_path.glob("*.tmp"))


def test_metric_deltas_are_cached_and_invalidated_after_change():
    book = _book()
    scenario = book.create(
        "Warm",
        (
            Perturbation(
                "surface_temperature", 2.0, "degC", interpolation="surface-only"
            ),
        ),
    )
    engine = _Engine()

    first = book.metric_deltas(engine, scenario.scenario_id, ("surface_temperature",))
    second = book.metric_deltas(engine, scenario.scenario_id, ("surface_temperature",))
    book.replace_perturbations(
        scenario.scenario_id,
        (
            Perturbation(
                "surface_temperature", 3.0, "degC", interpolation="surface-only"
            ),
        ),
    )
    third = book.metric_deltas(engine, scenario.scenario_id, ("surface_temperature",))

    assert first is second
    assert first[0].delta == pytest.approx(2.0)
    assert third[0].delta == pytest.approx(3.0)
    assert engine.calls == 4


def test_cancelled_sensitivity_grid_keeps_completed_cells_and_opens_one():
    book = _book()
    engine = _Engine()
    progress = []
    grid = run_sensitivity_grid(
        book,
        engine,
        (-1.0, 0.0, 1.0),
        (-1.0, 0.0, 1.0),
        ("surface_temperature", "surface_dewpoint"),
        cancelled=lambda: len(progress) >= 4,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert grid.cancelled
    assert grid.completed_cell_count == 4
    assert grid.requested_cell_count == 9
    cell = grid.cells[-1]
    scenario = scenario_from_cell(
        book, cell, moisture_depth_m_agl=grid.moisture_depth_m_agl
    )
    assert scenario.hypothetical
    assert len(scenario.perturbations) == 2


def test_scenario_collection_is_visibly_hypothetical():
    sounding = _profile()
    template = prof_collection.ProfCollection({"": [sounding]}, [VALID])
    template.setMeta("loc", "KOUN")
    book = ScenarioBook(ProfileSnapshot.from_profile(sounding))
    scenario = book.create(
        "Warm",
        (
            Perturbation(
                "surface_temperature", 1.0, "degC", interpolation="surface-only"
            ),
        ),
    )

    collection = scenario_collection(book, scenario.scenario_id, template)

    assert collection.getMeta("scenario_hypothetical") is True
    assert "hypothetical" in collection.getMeta("loc")
    assert np.isclose(collection._profs["scenario"][0].tmpc[0], 26.0)
