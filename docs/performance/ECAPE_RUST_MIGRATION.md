# Native ECAPE workspace

Measured on 2026-10-30 against `b9d6148d6f7a04c882f80e1235baa88cf4df9ee4`.
This is pending work after v3.5.0; published releases and their assets are unchanged.

## Measured target

The supported Rust backend was active before profiling. Existing native parcel,
wind-layer, GRIB, radar, range-transfer, provider and batch-analysis routines were
not counted as new migrations. Old dev sessions were left running and intact.

The existing `benchmarks/benchmark_rendering.py` measured a real HRRR UHD render
at 4564 × 3077. Its initial first render took 18.49 s wall / 5.86 s CPU, followed
by 2.68–2.76 s repeats. That large initial wall/CPU gap was not reproducible and
is not used as the optimization baseline. A separate cold cProfile trace found
ECAPE importing MetPy/xarray during first derived-index generation. Directly
timing the first reference workspace call gave 2.475 s; warm calls were
0.010–0.063 s. This is a first-sounding target, not a warm-kernel bottleneck.

## Implementation and scientific conventions

`rust/sharpmod-rs/src/ecape/` separates thermodynamics, buoyancy integration and
storm-relative inflow. `ecape_bindings.rs` accepts six owned float64 columns,
validates them at the native boundary and releases the GIL for the workspace.
The existing Python ECAPE entry point has only compatibility routing added.
Its portable Python reference, exact SHARPpy MUCAPE bound, analytic formula,
mask filtering and public missing-value contract retain their existing owners.
The backend capability contract rejects a stale extension without the new call.

The implementation follows the installed MetPy 1.7.1 conventions rather than
substituting the renderer's different SHARPpy parcel workspace:

- Ambaum liquid-water saturation and the specific-humidity/dewpoint round trip.
- Most-unstable parcel selection, including first-on-tie behavior.
- Romps/Lambert-W condensation level and dry/moist parcel ascent.
- Log-pressure intersections, distinct LFC/EL choices and virtual-temperature CAPE.
- The reported-level NCAPE cumulative mean, restricted to the equilibrium level.
- Pressure-integrated Bunkers motion and the reported 0–1 km inflow mean.

Moist ascent uses scalar RK4 in log pressure with a maximum step of 0.005.
No solver crate, thread pool, new Python dependency or test workflow is added.
The independent reference uses LSODA; small solver differences are checked
explicitly, not hidden by changing existing scientific tolerances.

Research used [MetPy's moist-ascent documentation](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.moist_lapse.html),
[its condensation-level documentation](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.lcl.html),
[the versioned reference source](https://github.com/Unidata/MetPy/tree/v1.7.1/src/metpy/calc)
and [Peters et al.'s ECAPE paper](https://arxiv.org/abs/2301.04712).
MetPy's BSD notice is retained in the project and native package notices.

## Alternating complete-render measurements

Three fresh processes per variant, in order before/after/after/before/before/after;
each executes the existing benchmark twice. The before variant restores only
the unchanged base revision's ECAPE workspace in memory. Both variants use the
same native extension for all other calculations. No test/build job ran during
this comparison. File caches are warm; these are application-cold first renders,
not reboot-cold measurements or complete process-startup timings.

Hardware: Intel Core i5-1235U, 10 cores / 12 threads, Windows 11, Balanced power.
Python 3.11.14, NumPy 2.4.6, Qt 6.11.2, release Rust extension, offscreen Qt,
locator disabled and geocoder off. The fixture SHA-256 and all twelve individual
render samples are in [the results JSON](../../benchmarks/results/2026-10-30-ecape-native.json).

| Metric, median of three processes | Before | Rust workspace |
| --- | ---: | ---: |
| First complete UHD render | 5.148 s | 3.367 s |
| Warm complete UHD render | 2.726 s | 2.704 s |
| Initial peak working set | 525.11 MiB | 365.46 MiB |
| Initial private memory | 1124.10 MiB | 626.23 MiB |

The first-render reduction is 1.781 s / 34.6%, with 159.65 MiB less initial peak
working set. Warm rendering is essentially unchanged. Every paired render has
the same dimensions, PNG size and decoded RGBA SHA-256:
`bd1cd1098a3c535f0ee25f7534b11efe48c5be4bcaec21b80123b559c81dd535`.
Peak/private values are process snapshots, not a memory cap or a long-session
leak result. Gains are not claims for every model, machine, image or operation.

## Validation

- Five Rust regression tests use thirteen recorded independent workspaces:
  HRRR, observed OAX, four BUFKIT hours, stable/dry/elevated/calm/saturated cases
  and an extended stratosphere. They also check malformed/short/duplicate inputs,
  parcel-selection ties and sparse profiles. Frozen fixture values are data;
  routine Rust tests do not import Python or MetPy to calculate their expected values.
- All captured parcel traces differ by less than 0.0001 K. CAPE differs by less
  than 0.1 J/kg; NCAPE, inflow and EL heights use tighter independent tolerances.
- An additional deterministic comparison of 134 profiles (fourteen established
  scientific-reference profiles plus 120 perturbed profiles, seed 350) matches
  every missing-value outcome. Maximum ECAPE difference: 0.023533 J/kg.
- Existing ECAPE non-negativity, zero-CAPE, upper-bound and author-reference
  checks pass under both forced Rust and forced Python: eight tests per backend.
- Twenty-two focused backend/renderer checks pass, including full reference PNG
  and metadata parity, exact independent composite oracles, observed/missing
  inputs, profile edits/reset and full-resolution exports.
- Stable format/Clippy and all native targets pass. The five new scientific
  tests also pass on the supported Rust 1.88 MSRV.
- The live development app is checked through Browser with an imported real
  HRRR NPZ. Existing scientific drawing and UI controls remain in their layers.

## Remaining measured work

The warm profile still spends time on Qt composition, repeated calibrated panel
redraws, PNG encoding and the independent upstream convective oracle. The latter
took about 0.97 s for HRRR / 1.30 s for OAX under profiling. Its previous native
source substitution failed strict equivalence, as recorded in
[the historical benchmark report](BENCHMARK_RESULTS.md); it is not reintroduced.

Live logs also contained 30 s UWyo station-list timeouts. A controlled probe
received the same 75,813-byte response through urllib in 1.82 s, while the
existing native transport varied from a 36.94 s retry failure to a 4.58 s warm
success. This does not establish a speed gain from moving that provider to the
native transport; no provider timeout, retry count or data source is changed.
Network variability and transport retry policy need separate measurements.

Raw profiles, XML test reports, provider probes and the Browser capture remain
under the ignored `.test-results/rust-migration/` directory. No reporting jobs,
new matrices, benchmark discovery or release gates are added.
