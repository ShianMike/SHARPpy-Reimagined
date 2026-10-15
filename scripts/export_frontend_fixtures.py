"""Write the golden contract fixtures shared by Rust, TypeScript, and pytest.

Run from the repository root whenever ``sharpmod/resources/uwyo_stations.json``
changes:

    python scripts/export_frontend_fixtures.py

It writes four files under ``src-tauri/contracts/``:

* ``public/stations.catalog.full.json``: every catalogue station as the public
  ``StationCatalog`` DTO, one record per line;
* ``public/stations.search-cases.json``: the ids ``search_stations()`` returns
  for a fixed list of queries, which the frontend search must reproduce;
* ``bridge/stations.catalog.result.seed-fallback.json`` and
  ``public/stations.catalog.seed-fallback.json``: the seed records
  ``load_catalog()`` falls back to.

The render functions have no side effects; ``test_bridge_contract.py`` loads
this module and compares their output with the committed files byte for byte.
Output is ASCII JSON (non-ASCII characters are ``\\uXXXX`` escapes) with LF
line endings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sharpmod.io import uwyo_catalog
from sharpmod.bridge.public import public_station

CONTRACTS = Path(__file__).resolve().parents[1] / "src-tauri" / "contracts"

#: The frontend's search result limit.
SEARCH_LIMIT = 8

#: Exact ids, an id prefix, mixed-case name substrings, blank input, case
#: folding that maps to ASCII, and leading characters where Python's
#: ``strip()`` and JavaScript's ``trim()`` disagree.
SEARCH_QUERIES = (
    "72357",
    "723",
    "norman",
    "NORMAN",
    "",
    "   ",
    "\t\n ",
    "\u00df",
    "\u017f",
    "\ufb01",
    "\x1c72357",
    "\ufeff72357",
    "\u00a072357",
)

GOLDEN_FILES = (
    "public/stations.catalog.full.json",
    "public/stations.search-cases.json",
    "public/stations.catalog.seed-fallback.json",
    "bridge/stations.catalog.result.seed-fallback.json",
)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=True)


def _bridge_station(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("id", "name", "lat", "lon", "src")}


def _catalog_document(stations: list[dict[str, Any]], tail: dict[str, Any]) -> str:
    lines = ["{", '"stations": [']
    lines.extend(
        _dumps(station) + ("," if i < len(stations) - 1 else "")
        for i, station in enumerate(stations)
    )
    lines.append("],")
    items = list(tail.items())
    lines.extend(
        f"{_dumps(key)}: {_dumps(value)}" + ("," if i < len(items) - 1 else "")
        for i, (key, value) in enumerate(items)
    )
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_full_catalog() -> str:
    stations = [public_station(r) for r in uwyo_catalog.all_stations()]
    return _catalog_document(stations, {
        "origin": "bundled",
        "availabilityBasis": {"kind": "catalogOnly"},
        "droppedCount": 0,
    })


def _seed_records() -> list[dict[str, Any]]:
    return sorted(uwyo_catalog._seed_catalog(), key=lambda r: r["id"])


def render_public_seed_fallback() -> str:
    stations = [public_station(r) for r in _seed_records()]
    return _catalog_document(stations, {
        "origin": "seedFallback",
        "availabilityBasis": {"kind": "catalogOnly"},
        "droppedCount": 0,
    })


def render_bridge_seed_fallback() -> str:
    stations = [_bridge_station(r) for r in _seed_records()]
    return _catalog_document(stations, {
        "origin": "seed_fallback",
        "dropped_count": 0,
    })


def render_search_cases() -> str:
    cases = [
        {
            "query": query,
            "limit": SEARCH_LIMIT,
            "ids": [r["id"] for r in uwyo_catalog.search_stations(query, SEARCH_LIMIT)],
        }
        for query in SEARCH_QUERIES
    ]
    lines = ["{", '"cases": [']
    lines.extend(
        _dumps(case) + ("," if i < len(cases) - 1 else "")
        for i, case in enumerate(cases)
    )
    lines.extend(["]", "}"])
    return "\n".join(lines) + "\n"


def render_all() -> dict[str, str]:
    """Return each golden file's path (relative to ``CONTRACTS``) and text."""
    return {
        "public/stations.catalog.full.json": render_full_catalog(),
        "public/stations.search-cases.json": render_search_cases(),
        "public/stations.catalog.seed-fallback.json": render_public_seed_fallback(),
        "bridge/stations.catalog.result.seed-fallback.json":
            render_bridge_seed_fallback(),
    }


def main() -> int:
    for relative, text in render_all().items():
        path = CONTRACTS / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"wrote {path.relative_to(CONTRACTS.parents[1])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
