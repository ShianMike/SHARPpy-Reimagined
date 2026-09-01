"""Tests for the project-owned RRFS route over NOMADS static GRIB2 files.

These cover the parts that cannot be checked by a live fetch without spending
hundreds of megabytes: the wgrib2 inventory arithmetic, the published URL shape,
the cycle contract, and the provenance sidecar that lets a warm hit skip the
network entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from sharpmod import rrfs_nomads
from sharpmod.model_fields import (
    NOAA_SURFACE_SEARCH,
    supports_noaa_surface_merge,
)

RUN = datetime(2026, 9, 1, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _forget_remembered_release():
    """Keep the per-process release cache from leaking between tests."""
    rrfs_nomads.forget_release()
    yield
    rrfs_nomads.forget_release()


# --- inventory arithmetic --------------------------------------------------


def test_parse_idx_turns_start_offsets_into_inclusive_ranges():
    """wgrib2 publishes starts only, so ends come from the following record."""
    text = "\n".join([
        "1:0:d=2026090100:HGT:1000 mb:1 hour fcst:",
        "2:100:d=2026090100:TMP:1000 mb:1 hour fcst:",
        "3:250:d=2026090100:RH:1000 mb:1 hour fcst:",
    ])

    records = rrfs_nomads.parse_idx(text, 400)

    assert [(r.start, r.end) for r in records] == [
        (0, 99), (100, 249), (250, 399),
    ]
    assert [r.size for r in records] == [100, 150, 150]
    assert [r.variable for r in records] == ["HGT", "TMP", "RH"]
    assert [r.level for r in records] == ["1000 mb"] * 3


def test_parse_idx_ends_the_final_record_at_the_object_boundary():
    """The last record has no successor, so only the object length bounds it.

    This is why the route pays for a one-byte range probe: an inventory alone
    cannot say where its final message stops.
    """
    records = rrfs_nomads.parse_idx(
        "1:0:d=2026090100:HGT:1000 mb:1 hour fcst:", 4096
    )

    assert records[-1].end == 4095
    assert records[-1].size == 4096


def test_parse_idx_shares_the_next_distinct_offset_across_duplicate_starts():
    """Repeated offsets must not produce a zero or negative length."""
    text = "\n".join([
        "1:0:d=2026090100:UGRD:1000 mb:1 hour fcst:",
        "2:0:d=2026090100:VGRD:1000 mb:1 hour fcst:",
        "3:500:d=2026090100:HGT:1000 mb:1 hour fcst:",
    ])

    records = rrfs_nomads.parse_idx(text, 800)

    assert [(r.start, r.end) for r in records[:2]] == [(0, 499), (0, 499)]
    assert all(record.size > 0 for record in records)


def test_parse_idx_sorts_an_out_of_order_inventory():
    text = "\n".join([
        "3:250:d=2026090100:RH:1000 mb:1 hour fcst:",
        "1:0:d=2026090100:HGT:1000 mb:1 hour fcst:",
        "2:100:d=2026090100:TMP:1000 mb:1 hour fcst:",
    ])

    records = rrfs_nomads.parse_idx(text, 400)

    assert [r.number for r in records] == [1, 2, 3]
    assert all(record.size > 0 for record in records)


def test_parse_idx_ignores_blank_and_unparsable_lines():
    text = "\n".join([
        "",
        "not an index row",
        "1:0:d=2026090100:HGT:1000 mb:1 hour fcst:",
        "   ",
    ])

    records = rrfs_nomads.parse_idx(text, 10)

    assert len(records) == 1


@pytest.mark.parametrize("text,total", [
    ("", 100),
    ("no usable rows", 100),
    ("1:0:d=2026090100:HGT:1000 mb:1 hour fcst:", 0),
    ("1:0:d=2026090100:HGT:1000 mb:1 hour fcst:", -5),
])
def test_parse_idx_refuses_an_inventory_it_cannot_bound(text, total):
    with pytest.raises(rrfs_nomads.RrfsUnavailable):
        rrfs_nomads.parse_idx(text, total)


def test_parse_idx_refuses_offsets_past_the_object_length():
    """A truncated object must fail loudly rather than request a bad range."""
    text = "\n".join([
        "1:0:d=2026090100:HGT:1000 mb:1 hour fcst:",
        "2:9000:d=2026090100:TMP:1000 mb:1 hour fcst:",
    ])

    with pytest.raises(rrfs_nomads.RrfsUnavailable):
        rrfs_nomads.parse_idx(text, 4096)


# --- published URL shape ---------------------------------------------------


@pytest.mark.parametrize("domain,expected", [
    ("conus", "rrfs.t00z.prslev.3km.f001.conus.grib2"),
    ("alaska", "rrfs.t00z.prslev.3km.f001.ak.grib2"),
    ("hawaii", "rrfs.t00z.prslev.2p5km.f001.hi.grib2"),
    ("puerto rico", "rrfs.t00z.prslev.2p5km.f001.pr.grib2"),
    ("north america", "rrfs.t00z.prslev.13km.f001.na.grib2"),
])
def test_build_url_matches_the_published_file_names(domain, expected):
    """Pin the exact layout, since a stale one is what made Herbie unusable."""
    url = rrfs_nomads.build_url(
        "para", RUN, 1, rrfs_nomads.PRESSURE_PRODUCT, domain
    )

    assert url == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/para/"
        "rrfs.20260901/00/" + expected
    )


def test_build_url_zero_pads_the_forecast_hour():
    url = rrfs_nomads.build_url(
        "prod", RUN, 84, rrfs_nomads.SURFACE_PRODUCT, "conus"
    )

    assert url.endswith("rrfs.t00z.2dfld.3km.f084.conus.grib2")
    assert "/prod/" in url


@pytest.mark.parametrize("name,tag", [
    ("conus", "conus"),
    ("CONUS", "conus"),
    ("  Alaska ", "ak"),
    ("puerto rico", "pr"),
    ("puerto-rico", "pr"),
    ("Puerto_Rico", "pr"),
    ("north  america", "na"),
])
def test_domain_for_normalizes_configured_tags(name, tag):
    assert rrfs_nomads.domain_for(name).tag == tag


def test_domain_for_rejects_an_unpublished_domain():
    with pytest.raises(ValueError, match="unknown RRFS domain"):
        rrfs_nomads.domain_for("atlantis")


def test_domain_for_passes_through_a_resolved_domain():
    item = rrfs_nomads.domain_for("hawaii")

    assert rrfs_nomads.domain_for(item) is item


# --- cycle contract --------------------------------------------------------


def test_only_the_synoptic_cycles_publish_pressure_levels():
    """Off-hour cycles publish a sub-hourly 2-D product and no sounding."""
    assert rrfs_nomads.PRESSURE_CYCLES == (0, 6, 12, 18)
    for hour in range(24):
        assert rrfs_nomads.publishes_pressure_levels(hour) is (
            hour in (0, 6, 12, 18)
        )


@pytest.mark.parametrize("value", [None, "", "noon", object()])
def test_publishes_pressure_levels_rejects_junk_without_raising(value):
    assert rrfs_nomads.publishes_pressure_levels(value) is False


def test_fetch_pair_refuses_an_off_hour_cycle_before_any_request(tmp_path):
    """The guard must fire without a session, a directory, or a request."""
    def explode(*_args, **_kwargs):
        raise AssertionError("no request may be made for an off-hour cycle")

    with pytest.raises(rrfs_nomads.RrfsUnavailable, match="sub-hourly"):
        rrfs_nomads.fetch_pair(
            "rrfs-a", "conus",
            datetime(2026, 9, 1, 5, tzinfo=timezone.utc), 1,
            download_dir=tmp_path / "never-created",
            session=type("S", (), {"get": explode, "close": explode})(),
        )
    assert not (tmp_path / "never-created").exists()


# --- field planning --------------------------------------------------------


def _prslev_inventory():
    """Build an inventory shaped like a real RRFS prslev index."""
    levels = [1000, 975, 950, 925, 900, 100, 50, 2]
    variables = ("HGT", "TMP", "RH", "SPFH", "DPT", "UGRD", "VGRD",
                 "DZDT", "ABSV", "CLMR")
    rows, offset, number = [], 0, 1
    for level in levels:
        for variable in variables:
            rows.append(
                f"{number}:{offset}:d=2026090100:{variable}:{level} mb:"
                "1 hour fcst:"
            )
            offset += 1000
            number += 1
    # Level-only records that must not vote on the pressure plan.
    for variable in ("PRES", "PWAT"):
        rows.append(
            f"{number}:{offset}:d=2026090100:{variable}:"
            "30-0 mb above ground:1 hour fcst:"
        )
        offset += 1000
        number += 1
    return rrfs_nomads.parse_idx("\n".join(rows), offset)


def test_pressure_field_plan_keeps_absv_and_drops_unusable_dzdt():
    """DZDT is geometric m/s; omeg is Pa/s, so those bytes cannot be read."""
    records = _prslev_inventory()

    search, fields = rrfs_nomads.pressure_field_plan(records)

    assert "ABSV" in fields
    assert "DZDT" not in fields
    assert "VVEL" not in fields
    assert rrfs_nomads.UNUSABLE_PRESSURE_FIELDS == frozenset({"DZDT"})
    selected = rrfs_nomads.select_records(records, search)
    assert selected
    assert {record.variable for record in selected} == set(fields)
    # Every selected record is isobaric; nothing level-only slipped in.
    assert all(record.level.endswith(" mb") for record in selected)


def test_pressure_field_plan_selects_every_published_level():
    records = _prslev_inventory()

    search, fields = rrfs_nomads.pressure_field_plan(records)
    selected = rrfs_nomads.select_records(records, search)

    levels = {record.level for record in selected}
    assert len(levels) == 8
    assert "2 mb" in levels and "1000 mb" in levels
    assert len(selected) == len(fields) * 8


def test_surface_search_selects_exactly_the_verified_ground_records():
    """The 2dfld product is the only published source of the ground row."""
    rows = [
        "1:0:d=2026090100:REFC:entire atmosphere:1 hour fcst:",
        "2:1000:d=2026090100:PRES:surface:1 hour fcst:",
        "3:2000:d=2026090100:HGT:surface:1 hour fcst:",
        "4:3000:d=2026090100:TMP:2 m above ground:1 hour fcst:",
        "5:4000:d=2026090100:SPFH:2 m above ground:1 hour fcst:",
        "6:5000:d=2026090100:DPT:2 m above ground:1 hour fcst:",
        "7:6000:d=2026090100:RH:2 m above ground:1 hour fcst:",
        "8:7000:d=2026090100:UGRD:10 m above ground:1 hour fcst:",
        "9:8000:d=2026090100:VGRD:10 m above ground:1 hour fcst:",
        "10:9000:d=2026090100:TMP:80 m above ground:1 hour fcst:",
        "11:10000:d=2026090100:PRES:cloud base:1 hour fcst:",
    ]
    records = rrfs_nomads.parse_idx("\n".join(rows), 11000)

    selected = rrfs_nomads.select_records(records, NOAA_SURFACE_SEARCH)

    assert [record.number for record in selected] == [2, 3, 4, 5, 6, 7, 8, 9]


def test_the_combined_field_union_completes_the_surface_contract():
    """A union that fails this check would be rejected downstream as stale."""
    _search, pressure_fields = rrfs_nomads.pressure_field_plan(
        _prslev_inventory()
    )
    surface_fields = ("PRES", "HGT", "TMP", "SPFH", "DPT", "RH",
                      "UGRD", "VGRD")

    union = tuple(dict.fromkeys((*pressure_fields, *surface_fields)))

    assert supports_noaa_surface_merge(union)
    # Neither half completes the contract alone, which is why both are fetched.
    assert not supports_noaa_surface_merge(pressure_fields)


# --- release directory resolution ------------------------------------------


def test_release_candidates_prefer_operational_then_parallel():
    """Probing prod first migrates the route on implementation day."""
    assert rrfs_nomads.RELEASE_PATHS[0] == "prod"
    assert set(rrfs_nomads.RELEASE_PATHS) == {"prod", "para", "v1.0"}
    assert rrfs_nomads._release_candidates() == rrfs_nomads.RELEASE_PATHS


def test_a_remembered_release_is_tried_first_but_others_remain():
    """The winner is cached, yet a later removal must still be recoverable."""
    rrfs_nomads._remember_release("para")

    candidates = rrfs_nomads._release_candidates()

    assert candidates[0] == "para"
    assert set(candidates) == set(rrfs_nomads.RELEASE_PATHS)
    assert len(candidates) == len(rrfs_nomads.RELEASE_PATHS)


# --- provenance sidecar ----------------------------------------------------


def test_provenance_round_trips_the_fields_actually_fetched(tmp_path):
    combined = tmp_path / "rrfs-a-2026090100-f001-verified-surface.grib2"

    rrfs_nomads.write_provenance(
        combined,
        fields=("HGT", "TMP", "PRES", "RH"),
        source_url="https://example.invalid/a;https://example.invalid/b",
        pressure_fields=("HGT", "TMP", "RH"),
    )
    recorded = rrfs_nomads.read_provenance(combined)

    assert recorded == {
        "fields": ("HGT", "TMP", "PRES", "RH"),
        "pressure_fields": ("HGT", "TMP", "RH"),
        "source_url": "https://example.invalid/a;https://example.invalid/b",
    }
    assert rrfs_nomads.provenance_path(combined).name == (
        combined.name + ".provenance.json"
    )


def test_reading_provenance_reports_absence_rather_than_raising(tmp_path):
    assert rrfs_nomads.read_provenance(tmp_path / "missing.grib2") is None


@pytest.mark.parametrize("payload", [
    "{not json",
    json.dumps({"version": 2, "fields": ["HGT"],
                "pressure_fields": ["HGT"], "source_url": "u"}),
    json.dumps({"version": 1, "fields": [],
                "pressure_fields": ["HGT"], "source_url": "u"}),
    json.dumps({"version": 1, "fields": ["HGT"],
                "pressure_fields": [], "source_url": "u"}),
    json.dumps({"version": 1, "fields": ["HGT"],
                "pressure_fields": ["HGT"], "source_url": ""}),
    json.dumps({"version": 1, "fields": ["HGT"],
                "pressure_fields": ["HGT"]}),
    json.dumps(["not", "a", "mapping"]),
])
def test_a_damaged_sidecar_is_treated_as_absent(tmp_path, payload):
    """An unusable sidecar must trigger a real fetch, not a wrong provenance."""
    combined = tmp_path / "combined.grib2"
    rrfs_nomads.provenance_path(combined).write_text(payload, encoding="utf-8")

    assert rrfs_nomads.read_provenance(combined) is None


def test_writing_provenance_leaves_no_temporary_file_behind(tmp_path):
    combined = tmp_path / "combined.grib2"

    rrfs_nomads.write_provenance(
        combined, fields=("HGT",), source_url="u", pressure_fields=("HGT",)
    )

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "combined.grib2.provenance.json"
    ]


# --- transport budget ------------------------------------------------------


def test_the_range_merge_budget_stays_tight_enough_for_large_domains():
    """The shared 25-percent budget would waste tens of MB on a CONUS plan."""
    assert rrfs_nomads.MAX_RANGE_OVERHEAD_RATIO == 0.05
    assert rrfs_nomads.MAX_RANGE_GAP_BYTES == 512 * 1024
    assert rrfs_nomads.DEFAULT_RANGE_WORKERS == 8
