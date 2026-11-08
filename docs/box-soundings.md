# Geographic box soundings

In **Model Viewer**, choose a model, run and forecast hour, then choose **Box
sounding** in the map toolbar. Drag to draw a geographic rectangle, drag inside
to move it, or drag its corners to resize it. Corner buttons also accept arrow
keys, with Shift for larger steps. Hold Space to pan. With the map canvas
focused, Enter creates a small editable box at the map centre. Clear the box
to start over, or return to **Point sounding** for ordinary map selection.

Press Escape to cancel an in-progress draw, move or corner resize and keep the
committed region. Losing window focus also cancels the edit. Interrupted pointer
capture restores map panning, and a second touch cannot replace the active box
gesture. Corner arrow shortcuts leave browser Alt/Ctrl/Command shortcuts alone.

Generate opens a normal sounding in the existing Soundings workspace. The
Skew-T, hodograph, parcels, tables and image layout are unchanged. Return to the
map to regenerate the region. The selection panel shows geographic dimensions
and model resolution; the exact selected and valid column counts appear after
extraction. A very small region can legitimately overlap just one model cell.

## Method

This uses actual native GRIB columns, not a collection of point-provider API
samples. There is no horizontal atmospheric regrid. Each selected message is
opened once and ecCodes reads all selected native indexes in one vector call.
The existing decoder supplies field units, missing masks, earth-relative winds
and verified surface rows.

For native cell `i`, its weight is the physical area of its intersection with
the geographic rectangle. Regular latitude/longitude cell areas use spherical
meridians and parallels. Lambert cells use the model's spherical projection,
with eight subdivisions per edge before geographic clipping and spherical
area integration. This approximates curved cell boundaries without resampling
the atmospheric fields. Thus the weights include latitude, projection scale
and partial boundary-cell coverage.

The common base is the **minimum surface pressure of all valid contributing
columns**: a pressure surface at or above every contributing ground. Heights
are averaged on that same surface. This deliberately avoids treating different
terrain elevations' 2 m/10 m conditions as one physical surface. In mountainous
regions the representative base may be well above lower terrain; a smaller
box is appropriate when a near-ground local profile is needed.

Above that base, the vertical coordinate is the union of native pressures.
Between adjacent valid levels, height, temperature, specific humidity and U/V
are interpolated linearly in log pressure. There is no extrapolation, no
below-ground sampling and no interpolation across a missing bounding value.
Specific humidity is obtained using the decoder's existing Magnus convention;
dew point is reconstructed from averaged specific humidity and pressure.
Wind components are averaged separately before rebuilding direction and speed.
Dew point, relative humidity, wind direction and independently computed CAPE
or other diagnostics are not spatially averaged.

At each level, joint availability of height, temperature, moisture and both
wind components must cover at least **80% of the requested physical area** by
default. The remaining valid weights are normalized. Coverage is relative to
the requested rectangle, including any part outside the model domain. All
selected columns and per-level coverage are recorded in the existing JSON
sidecar, together with the bounds and method version. Optional omega and
pressure-aligned vorticity remain unavailable without adequate coverage.
Profiles with insufficient coverage, inconsistent increasing heights, or a
supersaturated area mean are refused with an actionable error.

The resulting profile enters the existing portable NPZ, quality-control,
SHARPpy analysis and renderer path. Diagnostics are recalculated from this
profile. The native ground merge accepts and clamps rounding-scale surface
supersaturation up to 0.5 C, matching the existing point-sounding contract.

The Skew-T also draws thin temperature/dew-point traces and hodographs from the
individual native columns behind the thicker mean. Up to 64 valid columns are
shown; larger selections use 64 evenly spaced native scan-order quantiles,
including the first and last valid cells. This is spatial variability, not a
probabilistic forecast ensemble. The averaging and all diagnostics still use
every contributing column. Display traces keep their own native pressure levels,
terrain and missing masks; they are not copies of the mean.
Surface labels and wind barbs belong to the mean only, avoiding dozens of
overlapping annotations from the background columns.

These bounded traces are saved as numeric, non-pickled NPZ arrays and restored
only into the Rust-backed renderer's private collection. Opening the portable file retains
the spread even without the JSON sidecar. Analysis workspaces continue to see
one mean profile. Older saved box soundings without trace data remain readable
and show their original mean; regenerate one to include the native traces.

## Supported data and limits

| Source/grid | Availability |
| --- | --- |
| HRRR and RAP spherical Lambert grids | Native extraction supported; verified with current pressure and surface products. |
| GFS regular latitude/longitude | Supported; verified with a current pressure and surface product. |
| ECMWF 9 km 0.1-degree open-data grid | Supported; verified with its existing verified-surface companion adapter. Available levels remain limited to what that feed publishes. |
| Archived NAM/HiresW on the supported native grids | Decoder definitions are retained. Model retirement filters remain in place; these models are not restored to normal selection. |
| Other existing GRIB providers | Accepted when their existing adapter supplies a complete pressure ladder and verified surface on one of the supported grids. |
| Open-Meteo and ECCC point APIs | Box mode is unavailable; these sources cannot supply native grid-cell areas and columns. |
| Reduced/rotated Gaussian, polar stereographic, oblate-Earth or mixed pressure/surface grids | Explicitly unsupported by this initial area geometry adapter. A nearest-point approximation is not substituted. |

Default limits are 4,096 overlapping columns and 1,000,000 square kilometres.
Server operators can set `SHARPMOD_BOX_MAX_COLUMNS` (1–65,536),
`SHARPMOD_BOX_MAX_AREA_KM2`, and `SHARPMOD_BOX_MIN_COVERAGE` (0.5–1.0).
The native boundary validates these settings and geographic bounds. Selections
span at most 180 degrees of longitude, including antimeridian crossings.

Model-hour leases own downloaded data and use the existing transfer and cleanup
policy. A box cannot accidentally borrow a coordinate-specific point subset.
A bounded 16-profile native cache includes the full compressed-source SHA-256,
box bounds, method version, coverage/work limits and cache generation. It stores
compact profiles, not decoded full grids. Cache clearing invalidates reuse;
concurrent identical boxes share the completed profile after the ecCodes lock.
Cancellation is checked while waiting for that lock, reading messages, selecting
cells, aggregating levels and before registration. A changed selection/model/
run/hour or cancelled request cannot replace a newer frontend selection.

## Validation and measured performance

Focused tests cover physical cell overlap and scanning order, longitude wrapping,
Lambert round trips, log-pressure alignment, moisture conservation, vector winds,
terrain, missing data, coverage limits, source/geometry/settings cache identity,
cancellation, concurrent duplicate work and strict desktop/browser contracts.
Native HRRR, GFS and ECMWF indexes and column values were checked against
ecCodes and the existing point decoder. The saved ordinary HRRR point profile
remained bit-for-bit identical after the change.

Identical columns preserve the original matrix to 1e-10 in the synthetic tests.
An existing unstable HRRR example with about 3,000 J/kg surface CAPE preserves
parcel diagnostics, SRH, shear and storm motion within 1e-7 relative tolerance.
A real single-cell GFS average also preserves the established SHARPpy diagnostic
pipeline within 1e-6 relative tolerance, including missing-value availability.
An unavailable effective inflow layer stays masked instead of producing
artificially enormous wind vectors.

The following measurements include the individual grid traces. They were made
locally on Windows using the existing release-mode native extension and current
HRRR data. They are not Azure or hosted speed claims. OS file-cache state and
other activity on this shared development machine were uncontrolled.

| Native columns | Native extraction with application caches cleared | Native exact profile repeat | Full app: cached source/profile, new entry + analysis + HD image | HD rendering portion |
| ---: | ---: | ---: | ---: | ---: |
| 4 | 7.72 s | 0.200 s | 4.28 s | 3.20 s |
| 9 | 8.68 s | 0.179 s | 4.52 s | 3.12 s |
| 25 | 6.67 s | 0.199 s | 5.56 s | 4.59 s |
| 100 | 5.98 s | 0.170 s | 12.02 s | 10.73 s |
| 400 | 9.34 s | 0.231 s | 8.96 s | 7.97 s |

The full-app repeat creates a new sounding entry and runs analysis and rendering;
it is not redisplaying a previous image. The same entry's cached PNG request
took 73–97 ms including HTTP sampling overhead. The 100- and 400-column cases
both draw 64 individual columns. These are single observations, not controlled
scaling or speedup claims.

The initial full-app pass with traces took 35.77, 14.19, 30.76, 24.27 and 23.30 s
for 4, 9, 25, 100 and 400 columns. Its first request included 21.56 s of source
acquisition. A native build ran during that pass, so it is recorded separately
from the later repeat pass. Source acquisition, process startup and background
load should not be confused with steady-state extraction or rendering cost.

Native unpack/read work took 5.00–7.56 s and column assembly 0.31–0.57 s.
Interpolation plus averaging took 0.04–1.77 ms. The native test-process lifetime
peak working set was 492–498 MiB; the later live backend/renderer repeat pass
peaked around 584 MiB. Live memory is sampled every 50 ms and can miss short
peaks. The original mean-only render repeats took 2.96–3.65 s; plotting the
additional real columns has an explicit rendering cost and is bounded at 64.

The pre-change point baseline on a saved HRRR fixture was 8.69 s for native
decoding with application caches cleared and 0.15–0.25 s for exact repeats.
The current app's real HRRR point open took 10.03 s initially and 6.97 s on a
repeat, using its point-Zarr route. These different source paths should not
be presented as a direct point-versus-box speed comparison.

To repeat the native timing check, set `SHARPMOD_GRIB_BENCH_FIXTURE` to a complete
pressure-plus-surface HRRR file, `SHARPMOD_ECCODES_LIBRARY` to the active ecCodes
library, and `SHARPMOD_BOX_BENCH_OUTPUT` to an output JSON path, then run:

```text
cargo test --release --locked --manifest-path rust/sharpmod-rs/Cargo.toml measure_native_boxes --lib -- --ignored --nocapture
```

For the exact native-index/point-column comparison, run the ignored
`configured_native_grid` test with the same fixture/library variables and set
`SHARPMOD_BOX_REQUIRE_SURFACE=1` for surface, cache and concurrency assertions.
No external provider's private area-averaging algorithm is assumed identical.

References: [MetPy log interpolation](https://unidata.github.io/MetPy/latest/api/generated/metpy.interpolate.log_interpolate_1d.html),
[physical-area weighting](https://docs.xarray.dev/en/latest/examples/area_weighted_temperature.html),
[ecCodes data access](https://sites.ecmwf.int/docs/eccodes/group__get__set.html),
and [MapLibre draggable controls](https://maplibre.org/maplibre-gl-js/docs/examples/create-a-draggable-marker/).
