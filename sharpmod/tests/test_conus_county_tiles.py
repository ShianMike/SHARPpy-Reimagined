"""Builder and resource-format regressions for offline county context."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

from scripts import build_conus_county_tiles as builder


def test_segment_clipping_stays_inside_tile():
    clipped = builder._clip_segment(
        (-96.5, 36.5),
        (-94.5, 36.5),
        (-96.0, 36.0, -95.0, 37.0),
    )

    assert clipped == ((-96.0, 36.5), (-95.0, 36.5))


def test_unique_tile_edges_merge_into_one_deterministic_path():
    edges = {
        ((-9_600_000, 3_650_000), (-9_550_000, 3_650_000)),
        ((-9_550_000, 3_650_000), (-9_500_000, 3_650_000)),
    }

    first = builder._tile_lines(edges)
    second = builder._tile_lines(set(reversed(sorted(edges))))

    assert first == second
    assert first == ((
        (-9_600_000, 3_650_000),
        (-9_550_000, 3_650_000),
        (-9_500_000, 3_650_000),
    ),)


def test_bundled_archive_is_deterministic_and_independently_compressed():
    root = Path(__file__).resolve().parents[2]
    archive_path = root / "sharpmod" / "resources" / "conus-counties.zip"

    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        manifest = json.loads(archive.read("manifest.json"))

    assert [entry.filename for entry in entries] == sorted(
        entry.filename for entry in entries)
    assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in entries)
    assert all(
        entry.compress_type == zipfile.ZIP_DEFLATED for entry in entries)
    assert manifest["tile_count"] == len(entries) - 1
    assert manifest["format_version"] == builder.FORMAT_VERSION
