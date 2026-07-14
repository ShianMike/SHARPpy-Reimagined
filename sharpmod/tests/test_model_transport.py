"""Optimized forecast-model field and byte-range transport regressions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from sharpmod.model_fields import (
    build_noaa_search,
    choose_noaa_fields,
)
from sharpmod.model_transport import (
    ByteRange,
    DownloadCancelled,
    OptimizedTransportUnavailable,
    download_herbie_subset,
    download_ranges,
    plan_ranges,
)


def _inventory(*variables):
    return pd.DataFrame({"variable": list(variables)})


def test_noaa_search_keeps_levels_but_prunes_equivalent_fields():
    inventory = _inventory(
        "HGT", "TMP", "RH", "SPFH", "UGRD", "VGRD",
        "VVEL", "DZDT", "ABSV",
    )

    fields = choose_noaa_fields(inventory)

    assert fields == (
        "HGT", "TMP", "UGRD", "VGRD", "RH", "VVEL", "ABSV"
    )
    search = build_noaa_search(fields)
    assert ":SPFH:" not in search
    assert ":DZDT:" not in search
    assert "\\d+(?:\\.\\d+)? mb:" in search


def test_noaa_search_falls_back_to_specific_humidity_and_dzdt():
    fields = choose_noaa_fields(
        _inventory("HGT", "TMP", "SPFH", "UGRD", "VGRD", "DZDT")
    )

    assert fields == ("HGT", "TMP", "UGRD", "VGRD", "SPFH", "DZDT")


def test_noaa_search_requires_sounding_core_fields():
    with pytest.raises(ValueError, match="missing required pressure fields"):
        choose_noaa_fields(_inventory("HGT", "TMP", "RH", "UGRD"))


def test_range_plan_merges_small_gaps_under_global_overhead_budget():
    rows = [(0, 99), (120, 199), (5000, 5099)]

    assert plan_ranges(
        rows, max_gap=64, max_overhead_ratio=0.25
    ) == [ByteRange(0, 199), ByteRange(5000, 5099)]


def test_range_plan_does_not_spend_more_than_global_overhead_budget():
    rows = [(0, 99), (120, 219), (240, 339), (360, 459)]

    planned = plan_ranges(rows, max_gap=64, max_overhead_ratio=0.10)

    assert planned == [ByteRange(0, 339), ByteRange(360, 459)]


def test_range_plan_rejects_open_or_invalid_ranges():
    with pytest.raises(OptimizedTransportUnavailable, match="ending byte"):
        plan_ranges([(0, None)])
    with pytest.raises(OptimizedTransportUnavailable, match="invalid byte range"):
        plan_ranges([(100, 99)])


def test_range_plan_consolidates_shared_vector_message_offsets():
    assert plan_ranges(
        [(100, 99), (100, 199), (200, 249)]
    ) == [ByteRange(100, 249)]


@dataclass
class _Response:
    status_code: int
    content: bytes
    headers: dict[str, str]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")

    def iter_content(self, chunk_size=64 * 1024):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _RangeSession:
    def __init__(self, payload, ignore_ranges=False):
        self.payload = payload
        self.ignore_ranges = ignore_ranges
        self.requests = []

    def get(self, _url, headers, stream, timeout):
        assert stream is True
        assert timeout
        value = headers["Range"].removeprefix("bytes=")
        start, end = (int(part) for part in value.split("-"))
        self.requests.append((start, end))
        if self.ignore_ranges:
            return _Response(200, self.payload, {})
        data = self.payload[start:end + 1]
        return _Response(
            206,
            data,
            {
                "Content-Range": f"bytes {start}-{end}/{len(self.payload)}",
                "ETag": '"fixture"',
            },
        )


def test_completed_fragment_is_reused_and_output_is_atomic(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    session = _RangeSession(payload)
    ranges = [ByteRange(0, len(payload) - 1)]
    output = tmp_path / "subset.grib2"

    download_ranges(session, "https://data/file", ranges, output)
    download_ranges(session, "https://data/file", ranges, output)

    assert output.read_bytes() == payload
    assert session.requests == [(0, len(payload) - 1)]
    assert not list(tmp_path.glob("*.tmp"))


def test_partial_fragment_resumes_from_existing_size(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    output = tmp_path / "subset.grib2"
    fragments = tmp_path / ".subset.grib2.ranges"
    fragments.mkdir()
    (fragments / f"0-{len(payload) - 1}.part").write_bytes(payload[:9])
    session = _RangeSession(payload)

    download_ranges(
        session,
        "https://data/file",
        [ByteRange(0, len(payload) - 1)],
        output,
    )

    assert output.read_bytes() == payload
    assert session.requests == [(9, len(payload) - 1)]


def test_server_that_ignores_ranges_uses_fallback_exception(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    session = _RangeSession(payload, ignore_ranges=True)

    with pytest.raises(OptimizedTransportUnavailable, match="HTTP range"):
        download_ranges(
            session,
            "https://data/file",
            [ByteRange(0, len(payload) - 1)],
            tmp_path / "subset.grib2",
        )


def test_cancellation_preserves_resumable_fragment(tmp_path):
    payload = b"GRIB" + (b"x" * 100_000) + b"7777"
    session = _RangeSession(payload)
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(DownloadCancelled):
        download_ranges(
            session,
            "https://data/file",
            [ByteRange(0, len(payload) - 1)],
            tmp_path / "subset.grib2",
            cancelled=cancelled,
            chunk_size=4096,
        )

    fragments = tmp_path / ".subset.grib2.ranges"
    assert any(path.stat().st_size > 0 for path in fragments.glob("*.part"))


def test_herbie_subset_uses_planned_local_filename(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    session = _RangeSession(payload)

    class _Herbie:
        grib = "https://data/file"
        save_dir = tmp_path

        def inventory(self, search):
            assert search == ":chosen:"
            return pd.DataFrame({
                "start_byte": [0],
                "end_byte": [len(payload) - 1],
            })

        def get_localFilePath(self, search):
            assert search == ":chosen:"
            return Path(self.save_dir) / "subset.grib2"

    result, total = download_herbie_subset(
        _Herbie(), ":chosen:", save_dir=tmp_path, session=session
    )

    assert result == tmp_path / "subset.grib2"
    assert result.read_bytes() == payload
    assert total == len(payload)
