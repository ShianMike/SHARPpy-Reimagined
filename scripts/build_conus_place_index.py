"""Build the bundled offline CONUS title-place index from Census gazetteers.

The output is intentionally a point index, not a map-label layer. SHARPpy
Reimagined uses it only as a reverse-town fallback for the locator-map title
when the online resolver is disabled or unavailable.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_YEAR = 2025
SOURCE_PAGE = (
    "https://www.census.gov/geographies/reference-files/time-series/"
    "geo/gazetteer-files.html"
)
BOUNDARY_SOURCE_PAGE = (
    "https://www.census.gov/geographies/mapping-files/time-series/geo/"
    "cartographic-boundary.html"
)
URL_TEMPLATE = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "{year}_Gazetteer/{year}_Gaz_{kind}_national.zip"
)
STATE_BOUNDARY_URL_TEMPLATE = (
    "https://www2.census.gov/geo/tiger/GENZ{year}/kml/"
    "cb_{year}_us_state_500k.zip"
)
EXCLUDED_USPS = {"AK", "HI", "PR"}
CONUS_STATE_FIPS = {
    "01", "04", "05", "06", "08", "09", "10", "11", "12", "13", "16",
    "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27",
    "28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38",
    "39", "40", "41", "42", "44", "45", "46", "47", "48", "49", "50",
    "51", "53", "54", "55", "56",
}
STATE_NAMES = {
    "AL": "Alabama",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
_PLACE_SUFFIX_RE = re.compile(
    r"\s+(?:city|village|town|borough|municipality|CDP)$",
    re.IGNORECASE,
)


def _source_url(year: int, kind: str) -> str:
    return URL_TEMPLATE.format(year=year, kind=kind)


def _state_boundary_url(year: int) -> str:
    return STATE_BOUNDARY_URL_TEMPLATE.format(year=year)


def _download(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "SHARPpy-Reimagined place-index builder"},
    )
    with urlopen(request, timeout=60) as response:
        return response.read()


def _load_zip(path: str | None, url: str) -> bytes:
    if path:
        return Path(path).expanduser().read_bytes()
    return _download(url)


def _rows_from_zip(payload: bytes):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.casefold().endswith((".txt", ".csv"))
        ]
        if len(names) != 1:
            raise ValueError(
                f"expected one Census text file in archive, found {names!r}"
            )
        with archive.open(names[0]) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
                yield from csv.DictReader(text, delimiter="|")


def _boundary_polygons_from_zip(
    payload: bytes,
) -> tuple[list[tuple[str, tuple[tuple[float, float], ...]]], set[str]]:
    """Read generalized state polygons from a Census cartographic KML ZIP."""

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.casefold().endswith(".kml")
        ]
        if len(names) != 1:
            raise ValueError(
                f"expected one Census KML file in archive, found {names!r}"
            )
        with archive.open(names[0]) as source:
            root = ET.parse(source).getroot()

    namespace = {"kml": "http://www.opengis.net/kml/2.2"}
    polygons = []
    states = set()
    for placemark in root.findall(".//kml:Placemark", namespace):
        fields = {
            str(item.attrib.get("name", "")): str(item.text or "").strip()
            for item in placemark.findall(".//kml:SimpleData", namespace)
        }
        state_fips = fields.get("STATEFP", "")
        if state_fips not in CONUS_STATE_FIPS:
            continue
        states.add(state_fips)
        for coordinate_node in placemark.findall(
            ".//kml:Polygon/kml:outerBoundaryIs/"
            "kml:LinearRing/kml:coordinates",
            namespace,
        ):
            points = []
            for coordinate in str(coordinate_node.text or "").split():
                fields = coordinate.split(",")
                if len(fields) < 2:
                    continue
                lon = float(fields[0])
                lat = float(fields[1])
                points.append((lon, lat))
            if len(points) >= 4:
                if points[0] != points[-1]:
                    points.append(points[0])
                polygons.append((state_fips, tuple(points)))
    return polygons, states


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\t", " ").split())


def _coordinates(row: dict[str, str]) -> tuple[float, float]:
    lat = float(row["INTPTLAT"])
    lon = float(row["INTPTLONG"])
    if not 20.0 <= lat <= 55.0 or not -130.0 <= lon <= -60.0:
        raise ValueError(f"unexpected CONUS coordinate {lat}, {lon}")
    return lat, lon


def _land_area_sqmi(row: dict[str, str]) -> float:
    area = float(row["ALAND_SQMI"])
    if not math.isfinite(area) or area < 0.0:
        raise ValueError(f"invalid Census land area {area!r}")
    return area


def _place_record(row: dict[str, str]):
    usps = _clean_text(row.get("USPS")).upper()
    if usps in EXCLUDED_USPS or usps not in STATE_NAMES:
        return None
    name = _PLACE_SUFFIX_RE.sub("", _clean_text(row.get("NAME"))).strip()
    if not name:
        return None
    lat, lon = _coordinates(row)
    return (
        f"{name}, {STATE_NAMES[usps]}",
        lat,
        lon,
        "place",
        _clean_text(row.get("GEOID")),
        _land_area_sqmi(row),
        usps,
    )


def _subdivision_record(row: dict[str, str]):
    usps = _clean_text(row.get("USPS")).upper()
    if usps in EXCLUDED_USPS or usps not in STATE_NAMES:
        return None
    raw_name = _clean_text(row.get("NAME"))
    folded = raw_name.casefold()
    if folded.endswith(" town"):
        title = f"Town of {raw_name[:-5].strip()}"
    elif folded.endswith(" township"):
        title = f"{raw_name[:-9].strip()} Township"
    else:
        return None
    lat, lon = _coordinates(row)
    return (
        f"{title}, {STATE_NAMES[usps]}",
        lat,
        lon,
        "subdivision",
        _clean_text(row.get("GEOID")),
        _land_area_sqmi(row),
        usps,
    )


def _gzip_bytes(text: str) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        fileobj=output,
        mode="wb",
        filename="",
        mtime=0,
    ) as compressed:
        compressed.write(text.encode("utf-8"))
    return output.getvalue()


def build(
    *,
    year: int,
    places_zip: str | None,
    subdivisions_zip: str | None,
    state_boundary_zip: str | None,
    output: Path,
    metadata_output: Path,
) -> dict[str, object]:
    sources = {
        "place": (
            _source_url(year, "place"),
            _load_zip(places_zip, _source_url(year, "place")),
        ),
        "cousubs": (
            _source_url(year, "cousubs"),
            _load_zip(subdivisions_zip, _source_url(year, "cousubs")),
        ),
        "state-boundary": (
            _state_boundary_url(year),
            _load_zip(state_boundary_zip, _state_boundary_url(year)),
        ),
    }

    records = []
    for row in _rows_from_zip(sources["place"][1]):
        record = _place_record(row)
        if record is not None:
            records.append(record)
    place_count = len(records)
    for row in _rows_from_zip(sources["cousubs"][1]):
        record = _subdivision_record(row)
        if record is not None:
            records.append(record)

    records = sorted(
        set(records),
        key=lambda item: (item[1], item[2], item[0].casefold(), item[3]),
    )
    states = sorted({record[6] for record in records})
    if len(states) != 49:
        raise ValueError(
            f"expected the lower 48 states plus D.C.; found {len(states)}"
        )
    if len(records) < 50_000 or place_count < 30_000:
        raise ValueError(
            f"unexpectedly sparse Census input: {len(records)} total records"
        )

    boundary_polygons, boundary_states = _boundary_polygons_from_zip(
        sources["state-boundary"][1]
    )
    if boundary_states != CONUS_STATE_FIPS:
        raise ValueError(
            "state boundary input did not contain exactly the lower 48 states "
            f"plus D.C.: {sorted(boundary_states)!r}"
        )
    if len(boundary_polygons) < 100:
        raise ValueError(
            "unexpectedly sparse Census state boundary input: "
            f"{len(boundary_polygons)} polygons"
        )

    lines = [
        "# label\tlatitude\tlongitude\tkind\tgeoid\tland_area_sqmi",
        *(
            f"{label}\t{lat:.6f}\t{lon:.6f}\t{kind}\t{geoid}\t"
            f"{land_area_sqmi:.6f}"
            for label, lat, lon, kind, geoid, land_area_sqmi, _usps
            in records
        ),
        "# @boundary\tstate_fips\tlongitude,latitude ...",
        *(
            "@boundary\t"
            f"{state_fips}\t"
            + " ".join(
                f"{lon:.5f},{lat:.5f}"
                for lon, lat in points
            )
            for state_fips, points in boundary_polygons
        ),
        "",
    ]
    compressed = _gzip_bytes("\n".join(lines))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(compressed)

    metadata = {
        "schema_version": 2,
        "coverage": "48 contiguous U.S. states and District of Columbia",
        "source": (
            "U.S. Census Bureau Gazetteer and Cartographic Boundary Files"
        ),
        "source_page": SOURCE_PAGE,
        "boundary_source_page": BOUNDARY_SOURCE_PAGE,
        "source_year": int(year),
        "record_count": len(records),
        "place_count": place_count,
        "town_or_township_count": len(records) - place_count,
        "state_or_district_count": len(states),
        "states": states,
        "boundary_polygon_count": len(boundary_polygons),
        "boundary_point_count": sum(
            len(points) for _state_fips, points in boundary_polygons
        ),
        "place_record_fields": [
            "label",
            "latitude",
            "longitude",
            "kind",
            "geoid",
            "land_area_sqmi",
        ],
        "output_sha256": hashlib.sha256(compressed).hexdigest(),
        "source_files": [
            {
                "kind": kind,
                "url": url,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for kind, (url, payload) in sources.items()
        ],
        "usage": (
            "State polygons gate lookup to the contiguous United States; "
            "land area supplies a conservative containment proxy before "
            "nearest representative-point fallback; titles are never "
            "rendered as labels inside the map."
        ),
    }
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def _parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    resources = repo_root / "sharpmod" / "resources"
    parser = argparse.ArgumentParser(
        description="Build the bundled offline CONUS locator-title index."
    )
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument(
        "--places-zip",
        help="optional downloaded national Places Gazetteer ZIP",
    )
    parser.add_argument(
        "--subdivisions-zip",
        help="optional downloaded national County Subdivisions Gazetteer ZIP",
    )
    parser.add_argument(
        "--state-boundary-zip",
        help="optional downloaded national State cartographic-boundary KML ZIP",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=resources / "conus-places.tsv.gz",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=resources / "conus-places.metadata.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    metadata = build(
        year=args.year,
        places_zip=args.places_zip,
        subdivisions_zip=args.subdivisions_zip,
        state_boundary_zip=args.state_boundary_zip,
        output=args.output,
        metadata_output=args.metadata_output,
    )
    print(
        "[ok] wrote "
        f"{metadata['record_count']} CONUS title-place records to "
        f"{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
