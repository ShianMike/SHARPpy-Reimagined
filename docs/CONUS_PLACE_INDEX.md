# Offline CONUS locator-title index

SHARPpy Reimagined bundles a compact place index used for the title above the
hodograph locator map. It does not draw town names inside the map.

The index is generated from the U.S. Census Bureau's annual national
[Gazetteer Files][census-gazetteers]:

- Places, covering incorporated places and census-designated places.
- County subdivisions whose legal name is a town or township, improving rural
  coverage in states that use those entities.

Each record stores the Census representative point and `ALAND_SQMI` land area.
The resolver converts that area into a conservative circular footprint before
falling back to raw nearest-point distance. This keeps a large city such as
Denver associated with its downtown even when a tiny enclave's representative
point happens to be closer, while the enclave still wins close to its own
center. The proxy is deterministic and requires no map-label network request.

The same bundled file contains the Census
[Cartographic Boundary File][census-boundaries] outlines for all 48 contiguous
states and the District of Columbia. That geographic mask prevents nearby
points in Canada, Mexico, the Atlantic, or the Gulf of Mexico from inheriting
the nearest U.S. town name. Alaska, Hawaii, Puerto Rico, and the island areas
are excluded.

The bundled index is tried before the online resolver so opening a normal CONUS
sounding never waits on a geocoding request. The online fallback is also
limited to this mask and accepts settlement fields such as city, town, village,
municipality, or hamlet—not county names. Online answers, deterministic
offline results, and failed/empty results are cached with separate expiration
times so outages do not trigger repeated requests and old answers are
eventually refreshed.

The bundled metadata records the resource schema, source year, source URLs,
input hashes, output hash, place fields/counts, and boundary polygon counts.
The reader remains compatible with legacy five-column records that do not
contain land area.

The locator's map context is a separate `conus-counties.zip` resource built
from the Census national 1:500,000 County Cartographic Boundary File. County
edges are clipped and deduplicated into independently compressed one-degree
tiles with delta-encoded coordinates. A local locator reads only its
intersecting tiles, keeping paint latency and memory bounded while preserving
useful county context well inside a state. The tile payload contains geometry
only—never county or town labels. Its companion metadata records the source
year, official URL, source/archive SHA-256 hashes, scale, encoding, and geometry
counts.

## Refreshing

Run:

```powershell
.\.gribenv\Scripts\python.exe scripts\build_conus_place_index.py --year 2025
.\.gribenv\Scripts\python.exe scripts\build_conus_county_tiles.py --year 2025
```

For a newer annual Census release, update the year. The builder validates that
all 48 contiguous states plus the District of Columbia are represented and
rejects unexpectedly sparse inputs.

The lookup first verifies that the coordinate falls in a bundled state polygon,
then prefers a plausible area footprint before using the nearest Census
representative point. Online reverse geocoding is only attempted when the
bounded offline search has no result.

[census-gazetteers]: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
[census-boundaries]: https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html
