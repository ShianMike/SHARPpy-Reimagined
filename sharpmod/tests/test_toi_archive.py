"""Archive-runner and catalogue tests against mocked archives and tiny fixtures.

Nothing here touches the network. Live-archive checks are opt-in and live in
``test_live_provider_contracts.py``; these tests prove the resumable runner,
budget enforcement, hashing, strict JSON, determinism, and the catalogue's
leakage guards using in-memory doubles.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from sharpmod.guidance.toi_archive import (
    HRRR_F18_AVAILABLE_FROM,
    ArchiveRunner,
    CaseOutcome,
    FrameNotPublished,
    ResilientFrameFetcher,
    RunBudget,
    TOIArchiveError,
    archive_source_record,
    audit_local_resources,
    case_cache_key,
    default_case_estimate,
    file_sha256,
    maximum_available_forecast_hour,
    resolve_forecast_anchor,
    scientific_content_hash,
)
from sharpmod.guidance.toi_catalog import (
    CatalogPlan,
    TOICatalogError,
    TornadoDay,
    build_case_catalog,
    load_tornado_days,
    ncei_detail_urls,
)
from sharpmod.guidance.toi_dataset import TOICase, TOIDatasetError
from sharpmod.guidance.toi_risk_objects import (
    TOI_RISK_OBJECT_METHOD_VERSION,
    TOIRiskObjectError,
    conus_land_mask,
    conus_land_source,
    conus_land_tiles,
    detect_risk_objects,
    select_risk_object,
)
from sharpmod.model_transport import DownloadCancelled
from sharpmod.tests._toi_fixtures import synthetic_frame

RUN_TIME = datetime(2021, 3, 25, 6, tzinfo=timezone.utc)


def _case(event_id: str = "pilot-a", forecast_hour: int = 6, **overrides) -> TOICase:
    values = {
        "event_id": event_id,
        "case_class": "outbreak",
        "run_time": RUN_TIME,
        "forecast_hour": forecast_hour,
        "latitude": 35.0,
        "longitude": -97.0,
        "anchor_source": "fixed_domain_grid",
        "observed": {
            "tornado_count": 40,
            "ef2_plus_count": 10,
            "ef3_plus_count": 4,
            "ef4_plus_count": 1,
            "longest_ef2_plus_path_miles": 50.0,
        },
    }
    values.update(overrides)
    return TOICase(**values)


class FakeArchive:
    """A mocked frame source that writes a byte payload and can inject faults."""

    def __init__(
        self,
        *,
        payload_bytes: int = 4096,
        fail_hours: frozenset[int] = frozenset(),
        transient_hours: frozenset[int] = frozenset(),
        fail_events: int = 0,
    ):
        self.payload_bytes = payload_bytes
        self.fail_hours = fail_hours
        self.transient_hours = set(transient_hours)
        self.fail_events = fail_events
        self.calls: list[tuple[str, int]] = []

    def __call__(
        self,
        run_time,
        forecast_hour,
        point_latitude,
        point_longitude,
        pressure_level_hpa,
        *,
        download_dir=None,
        progress_callback=None,
        cancelled=None,
        region_radius_km=1400.0,
        grid_stride=4,
    ):
        hour = int(forecast_hour)
        self.calls.append((str(run_time), hour))
        # Simulate the bytes a real subset download would leave on disk.
        if download_dir is not None:
            target = Path(download_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"subset_f{hour:03d}.grib2").write_bytes(
                b"\0" * self.payload_bytes
            )
        if hour in self.fail_hours:
            raise RuntimeError(f"HRRR F{hour:03d} is unavailable")
        if hour in self.transient_hours:
            self.transient_hours.discard(hour)
            raise TimeoutError(f"transient fault on F{hour:03d}")
        return synthetic_frame(
            run_time,
            hour,
            point_latitude,
            point_longitude,
            pressure_level_hpa,
            strength=0.9,
        )


# --------------------------------------------------------------------------
# Estimates, audit, and source provenance
# --------------------------------------------------------------------------


def test_case_estimate_scales_frames_bytes_requests_and_time():
    estimate = default_case_estimate(seconds_per_frame=10.0)

    assert estimate.frames_per_case == 7
    assert estimate.anchor_frames_per_case == 1
    assert estimate.requests_per_case == 40
    assert estimate.seconds_per_case == pytest.approx(80.0)
    scaled = estimate.for_cases(600)
    assert scaled["frames"] == 4800
    assert scaled["transfer_gib"] > 40
    assert scaled["wall_hours"] == pytest.approx(13.33, abs=0.1)
    # Discarding raw subsets keeps retained output tiny, which is what makes a
    # multi-hundred-case run feasible on a laptop.
    assert scaled["retained_mib"] < 10
    assert scaled["peak_raw_mib_if_discarding"] < 50


def test_audit_reports_disk_headroom_and_missing_caches(tmp_path):
    present = tmp_path / "cache"
    present.mkdir()
    (present / "blob.bin").write_bytes(b"x" * 2048)

    audit = audit_local_resources(
        working_directory=tmp_path / "work",
        cache_directories=[present, tmp_path / "absent"],
    )

    assert audit["disk_free_bytes"] > 0
    assert audit["caches"][0]["present"] is True
    assert audit["caches"][0]["bytes"] == 2048
    assert audit["caches"][1]["present"] is False


def test_source_record_names_official_archives_and_licenses():
    record = archive_source_record()

    assert record["hrrr"]["bucket"] == "noaa-hrrr-bdp-pds"
    assert "noaa-hrrr-bdp-pds" in record["hrrr"]["base_url"]
    assert "public domain" in record["hrrr"]["license"]
    assert record["hrrr"]["messages_per_frame"] == 8
    assert set(record["hrrr"]["measured_subset_mib_by_era"]) == {
        "HRRRv1",
        "HRRRv2",
        "HRRRv3",
        "HRRRv4",
    }
    assert "ncei.noaa.gov" in record["outcomes"]["base_url"]
    assert record["feature_method_version"].startswith("sharpmod_hrrr_toi")


def test_measured_legacy_forecast_hour_limit_is_encoded():
    # Measured against the official bucket: no F18 at 06Z before HRRRv2.
    assert maximum_available_forecast_hour(
        datetime(2015, 4, 8, 6, tzinfo=timezone.utc)
    ) == 15
    assert maximum_available_forecast_hour(
        datetime(2016, 3, 30, 6, tzinfo=timezone.utc)
    ) == 15
    assert maximum_available_forecast_hour(HRRR_F18_AVAILABLE_FROM) == 18
    assert maximum_available_forecast_hour(
        datetime(2023, 3, 30, 6, tzinfo=timezone.utc)
    ) == 18


# --------------------------------------------------------------------------
# Deterministic cache keys
# --------------------------------------------------------------------------


def test_cache_key_is_deterministic_and_covers_feature_changing_inputs():
    base = case_cache_key(_case())

    assert base == case_cache_key(_case())
    assert base != case_cache_key(_case(forecast_hour=12))
    assert base != case_cache_key(_case(latitude=36.0))
    assert base != case_cache_key(_case(), sampling_interval_hours=6)
    assert base != case_cache_key(_case(), region_radius_km=900.0)
    assert base != case_cache_key(_case(), grid_stride=8)
    # Changing the method version must invalidate the key, so an incompatible
    # cached extract can never be reused.
    assert base != case_cache_key(_case(), method_version="something-else")


# --------------------------------------------------------------------------
# Resilient fetcher
# --------------------------------------------------------------------------


def test_fetcher_retries_transient_faults_with_bounded_backoff(tmp_path):
    archive = FakeArchive(transient_hours=frozenset({6}))
    slept: list[float] = []
    fetcher = ResilientFrameFetcher(
        # Rate limiting off, so the only sleep observed is the retry backoff.
        budget=RunBudget(
            maximum_attempts=3,
            backoff_base_seconds=1.0,
            minimum_request_interval_seconds=0.0,
        ),
        root=tmp_path,
        inner=archive,
        sleep=slept.append,
        monotonic=lambda: 0.0,
    )

    frame = fetcher(RUN_TIME, 6, 35.0, -97.0, 500)

    assert frame.forecast_hour == 6
    assert fetcher.attempts == 2
    assert fetcher.frames_decoded == 1
    # Exactly one backoff, bounded by the first-attempt ceiling (full jitter).
    assert len(slept) == 1
    assert 0.0 <= slept[0] <= 1.0
    assert "transient fault" in fetcher.retry_log[0]


def test_fetcher_backoff_grows_and_stays_capped(tmp_path):
    archive = FakeArchive(fail_hours=frozenset({18}))
    slept: list[float] = []
    fetcher = ResilientFrameFetcher(
        budget=RunBudget(
            maximum_attempts=5,
            backoff_base_seconds=2.0,
            backoff_maximum_seconds=8.0,
            minimum_request_interval_seconds=0.0,
        ),
        root=tmp_path,
        inner=archive,
        sleep=slept.append,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(TOIArchiveError):
        fetcher(RUN_TIME, 18, 35.0, -97.0, 500)

    # Four waits between five attempts, each inside its exponential ceiling and
    # never above the configured cap.
    assert len(slept) == 4
    for index, delay in enumerate(slept):
        ceiling = min(8.0, 2.0 * (2**index))
        assert 0.0 <= delay <= ceiling
    assert max(slept) <= 8.0


def test_fetcher_gives_up_after_the_attempt_ceiling(tmp_path):
    archive = FakeArchive(fail_hours=frozenset({18}))
    fetcher = ResilientFrameFetcher(
        budget=RunBudget(maximum_attempts=3, backoff_base_seconds=0.0),
        root=tmp_path,
        inner=archive,
        sleep=lambda _s: None,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(TOIArchiveError, match="failed after 3 attempts"):
        fetcher(RUN_TIME, 18, 35.0, -97.0, 500)
    assert fetcher.attempts == 3
    assert fetcher.frames_decoded == 0


def test_fetcher_accounts_transferred_bytes_and_discards_raw(tmp_path):
    archive = FakeArchive(payload_bytes=8192)
    fetcher = ResilientFrameFetcher(
        budget=RunBudget(discard_raw_after_extract=True),
        root=tmp_path,
        inner=archive,
        sleep=lambda _s: None,
        monotonic=lambda: 0.0,
    )

    fetcher(RUN_TIME, 6, 35.0, -97.0, 500)
    fetcher(RUN_TIME, 9, 35.0, -97.0, 500)

    assert fetcher.transfer_bytes == 16384
    # Raw subsets are removed, so peak disk stays at one frame, not the run.
    assert list(tmp_path.rglob("*.grib2")) == []


def test_fetcher_keeps_raw_when_asked(tmp_path):
    fetcher = ResilientFrameFetcher(
        budget=RunBudget(discard_raw_after_extract=False),
        root=tmp_path,
        inner=FakeArchive(payload_bytes=1024),
        sleep=lambda _s: None,
        monotonic=lambda: 0.0,
    )

    fetcher(RUN_TIME, 6, 35.0, -97.0, 500)

    assert len(list(tmp_path.rglob("*.grib2"))) == 1


def test_fetcher_propagates_cancellation_without_retrying(tmp_path):
    archive = FakeArchive()
    fetcher = ResilientFrameFetcher(
        budget=RunBudget(maximum_attempts=4),
        root=tmp_path,
        inner=archive,
        sleep=lambda _s: None,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(DownloadCancelled):
        fetcher(RUN_TIME, 6, 35.0, -97.0, 500, cancelled=lambda: True)
    assert archive.calls == []


def test_fetcher_respects_a_minimum_request_interval(tmp_path):
    clock = {"now": 0.0}
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    fetcher = ResilientFrameFetcher(
        budget=RunBudget(minimum_request_interval_seconds=1.5),
        root=tmp_path,
        inner=FakeArchive(),
        sleep=sleep,
        monotonic=lambda: clock["now"],
    )

    fetcher(RUN_TIME, 6, 35.0, -97.0, 500)
    fetcher(RUN_TIME, 9, 35.0, -97.0, 500)

    assert slept and slept[0] == pytest.approx(1.5)


# --------------------------------------------------------------------------
# Leakage-safe anchor resolution
# --------------------------------------------------------------------------


def test_anchor_is_resolved_from_forecast_fields_at_a_fixed_centre():
    archive = FakeArchive()

    latitude, longitude, provenance = resolve_forecast_anchor(
        RUN_TIME, 6, fetcher=archive, minimum_grid_points=4
    )

    assert provenance["anchor_source"] == "model_forecast_maximum_stp"
    assert provenance["anchor_search_centre"] == "39,-98"
    assert provenance["anchor_peak_proxy_stp"] > 0
    # Object-based selection, not a single grid maximum.
    assert provenance["anchor_selection_method"] == TOI_RISK_OBJECT_METHOD_VERSION
    assert provenance["anchor_candidate_objects"] >= 1
    assert provenance["anchor_accepted_objects"] >= 1
    assert provenance["anchor_selected_land_fraction"] >= 0.6
    assert provenance["anchor_selected_area_km2"] > 0
    assert provenance["anchor_resolved_region"] != "outside_conus"
    assert provenance["land_mask_source_sha256"]
    # The search starts from the fixed CONUS centre, never from observations,
    # and samples more than one issuance-time hour.
    assert archive.calls and {hour for _run, hour in archive.calls} == {6, 12, 18}
    assert -180.0 <= longitude <= 180.0
    assert -90.0 <= latitude <= 90.0


def test_anchor_is_unavailable_rather_than_guessed_when_no_object_qualifies():
    class Quiet(FakeArchive):
        def __call__(self, *args, **kwargs):
            frame = super().__call__(*args, **kwargs)
            # A genuinely quiet domain: no convective signal anywhere.
            frame.surface_cape_jkg[:] = 0.0
            frame.srh_1km_m2s2[:] = 0.0
            return frame

    with pytest.raises(TOIArchiveError, match="anchor unavailable"):
        resolve_forecast_anchor(RUN_TIME, 6, fetcher=Quiet())


def test_anchor_rejects_an_offshore_maximum_and_prefers_a_land_object():
    # The exact failure the pilot exposed: a hotter offshore object must lose to
    # a broader land object rather than being selected on peak intensity.
    rows, cols = 24, 40
    latitude = np.repeat(np.linspace(24.0, 48.0, rows)[:, None], cols, axis=1)
    longitude = np.repeat(np.linspace(-104.0, -70.0, cols)[None, :], rows, axis=0)
    stp = np.zeros((rows, cols))
    for row in np.argsort(np.abs(latitude[:, 0] - 30.0))[:3]:
        for col in np.argsort(np.abs(longitude[0, :] - (-72.0)))[:3]:
            stp[row, col] = 9.0
    for row in np.argsort(np.abs(latitude[:, 0] - 35.0))[:5]:
        for col in np.argsort(np.abs(longitude[0, :] - (-91.0)))[:6]:
            stp[row, col] = 3.0

    objects = detect_risk_objects(stp, latitude, longitude)
    best, provenance = select_risk_object(stp, latitude, longitude)

    assert len(objects) == 2
    offshore = next(obj for obj in objects if obj.peak_stp == pytest.approx(9.0))
    assert offshore.accepted is False
    assert offshore.land_fraction == pytest.approx(0.0)
    assert "land fraction" in offshore.rejection_reason
    # The selected object is the land one, despite a lower peak.
    assert best.peak_stp == pytest.approx(3.0)
    assert best.land_fraction == pytest.approx(1.0)
    assert 33.0 < best.centroid_latitude < 38.0
    assert -95.0 < best.centroid_longitude < -88.0
    assert provenance["anchor_accepted_objects"] == 1


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected"),
    [
        (30.30, -76.69, False),  # 2018-11-05 pilot anchor: Atlantic
        (27.79, -94.06, False),  # 2023-03-31 pilot anchor: Gulf of Mexico
        (35.10, -90.00, True),  # Memphis
        (37.50, -96.86, True),  # 2015-04-09 pilot anchor: Kansas
        (32.85, -92.29, True),  # 2017-01-22 pilot anchor: Louisiana
    ],
)
def test_land_mask_classifies_the_observed_pilot_anchors(
    latitude, longitude, expected
):
    mask = conus_land_mask(np.asarray([[latitude]]), np.asarray([[longitude]]))

    assert bool(mask[0, 0]) is expected


def test_land_mask_provenance_names_the_bundled_census_source():
    source = conus_land_source()

    assert "Census" in source["land_mask_source"]
    assert source["land_mask_source_sha256"]
    assert source["land_mask_tile_degrees"] == 1
    assert len(conus_land_tiles()) == source["land_mask_tile_count"]


def test_risk_objects_reject_single_cell_noise_and_tiny_areas():
    latitude = np.repeat(np.linspace(33.0, 39.0, 12)[:, None], 12, axis=1)
    longitude = np.repeat(np.linspace(-98.0, -92.0, 12)[None, :], 12, axis=0)
    stp = np.zeros((12, 12))
    stp[5, 5] = 12.0  # one very hot cell over land

    objects = detect_risk_objects(stp, latitude, longitude)

    assert len(objects) == 1
    assert objects[0].accepted is False
    assert "grid point" in objects[0].rejection_reason
    with pytest.raises(TOIRiskObjectError, match="no issuance-time risk object"):
        select_risk_object(stp, latitude, longitude)


def _crescent_domain():
    """A concave coastline whose land-and-ocean object centroids fall offshore.

    Land is a C opening east: a western block plus a northern and a southern
    arm.  A rectangular risk object laid over it keeps land fraction above the
    0.5 minimum while the centroid of *all* its members lands in the enclosed
    water.  This is the ``null-2018-04-16`` geometry in miniature.
    """

    rows, cols = 20, 20
    latitude = np.repeat(np.linspace(28.0, 38.0, rows)[:, None], cols, axis=1)
    longitude = np.repeat(np.linspace(-86.0, -76.0, cols)[None, :], rows, axis=0)
    land = np.zeros((rows, cols), dtype=bool)
    land[:, 0:6] = True  # western block
    land[0:3, 6:16] = True  # northern arm
    land[17:20, 6:16] = True  # southern arm
    stp = np.zeros((rows, cols))
    stp[:, 0:16] = 1.5  # one object spanning land and the enclosed water
    return stp, latitude, longitude, land


def _grid_index(latitude, longitude, point_latitude, point_longitude):
    distance = np.hypot(latitude - point_latitude, longitude - point_longitude)
    return np.unravel_index(int(np.argmin(distance)), distance.shape)


def test_large_land_and_ocean_object_anchors_on_land_not_offshore():
    stp, latitude, longitude, land = _crescent_domain()

    (selected,) = detect_risk_objects(stp, latitude, longitude, land_mask=land)

    # The object qualifies on every published rule, so it is not filtered out;
    # the anchor constraint is what has to keep it honest.
    assert selected.accepted is True
    assert selected.land_fraction > 0.5

    # Demonstrate the v1 escape actually exists for this geometry: the centroid
    # of every member point sits in the enclosed water.
    members = np.nonzero(stp >= 0.5)
    naive_row, naive_col = _grid_index(
        latitude,
        longitude,
        float(np.mean(latitude[members])),
        float(np.mean(longitude[members])),
    )
    assert bool(land[naive_row, naive_col]) is False

    # v2 anchors on a land grid point belonging to the object.
    assert selected.anchor_on_land is True
    anchor_row, anchor_col = _grid_index(
        latitude,
        longitude,
        selected.centroid_latitude,
        selected.centroid_longitude,
    )
    assert bool(land[anchor_row, anchor_col]) is True
    assert stp[anchor_row, anchor_col] >= 0.5
    # The snap moved the anchor by at most one grid step, not across the domain.
    assert selected.anchor_snap_km < 100.0


def test_selected_anchor_provenance_records_the_land_constraint():
    stp, latitude, longitude, land = _crescent_domain()

    selected, provenance = select_risk_object(
        stp, latitude, longitude, land_mask=land
    )

    assert provenance["anchor_on_land"] is True
    assert provenance["anchor_selection_method"].endswith("_v2")
    assert provenance["anchor_resolved_latitude"] == pytest.approx(
        round(selected.centroid_latitude, 4)
    )
    assert provenance["anchor_land_centroid_latitude"] is not None
    assert provenance["anchor_snap_km"] >= 0.0


def test_every_accepted_object_anchors_inside_its_own_land_points():
    stp, latitude, longitude, land = _crescent_domain()
    # Add a second, purely offshore object to confirm it is still rejected and
    # never contributes an anchor.
    stp[8:12, 17:20] = 6.0

    objects = detect_risk_objects(stp, latitude, longitude, land_mask=land)

    assert len(objects) == 2
    offshore = next(obj for obj in objects if obj.peak_stp == pytest.approx(6.0))
    assert offshore.accepted is False
    assert offshore.anchor_on_land is False
    for obj in objects:
        if not obj.accepted:
            continue
        row, col = _grid_index(
            latitude, longitude, obj.centroid_latitude, obj.centroid_longitude
        )
        assert bool(land[row, col]) is True


def test_risk_object_selection_is_deterministic_for_equal_scores():
    latitude = np.repeat(np.linspace(33.0, 40.0, 16)[:, None], 24, axis=1)
    longitude = np.repeat(np.linspace(-100.0, -86.0, 24)[None, :], 16, axis=0)
    stp = np.zeros((16, 24))
    stp[4:8, 3:8] = 4.0
    stp[10:14, 15:20] = 4.0

    first = select_risk_object(stp, latitude, longitude)[0]
    second = select_risk_object(stp, latitude, longitude)[0]

    assert first == second


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


def _runner(tmp_path, archive, **budget_overrides) -> ArchiveRunner:
    budget = RunBudget(
        maximum_cases=budget_overrides.pop("maximum_cases", 10),
        maximum_transfer_bytes=budget_overrides.pop(
            "maximum_transfer_bytes", 64 * 1024**2
        ),
        maximum_seconds=budget_overrides.pop("maximum_seconds", 600.0),
        minimum_free_bytes=budget_overrides.pop("minimum_free_bytes", 1),
        backoff_base_seconds=0.0,
        minimum_request_interval_seconds=0.0,
        **budget_overrides,
    )
    return ArchiveRunner(
        output_directory=tmp_path / "run",
        budget=budget,
        fetcher=archive,
        sleep=lambda _s: None,
    )


def test_runner_extracts_a_case_and_writes_a_hashed_strict_json_record(tmp_path):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=2048))

    report = runner.run([_case()])

    assert report.stop_reason == "completed"
    assert len(report.succeeded) == 1
    outcome = report.succeeded[0]
    assert outcome.frames_decoded == 7
    assert outcome.transfer_bytes == 7 * 2048
    path = runner.cases_dir / f"{outcome.cache_key}.json"
    text = path.read_text(encoding="utf-8")
    for token in ("NaN", "Infinity"):
        assert token not in text
    payload = json.loads(text)
    # The scientific hash is recomputable from the file, not self-referential.
    assert payload["scientific_content_sha256"] == outcome.scientific_content_sha256
    assert scientific_content_hash(payload) == outcome.scientific_content_sha256
    # The artifact hash lives in the checkpoint, over the final file bytes.
    assert outcome.artifact_sha256 == file_sha256(path)
    assert "artifact_sha256" not in payload
    assert payload["guidance"]["toi"]["state"] == "experimental"
    assert payload["case"]["event_id"] == "pilot-a"
    # The run report itself is written atomically and is strict JSON.
    assert (runner.root / "run-report.json").exists()
    json.loads((runner.root / "run-report.json").read_text(encoding="utf-8"))


def test_runner_is_deterministic_across_reruns(tmp_path):
    first = _runner(tmp_path / "a", FakeArchive(payload_bytes=1024))
    second = _runner(tmp_path / "b", FakeArchive(payload_bytes=1024))

    outcome_a = first.run([_case()]).succeeded[0]
    outcome_b = second.run([_case()]).succeeded[0]

    assert outcome_a.cache_key == outcome_b.cache_key
    # The scientific hash itself is stable, not merely selected subtrees.
    assert (
        outcome_a.scientific_content_sha256 == outcome_b.scientific_content_sha256
    )
    payload_a = json.loads(
        (first.cases_dir / f"{outcome_a.cache_key}.json").read_text("utf-8")
    )
    payload_b = json.loads(
        (second.cases_dir / f"{outcome_b.cache_key}.json").read_text("utf-8")
    )
    assert payload_a["guidance"] == payload_b["guidance"]
    assert payload_a["case"] == payload_b["case"]
    assert scientific_content_hash(payload_a) == scientific_content_hash(payload_b)


def test_runner_resumes_after_a_crash_without_repeating_work(tmp_path):
    archive = FakeArchive(payload_bytes=1024)
    runner = _runner(tmp_path, archive)
    cases = [_case("event-1"), _case("event-2", forecast_hour=12)]

    first = runner.run(cases[:1])
    assert len(first.succeeded) == 1
    calls_after_first = len(archive.calls)

    # A fresh runner over the same directory sees the checkpoint.
    resumed = _runner(tmp_path, archive)
    assert len(resumed.completed_keys()) == 1
    messages: list[str] = []
    second = resumed.run(cases, progress=messages.append)

    assert len(second.outcomes) == 1
    assert second.succeeded[0].event_id == "event-2"
    assert any("resume-skip event-1" in message for message in messages)
    # Only the new case was fetched.
    assert len(archive.calls) == calls_after_first + 7


def test_runner_ignores_a_truncated_final_checkpoint_line(tmp_path):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=512))
    runner.run([_case("event-1")])
    with open(runner.checkpoint_path, "a", encoding="utf-8") as handle:
        handle.write('{"cache_key": "partial-writ')

    # The corrupt tail is skipped rather than trusted or fatal.
    assert len(runner.completed_keys()) == 1


def test_runner_retries_a_failed_checkpoint_on_resume(tmp_path):
    archive = FakeArchive(payload_bytes=512)
    runner = _runner(tmp_path, archive)
    case = _case("retry-after-failure")
    key = case_cache_key(case)
    failed = CaseOutcome(
        event_id=case.event_id,
        run_time=case.run_time.isoformat(),
        cache_key=key,
        status="failed",
        reason="transient archive outage",
    )
    runner.checkpoint_path.write_text(
        json.dumps(failed.to_mapping()) + "\n", encoding="utf-8"
    )

    assert runner.checkpoint_records()[key]["status"] == "failed"
    assert runner.completed_keys() == {}

    report = runner.run([case])

    assert len(report.succeeded) == 1
    assert report.succeeded[0].cache_key == key
    assert archive.calls
    assert runner.completed_keys() == {key: "success"}


def test_runner_stops_at_the_case_budget(tmp_path):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=512), maximum_cases=2)

    report = runner.run(
        [_case(f"event-{index}", forecast_hour=6 + index) for index in range(5)]
    )

    assert report.stop_reason == "case_budget_reached"
    assert len(report.outcomes) == 2


def test_runner_stops_at_the_transfer_budget(tmp_path):
    runner = _runner(
        tmp_path,
        FakeArchive(payload_bytes=1024 * 1024),
        maximum_transfer_bytes=8 * 1024 * 1024,
    )

    report = runner.run(
        [_case(f"event-{index}", forecast_hour=6 + index) for index in range(6)]
    )

    assert report.stop_reason == "transfer_budget_reached"
    assert report.transfer_bytes >= 8 * 1024 * 1024
    assert len(report.outcomes) < 6


def test_runner_stops_when_disk_headroom_is_exhausted(tmp_path):
    runner = _runner(
        tmp_path,
        FakeArchive(payload_bytes=512),
        minimum_free_bytes=1024**6,
    )

    report = runner.run([_case()])

    assert report.stop_reason.startswith("disk_headroom_exhausted")
    assert report.outcomes == []


def test_runner_stops_at_the_time_budget(tmp_path):
    clock = {"now": 0.0}

    def monotonic() -> float:
        clock["now"] += 30.0
        return clock["now"]

    runner = ArchiveRunner(
        output_directory=tmp_path / "run",
        budget=RunBudget(
            maximum_seconds=10.0,
            minimum_free_bytes=1,
            backoff_base_seconds=0.0,
            minimum_request_interval_seconds=0.0,
        ),
        fetcher=FakeArchive(payload_bytes=256),
        sleep=lambda _s: None,
        monotonic=monotonic,
    )

    report = runner.run([_case()])

    assert report.stop_reason == "time_budget_reached"


def test_runner_records_partial_sampling_as_degraded_but_usable(tmp_path):
    # F018 missing mirrors the measured pre-HRRRv2 archive limitation.
    runner = _runner(tmp_path, FakeArchive(fail_hours=frozenset({18})))

    report = runner.run([_case()])

    outcome = report.succeeded[0]
    assert outcome.frames_decoded == 6
    assert outcome.sampling_status == "degraded"
    payload = json.loads(
        (runner.cases_dir / f"{outcome.cache_key}.json").read_text("utf-8")
    )
    provenance = payload["guidance"]["provenance"]
    assert provenance["toi_time_coverage_hours"] == "15"
    assert "missing forecast hours 18" in provenance["toi_sampling_degraded_reason"]
    assert any("F018" in line for line in payload["retry_log"])


def test_runner_marks_insufficient_coverage_as_skipped_with_a_reason(tmp_path):
    runner = _runner(
        tmp_path, FakeArchive(fail_hours=frozenset({9, 12, 15, 18}))
    )

    report = runner.run([_case()])

    assert report.succeeded == []
    assert len(report.skipped) == 1
    assert "6 h of the 18 h window" in report.skipped[0].reason
    # A skip is checkpointed, so a resume does not silently retry it forever.
    assert len(runner.completed_keys()) == 1


def test_runner_reports_cancellation_and_stops(tmp_path):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=256))

    report = runner.run([_case()], cancelled=lambda: True)

    assert report.stop_reason == "cancelled"
    assert report.outcomes == []


def test_runner_collects_a_compact_manifest(tmp_path):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=512))
    runner.run([_case("event-1"), _case("event-2", forecast_hour=12)])

    manifest = runner.collect_manifest()

    assert manifest["verified"] is True
    assert manifest["verified_cases"] == 2
    assert {record["event_id"] for record in manifest["cases"]} == {
        "event-1",
        "event-2",
    }
    assert all(
        record["scientific_content_sha256"] for record in manifest["cases"]
    )
    assert all(record["artifact_sha256"] for record in manifest["cases"])
    assert manifest["sources"]["hrrr"]["bucket"] == "noaa-hrrr-bdp-pds"


def test_outcome_status_must_be_one_of_three_explicit_values():
    with pytest.raises(TOIArchiveError, match="case status must be"):
        CaseOutcome(
            event_id="e",
            run_time="2021-01-01T00:00:00+00:00",
            cache_key="k",
            status="maybe",
        )


def test_budget_rejects_unbounded_concurrency():
    with pytest.raises(TOIArchiveError, match="capped at 4"):
        RunBudget(maximum_concurrent_cases=64)


# --------------------------------------------------------------------------
# Catalogue generation
# --------------------------------------------------------------------------

_NCEI_LISTING = """
<a href="StormEvents_details-ftp_v1.0_d2015_c20200317.csv.gz">x</a>
<a href="StormEvents_details-ftp_v1.0_d2015_c20240116.csv.gz">x</a>
<a href="StormEvents_details-ftp_v1.0_d2016_c20220719.csv.gz">x</a>
"""

_NCEI_HEADER = (
    "BEGIN_YEARMONTH,BEGIN_DAY,EVENT_TYPE,TOR_F_SCALE,TOR_LENGTH,"
    "BEGIN_LAT,BEGIN_LON,BEGIN_DATE_TIME\n"
)


def _ncei_rows(rows: list[str]) -> bytes:
    return gzip.compress((_NCEI_HEADER + "".join(rows)).encode("utf-8"))


def test_ncei_url_resolution_picks_the_newest_creation_date():
    urls = ncei_detail_urls([2015, 2016], listing=_NCEI_LISTING)

    assert urls[2015].endswith("d2015_c20240116.csv.gz")
    assert urls[2016].endswith("d2016_c20220719.csv.gz")

    with pytest.raises(TOICatalogError, match="not found for year"):
        ncei_detail_urls([2099], listing=_NCEI_LISTING)


def test_tornado_days_aggregate_counts_intensities_and_paths():
    payload = _ncei_rows(
        [
            "201504,09,Tornado,EF4,45.2,35.1,-97.2,09-APR-15 18:00:00\n",
            "201504,09,Tornado,EF3,12.0,35.4,-97.0,09-APR-15 19:00:00\n",
            "201504,09,Tornado,EF1,2.0,35.5,-96.9,09-APR-15 20:00:00\n",
            "201504,09,Hail,,0,35.5,-96.9,09-APR-15 20:00:00\n",
            "201504,15,Tornado,EF0,0.5,33.0,-95.0,15-APR-15 22:00:00\n",
        ]
    )

    days, sources = load_tornado_days({2015: "memory://x"}, fetch=lambda _u: payload)

    heavy = days["2015-04-09"]
    assert heavy.tornado_count == 3
    assert heavy.ef2_plus_count == 2
    assert heavy.ef3_plus_count == 2
    assert heavy.ef4_plus_count == 1
    assert heavy.longest_ef2_plus_path_miles == pytest.approx(45.2)
    assert heavy.is_high_end is True
    light = days["2015-04-15"]
    assert light.tornado_count == 1
    assert light.is_high_end is False
    assert sources[0].sha256 and sources[0].bytes == len(payload)


def _days() -> dict[str, TornadoDay]:
    days: dict[str, TornadoDay] = {}
    # Six high-end days and a set of ordinary tornado days across 2015-2018.
    for index, date in enumerate(
        ["2015-04-09", "2016-03-31", "2017-01-22", "2018-04-13"]
    ):
        days[date] = TornadoDay(
            date=date,
            tornado_count=40 + index,
            ef2_plus_count=10,
            ef3_plus_count=4,
            ef4_plus_count=1,
            longest_ef2_plus_path_miles=55.0,
            centroid_latitude=35.0,
            centroid_longitude=-90.0,
        )
    for index, date in enumerate(
        ["2015-05-20", "2016-06-10", "2017-05-04", "2018-10-02"]
    ):
        days[date] = TornadoDay(
            date=date,
            tornado_count=3 + index,
            ef2_plus_count=0,
            ef3_plus_count=0,
            ef4_plus_count=0,
            longest_ef2_plus_path_miles=0.0,
            centroid_latitude=36.0,
            centroid_longitude=-95.0,
        )
    return days


def test_catalog_is_stratified_leakage_safe_and_frequency_aware():
    plan = CatalogPlan(
        positive_cases=4,
        severe_cases=4,
        null_cases=8,
        first_year=2015,
        last_year=2018,
    )

    payload = build_case_catalog(_days(), plan=plan)

    assert payload["target_definition"] == "high_risk_worthy_proxy_v1"
    assert payload["dataset_kind"] == "historical"
    assert payload["catalog_counts"] == {"outbreak": 4, "severe": 4, "null": 8}
    # Real high-end days are rare against the full day population.
    assert 0.0 < payload["population_base_rate"] < 0.01
    for case in payload["cases"]:
        # The anchor is resolved from forecast fields, never observations.
        assert case["anchor_source"] == "model_forecast_maximum_stp"
        assert case["latitude"] == 39.0 and case["longitude"] == -98.0
        # The cycle is 06Z the day before the event day.
        cycle = datetime.fromisoformat(case["run_time"])
        assert cycle.hour == 6
        assert case["event_id"].endswith(case["notes"].split()[-1])
    assert set(payload["catalog_strata"]) >= {
        "season",
        "hrrr_era",
        "forecast_hour",
        "event_year",
    }
    # Multiple forecast leads are represented rather than one.
    assert len(payload["catalog_strata"]["forecast_hour"]) > 1


def test_catalog_never_queues_a_forecast_hour_the_archive_cannot_serve():
    """Regression: 32 of 600 real cases were guaranteed failures.

    The builder reported the pre-HRRRv2 F15 ceiling but still assigned forecast
    hours round-robin, so 06Z cases in 2015-2016 were queued at F018 - a frame
    the archive never published. Each one burned four retries with backoff
    before failing.
    """
    plan = CatalogPlan(
        positive_cases=6,
        severe_cases=6,
        null_cases=6,
        first_year=2015,
        last_year=2018,
    )

    payload = build_case_catalog(_days(), plan=plan)

    impossible = [
        case
        for case in payload["cases"]
        if int(case["forecast_hour"])
        > maximum_available_forecast_hour(
            datetime.fromisoformat(case["run_time"])
        )
    ]
    assert impossible == [], f"{len(impossible)} case(s) can only ever fail"

    limits = payload["catalog_sampling_limits"]
    assert limits["forecast_hours_clamped"] >= 1
    assert "clamped" in limits["forecast_hour_clamp_note"]
    # Clamping targets a planned hour, so no era-confounded f015 bin appears.
    assert set(payload["catalog_strata"]["forecast_hour"]) <= {
        f"f{hour:03d}" for hour in plan.forecast_hours
    }
    # More than one lead survives, so lead stratification stays meaningful.
    assert len(payload["catalog_strata"]["forecast_hour"]) > 1


def test_unpublished_frame_fails_fast_instead_of_retrying(tmp_path):
    """A frame the archive never published is not a transport fault."""
    attempts: list[int] = []

    class Counting(FakeArchive):
        def __call__(self, run_time, forecast_hour, *args, **kwargs):
            attempts.append(int(forecast_hour))
            return super().__call__(run_time, forecast_hour, *args, **kwargs)

    legacy = HRRR_F18_AVAILABLE_FROM - timedelta(days=1)
    slept: list[float] = []
    fetcher = ResilientFrameFetcher(
        budget=RunBudget(maximum_attempts=4),
        root=tmp_path,
        inner=Counting(),
        sleep=slept.append,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(FrameNotPublished, match="serves at most F015"):
        fetcher(legacy, 18, 35.0, -97.0, 500)

    # No request was issued and no backoff was slept: retrying cannot help.
    assert attempts == []
    assert slept == []
    # A hour the era does serve still works.
    fetcher(legacy, 12, 35.0, -97.0, 500)
    assert attempts == [12]


def test_anchor_degrades_when_an_extra_hour_is_unpublished():
    """A pre-HRRRv2 case must still resolve an anchor from the hours that exist.

    Failing the case instead would silently cost the archive two development
    years, which the 8-year sample-size floor cannot afford.
    """
    legacy = HRRR_F18_AVAILABLE_FROM - timedelta(days=1)

    class LegacyArchive(FakeArchive):
        def __call__(self, run_time, forecast_hour, *args, **kwargs):
            if int(forecast_hour) > maximum_available_forecast_hour(run_time):
                raise FrameNotPublished(
                    f"F{int(forecast_hour):03d} is not published; this archive "
                    "era serves at most F015"
                )
            return super().__call__(run_time, forecast_hour, *args, **kwargs)

    archive = LegacyArchive()
    _lat, _lon, provenance = resolve_forecast_anchor(
        legacy, 6, fetcher=archive, minimum_grid_points=4
    )

    # F018 was requested, refused, and skipped rather than aborting the case.
    assert provenance["anchor_unpublished_hours"] == "18"
    assert provenance["anchor_frames_complete"] is False
    assert provenance["anchor_forecast_hours"] == "6,12"
    assert provenance["anchor_resolved_region"] != "outside_conus"
    # A modern cycle is still reported as complete.
    _lat2, _lon2, modern = resolve_forecast_anchor(
        RUN_TIME, 6, fetcher=FakeArchive(), minimum_grid_points=4
    )
    assert modern["anchor_unpublished_hours"] == "none"
    assert modern["anchor_frames_complete"] is True


def test_catalog_surfaces_the_measured_legacy_f15_limitation():
    plan = CatalogPlan(
        positive_cases=4, severe_cases=4, null_cases=4,
        first_year=2015, last_year=2018,
    )

    payload = build_case_catalog(_days(), plan=plan)
    limits = payload["catalog_sampling_limits"]

    assert limits["legacy_f15_cases"] >= 1
    assert 0.0 < limits["legacy_f15_fraction"] <= 1.0
    assert "no F18 at 06Z" in limits["note"]


def test_catalog_case_ids_group_a_whole_event_day():
    payload = build_case_catalog(
        _days(),
        plan=CatalogPlan(
            positive_cases=4, severe_cases=4, null_cases=4,
            first_year=2015, last_year=2018,
        ),
    )

    # One event id per convective day, so every cycle for that day shares an
    # event_year and cannot be split across a validation boundary.
    ids = [case["event_id"] for case in payload["cases"]]
    assert len(ids) == len(set(ids))
    assert all(identifier.count("-") == 3 for identifier in ids)


def test_catalog_refuses_a_single_class_plan():
    with pytest.raises(TOICatalogError, match="missing"):
        build_case_catalog(
            _days(),
            plan=CatalogPlan(
                positive_cases=4, severe_cases=0, null_cases=0,
                first_year=2015, last_year=2018,
            ),
        )


def test_catalog_manifest_loads_through_the_validated_contract(tmp_path):
    from sharpmod.guidance.toi_catalog import save_catalog
    from sharpmod.guidance.toi_dataset import TOILabelManifest

    payload = build_case_catalog(
        _days(),
        plan=CatalogPlan(
            positive_cases=4, severe_cases=4, null_cases=6,
            first_year=2015, last_year=2018,
        ),
    )
    path = save_catalog(payload, tmp_path / "catalog.json")

    manifest = TOILabelManifest.load(path)

    assert manifest.dataset_kind == "historical"
    assert manifest.target_definition == "high_risk_worthy_proxy_v1"
    assert len(manifest.cases) == 14
    # Labels are derived by the named proxy from the recorded observed counts.
    positives = [case for case in manifest.cases if manifest.label_for(case) == 1]
    assert len(positives) == 4


def test_catalog_rejects_out_of_window_only_input():
    with pytest.raises(TOICatalogError, match="no observed tornado days"):
        build_case_catalog(
            _days(), plan=CatalogPlan(first_year=2024, last_year=2025)
        )


def test_runner_rejects_an_unreadable_case_file(tmp_path):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=256))
    runner.run([_case()])
    (runner.cases_dir / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(TOIDatasetError, match="archive verification failed"):
        runner.collect_manifest()


# --------------------------------------------------------------------------
# Single-writer run lock (found by the pilot)
# --------------------------------------------------------------------------


def test_run_directory_lock_is_exclusive(tmp_path):
    from sharpmod.guidance.toi_archive import RunDirectoryLock

    first = RunDirectoryLock(tmp_path / "run.lock")
    first.acquire()
    try:
        assert (tmp_path / "run.lock").exists()
        with pytest.raises(TOIArchiveError, match="locked by another process"):
            RunDirectoryLock(tmp_path / "run.lock").acquire()
    finally:
        first.release()
    # Released locks are reusable.
    with RunDirectoryLock(tmp_path / "run.lock"):
        assert (tmp_path / "run.lock").exists()
    assert not (tmp_path / "run.lock").exists()


def test_run_directory_lock_reclaims_a_stale_lock(tmp_path):
    from sharpmod.guidance.toi_archive import RunDirectoryLock

    path = tmp_path / "run.lock"
    path.write_text("pid=999999 acquired_at=old", encoding="utf-8")
    import os as _os
    import time as _time

    stale = _time.time() - 10_000
    _os.utime(path, (stale, stale))

    # A hard crash must not wedge an unattended run forever.
    with RunDirectoryLock(path, stale_after_seconds=3600.0):
        assert path.exists()


def test_a_second_runner_cannot_duplicate_work_concurrently(tmp_path):
    archive = FakeArchive(payload_bytes=256)
    runner = _runner(tmp_path, archive)
    blocker = None

    def probe(_message: str) -> None:
        nonlocal blocker
        if blocker is None:
            # While the first run holds the lock, a second must refuse.
            other = _runner(tmp_path, FakeArchive(payload_bytes=256))
            try:
                other.run([_case("other")])
            except TOIArchiveError as exc:
                blocker = str(exc)

    runner.run([_case("event-1")], progress=probe)

    assert blocker is not None
    assert "single writer" in blocker


# --------------------------------------------------------------------------
# Deterministic scientific hashing
# --------------------------------------------------------------------------


def _science_payload(**overrides) -> dict:
    payload = {
        "hash_version": "toi_scientific_content_sha256_v1",
        "cache_key": "abc123",
        "cache_inputs": {"event_id": "e", "forecast_hour": 6},
        "case": {"event_id": "e", "observed": {"tornado_count": 3}},
        "anchor": {"anchor_selected_land_fraction": 0.9},
        "guidance": {"toi": {"score": 4.2}},
        "method_versions": {"feature_method": "m"},
        "sources": {"hrrr_bucket": "noaa-hrrr-bdp-pds"},
        # Volatile operational metadata.
        "extracted_at": "2026-08-05T12:00:00+00:00",
        "retry_log": ["F018 attempt 1: TimeoutError"],
        "transfer_bytes": 12345,
        "frames_decoded": 7,
    }
    payload.update(overrides)
    return payload


def test_scientific_hash_ignores_clocks_retries_and_transfer_metadata():
    base = scientific_content_hash(_science_payload())

    # Different clock, different retries, different transfer accounting.
    assert base == scientific_content_hash(
        _science_payload(
            extracted_at="2031-01-01T00:00:00+00:00",
            retry_log=["F006 attempt 1: TimeoutError", "F006 attempt 2: 503"],
            transfer_bytes=99_999_999,
            frames_decoded=6,
        )
    )
    # Extra volatile keys must not participate either.
    assert base == scientific_content_hash(
        _science_payload(seconds=413.2, runner_host="vm-7")
    )


@pytest.mark.parametrize(
    "field",
    ["cache_key", "cache_inputs", "case", "anchor", "guidance", "method_versions",
     "sources", "hash_version"],
)
def test_scientific_hash_changes_when_any_scientific_field_changes(field):
    base = scientific_content_hash(_science_payload())

    mutated = _science_payload()
    mutated[field] = {"tampered": True} if isinstance(
        mutated[field], dict
    ) else "tampered"

    assert scientific_content_hash(mutated) != base


def test_scientific_hash_requires_every_declared_field():
    payload = _science_payload()
    del payload["guidance"]

    with pytest.raises(TOIArchiveError, match="missing field"):
        scientific_content_hash(payload)


def test_runner_hash_is_stable_across_clocks_retries_and_timing(tmp_path):
    # Same scientific inputs, different transient conditions on each run.
    clean = _runner(tmp_path / "clean", FakeArchive(payload_bytes=1024))
    retried = _runner(
        tmp_path / "retried",
        FakeArchive(payload_bytes=8192, transient_hours=frozenset({6, 12})),
    )

    first = clean.run([_case()]).succeeded[0]
    second = retried.run([_case()]).succeeded[0]

    # The retried run really did retry and really did transfer more bytes.
    assert second.attempts > first.attempts
    assert second.transfer_bytes != first.transfer_bytes
    # The scientific hash is nevertheless identical.
    assert second.scientific_content_sha256 == first.scientific_content_sha256
    # The artifact hashes differ only because the volatile block differs.
    payload_a = json.loads(
        (clean.cases_dir / f"{first.cache_key}.json").read_text("utf-8")
    )
    payload_b = json.loads(
        (retried.cases_dir / f"{second.cache_key}.json").read_text("utf-8")
    )
    assert payload_a["retry_log"] != payload_b["retry_log"]
    assert payload_a["scientific_content_sha256"] == (
        payload_b["scientific_content_sha256"]
    )


# --------------------------------------------------------------------------
# Verification is real
# --------------------------------------------------------------------------


def _verified_runner(tmp_path, cases=None):
    runner = _runner(tmp_path, FakeArchive(payload_bytes=1024))
    runner.run(cases or [_case("event-1"), _case("event-2", forecast_hour=12)])
    report = runner.verify()
    assert report["verified"] is True, report["failures"]
    return runner


def test_verify_passes_on_an_untouched_run(tmp_path):
    runner = _verified_runner(tmp_path)

    report = runner.verify()

    assert report["failure_count"] == 0
    assert report["case_files"] == 2
    assert report["verified_cases"] == 2
    assert report["checkpoint_entries"] == 2
    assert report["hash_version"] == "toi_scientific_content_sha256_v1"


def test_verify_rejects_scientific_tampering(tmp_path):
    runner = _verified_runner(tmp_path)
    path = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guidance"]["toi"]["score"] = 4.99
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert report["verified"] is False
    assert "scientific_hash_mismatch" in checks
    assert str(path) in {failure["path"] for failure in report["failures"]}


def test_verify_rejects_a_forged_embedded_hash(tmp_path):
    runner = _verified_runner(tmp_path)
    path = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guidance"]["toi"]["score"] = 1.11
    # Recompute the stored hash so it self-consistently matches the tampering.
    payload["scientific_content_sha256"] = scientific_content_hash(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    # The embedded hash is now self-consistent, so only the checkpoint's
    # independent artifact and scientific hashes can catch it.
    assert report["verified"] is False
    assert "artifact_hash_mismatch" in checks
    assert "checkpoint_hash_mismatch" in checks


def test_verify_rejects_a_byte_level_edit_that_keeps_the_science(tmp_path):
    runner = _verified_runner(tmp_path)
    path = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["extracted_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    # The science is unchanged, so the scientific hash still matches; the
    # artifact hash over final bytes is what detects the edit.
    assert "scientific_hash_mismatch" not in checks
    assert "artifact_hash_mismatch" in checks


def test_verify_rejects_truncated_and_non_strict_json(tmp_path):
    runner = _verified_runner(tmp_path)
    files = sorted(runner.cases_dir.glob("*.json"))
    files[0].write_text('{"cache_key": "abc", "case": {', encoding="utf-8")
    files[1].write_text('{"cache_key": "x", "score": NaN}', encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert "corrupt_json" in checks
    assert "non_strict_json" in checks


def test_verify_rejects_a_renamed_case_file(tmp_path):
    runner = _verified_runner(tmp_path)
    path = next(runner.cases_dir.glob("*.json"))
    path.rename(runner.cases_dir / "renamed.json")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert "filename_mismatch" in checks
    assert "missing_file" in checks


def test_verify_rejects_a_duplicated_case_file(tmp_path):
    runner = _verified_runner(tmp_path)
    source = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(source.read_text(encoding="utf-8"))
    duplicate = runner.cases_dir / f"{payload['cache_key']}.json.copy.json"
    duplicate.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert "duplicate_cache_key" in checks
    assert "duplicate_case" in checks


def test_verify_rejects_swapped_case_files(tmp_path):
    runner = _verified_runner(tmp_path)
    first, second = sorted(runner.cases_dir.glob("*.json"))
    text_a, text_b = first.read_text("utf-8"), second.read_text("utf-8")
    first.write_text(text_b, encoding="utf-8")
    second.write_text(text_a, encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert report["verified"] is False
    assert "filename_mismatch" in checks


def test_verify_rejects_an_orphan_file_with_no_checkpoint(tmp_path):
    runner = _verified_runner(tmp_path)
    source = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["cache_key"] = "orphan00000000000000"
    payload["cache_inputs"]["event_id"] = "orphan"
    payload["case"]["event_id"] = "orphan"
    payload["scientific_content_sha256"] = scientific_content_hash(payload)
    (runner.cases_dir / "orphan00000000000000.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert "orphan_file" in checks


def test_verify_rejects_a_missing_file_for_a_checkpointed_success(tmp_path):
    runner = _verified_runner(tmp_path)
    next(runner.cases_dir.glob("*.json")).unlink()

    report = runner.verify()
    failures = [f for f in report["failures"] if f["check"] == "missing_file"]

    assert failures
    assert failures[0]["event_id"] in {"event-1", "event-2"}


def test_verify_rejects_checkpoint_status_disagreement(tmp_path):
    runner = _runner(tmp_path, FakeArchive(fail_hours=frozenset({9, 12, 15, 18})))
    report = runner.run([_case("skipme")])
    key = report.skipped[0].cache_key
    # Fabricate a case file for a case the checkpoint says was skipped.
    payload = _science_payload(cache_key=key)
    payload["scientific_content_sha256"] = scientific_content_hash(payload)
    (runner.cases_dir / f"{key}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    verification = runner.verify()
    checks = {failure["check"] for failure in verification["failures"]}

    assert "status_disagreement" in checks


def test_verify_rejects_a_cache_key_that_does_not_derive_from_its_inputs(tmp_path):
    runner = _verified_runner(tmp_path)
    path = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cache_inputs"]["grid_stride"] = 999
    payload["scientific_content_sha256"] = scientific_content_hash(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = runner.verify()
    checks = {failure["check"] for failure in report["failures"]}

    assert "cache_key_mismatch" in checks


def test_verify_rejects_a_method_and_source_mismatch(tmp_path):
    runner = _verified_runner(tmp_path)
    path = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sources"].pop("hrrr_bucket")
    payload["scientific_content_sha256"] = scientific_content_hash(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = runner.verify(expected_feature_method="a-different-method")
    checks = {failure["check"] for failure in report["failures"]}

    assert "missing_source" in checks
    assert "method_mismatch" in checks


def test_verify_preserves_skip_reasons(tmp_path):
    runner = _runner(tmp_path, FakeArchive(fail_hours=frozenset({9, 12, 15, 18})))
    runner.run([_case("skipme")])

    report = runner.verify()

    assert report["checkpoint_statuses"] == {"skipped": 1}
    assert len(report["skipped"]) == 1
    assert "6 h of the 18 h window" in report["skipped"][0]["reason"]


# --------------------------------------------------------------------------
# Archive -> dataset compilation performs no network access
# --------------------------------------------------------------------------


def test_compile_builds_a_trainable_dataset_without_any_network(tmp_path, monkeypatch):
    import urllib.request

    from sharpmod.guidance.toi_compile import compile_archive_dataset

    runner = _runner(tmp_path, FakeArchive(payload_bytes=1024))
    runner.run(
        [
            _case("outbreak-a", forecast_hour=6),
            _case("severe-b", forecast_hour=12, case_class="severe"),
            _case("null-c", forecast_hour=18, case_class="null"),
        ]
    )

    def forbidden(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("compilation must not perform network access")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(
        "sharpmod.guidance.hrrr.fetch_hrrr_regional_frame", forbidden
    )

    dataset, report = compile_archive_dataset(
        tmp_path / "run", label_source="mocked archive fixture"
    )

    assert report["network_fetches"] == 0
    assert report["verified"] is True
    assert report["compiled_rows"] == 3
    assert len(dataset.rows) == 3
    # Provenance retained per row: event/year/region/season/lead/era plus hashes.
    provenance = report["provenance"][0]
    for key in (
        "event_year",
        "hrrr_era",
        "region",
        "season",
        "forecast_lead",
        "scientific_content_sha256",
        "anchor_selection",
    ):
        assert key in provenance
    # The dataset is directly consumable by the training entry points.
    assert dataset.target_definition == "high_risk_worthy_proxy_v1"
    assert set(dataset.years) == {2021}
    assert dataset.positive_count == 3


def test_compiled_dataset_is_accepted_by_the_training_entry_point(tmp_path):
    from sharpmod.guidance.toi_compile import compile_archive_dataset
    from sharpmod.guidance.toi_training import evaluate_dataset

    runner = _runner(tmp_path, FakeArchive(payload_bytes=512))
    cases = [
        _case("out-a", forecast_hour=6),
        _case("sev-b", forecast_hour=12, case_class="severe"),
    ]
    # A negative case so evaluation has both outcomes.
    cases.append(
        _case(
            "null-c",
            forecast_hour=18,
            case_class="null",
            observed={
                "tornado_count": 0,
                "ef2_plus_count": 0,
                "ef3_plus_count": 0,
                "ef4_plus_count": 0,
                "longest_ef2_plus_path_miles": 0.0,
            },
        )
    )
    runner.run(cases)

    dataset, _report = compile_archive_dataset(
        tmp_path / "run", label_source="mocked archive fixture"
    )
    evaluation = evaluate_dataset(dataset)

    assert evaluation["cases"] == 3
    assert "public_anchor_transform" in evaluation["reports"]


def test_compile_refuses_unverified_archive_output(tmp_path):
    from sharpmod.guidance.toi_compile import TOICompileError, compile_archive_dataset

    runner = _runner(tmp_path, FakeArchive(payload_bytes=512))
    runner.run([_case("event-1")])
    path = next(runner.cases_dir.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guidance"]["toi"]["score"] = 5.0
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(TOICompileError, match="refusing to compile unverified"):
        compile_archive_dataset(tmp_path / "run", label_source="x")


def test_compile_preserves_skip_reasons_and_population_weights(tmp_path):
    from sharpmod.guidance.toi_compile import compile_archive_dataset

    runner = _runner(tmp_path, FakeArchive(payload_bytes=512))
    runner.run([_case("kept", forecast_hour=6)])
    # A second run whose case is skipped for insufficient coverage.
    thin = ArchiveRunner(
        output_directory=tmp_path / "run",
        budget=runner.budget,
        fetcher=FakeArchive(fail_hours=frozenset({9, 12, 15, 18})),
        sleep=lambda _s: None,
    )
    thin.run(
        [
            _case(
                "dropped",
                forecast_hour=12,
                case_class="null",
                observed={
                    "tornado_count": 0,
                    "ef2_plus_count": 0,
                    "ef3_plus_count": 0,
                    "ef4_plus_count": 0,
                    "longest_ef2_plus_path_miles": 0.0,
                },
            )
        ]
    )

    dataset, report = compile_archive_dataset(
        tmp_path / "run", label_source="fixture"
    )

    assert report["compiled_rows"] == 1
    assert report["skipped_cases"] == 1
    assert dataset.skipped and "dropped" in dataset.skipped[0]["event_id"]
    assert "window" in dataset.skipped[0]["reason"]
