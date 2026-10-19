# Performance architecture decisions and research

Status: research and baseline stage, 2026-10-19. Decisions below describe the
current constraints, not a completed migration or a release approval.

## D01 — retain the existing scientific facade and native ownership

The existing PyO3/NumPy backend already implements coarse parcel, DCAPE,
kinematic and batch workspaces. Reuse it and identify remaining orchestration
cost before adding another numerical engine. Rust work that detaches from
Python must own its inputs; an immutable Python/NumPy borrow does not make
concurrent mutation impossible. Preserve current floating-point conventions,
masked/missing values and tested Python fallback.

Research: [PyO3 0.29 parallelism](https://pyo3.rs/v0.29.0/parallelism.html),
[rust-numpy read-only borrowing](https://docs.rs/numpy/0.29.0/numpy/borrow/struct.PyReadonlyArray.html),
[NumPy thread safety](https://numpy.org/doc/stable/reference/thread_safety.html).
Adding free-threaded Python is an evaluation topic, not an assumption that the
current Qt/ecCodes/dependency stack supports it safely.

## D02 — profile the reference renderer before replacing it

Qt has optimized raster paths and retained bitmap caches. The current headless
worker reuses imports but reconstructs widgets for each render. First separate
scientific/oracle work, composition, redraw, encoding and transport. Keep the
reference and all calibrated axes/export density until an alternative proves
visual, numerical and interaction equivalence. PNG compression is lossless but
has CPU/size tradeoffs; do not lower resolution or remove chart information to
obtain a better timing.

Research: [QPainter performance](https://doc.qt.io/qt-6/qpainter.html#performance),
[QWidget updates and buffering](https://doc.qt.io/qt-6/qwidget.html#updatesEnabled-prop),
[QImageWriter compression/quality](https://doc.qt.io/qt-6/qimagewriter.html).

## D03 — preserve ecCodes correctness and verify lock assumptions

Existing direct decoding uses the mature native ecCodes library and serializes
its access. Thread-safety depends on its actual build and global/multifield
state, not simply on using Rust or Rayon. Audit both Python and Rust locks and
time inventory, extraction, downstream profile construction and network
separately before choosing additional parallelism or replacing a decoder.

Research: [ecCodes documentation](https://confluence.ecmwf.int/spaces/ECC/overview),
[ecCodes build thread options](https://github.com/ecmwf/eccodes/blob/develop/CMakeLists.txt).

## D04 — preserve the existing desktop/map stack and release boundary

Keep Tauri, React, MapLibre and deck.gl unless representative end-to-end evidence
supports replacement. Native command/provider substitutions must retain DTOs,
SHRF frames, cancellation, bounded work and failure semantics. No new service is
justified by the present audit. Software rendering is a fallback and must be
reported separately from actual integrated-GPU measurement.

The Unreleased changelog may be updated in the primary checkout. Candidate code
stays isolated; stable main and published v3.1.0 remain unchanged. Publication of
v3.2.0 requires the user's explicit word after validation.

## D05 — tune installed PNG encoding before adding a renderer dependency

Accepted: HD/UHD use Qt quality 50, retain exact pixels/density, retain explicit
overrides and maximal compression for original-size output. Five interleaved
full renders per mode/setting show 24.1% / 35.9% less median elapsed time with
+4.0% / +3.5% PNG bytes. Each decoded RGBA hash is unchanged. Existing pixel and
atomic-export tests remain the correctness gate; the optimization makes no
new memory bound or cold-start promise. No alternate codec/dependency or new UI
setting is needed. See [benchmark results](BENCHMARK_RESULTS.md).

## D06 — evaluate libraries against the measured operation

| Option | Finding and adoption gate |
| --- | --- |
| NumPy / ndarray views | Installed native NumPy/PyO3 paths already support contiguous buffers. Additional ndarray-based kernels should reuse owned storage and amortize boundary cost; no new array dependency is needed now. |
| Rayon / Tokio | Keep CPU batch work on the existing bounded shared Rayon pool and async process/I/O work in Tokio. Unbounded `spawn_blocking` does not fix a numerical bottleneck or make a started operation cancellable. |
| ecCodes / NetCDF-C / xarray | Keep their format coverage and lazy spatial selection. Prioritize reading selected fields/regions and chunk layout, not replacing a mature C decoder because the caller is Python. |
| Plotters | Pure Rust bitmap/vector/canvas outputs are relevant to a prototype, but do not supply this application's calibrated Skew-T, hodograph, scientific insets and edit semantics automatically. Not added. |
| tiny-skia | A native CPU raster candidate for a future geometry prototype, with a new text/font/calibration integration cost. Current Qt lossless tuning already yields an end-to-end gain without adding it. |
| wgpu / WebGPU | Potential future rendering backend; device/resource/context-loss and feature coverage require actual integrated-GPU measurement. Existing deck.gl/MapLibre WebGL path remains the default and fallback. |

Research: [ndarray](https://docs.rs/ndarray/latest/ndarray/),
[Rayon](https://docs.rs/rayon/latest/rayon/),
[Tokio blocking tasks](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html),
[NetCDF-C](https://docs.unidata.ucar.edu/netcdf-c/current/),
[xarray chunked execution](https://docs.xarray.dev/en/stable/user-guide/dask.html),
[Plotters](https://docs.rs/plotters/latest/plotters/),
[tiny-skia](https://docs.rs/tiny-skia/latest/tiny_skia/),
[wgpu](https://docs.rs/wgpu/latest/wgpu/),
[deck.gl WebGPU coverage](https://deck.gl/docs/developer-guide/webgpu).

The measured host has Intel Iris Xe Graphics, driver 32.0.101.7085. Detection
alone is not evidence of GPU acceleration or frame-rate gains. Benchmarks must
record the actual active renderer; software fallback stays a separate result.

## D07 — apply established weather-viewer techniques selectively

Metview's documented Fieldset selection treats ordered GRIB fields as a shared
source rather than reconstructing a new full data product for every view. That
supports this application's existing message inventory and model-hour reuse;
it does not justify introducing Metview as another runtime.

Unidata IDV and SSEC McIDAS-V both document temporal/spatial subsetting and
retaining reused data, while explaining cache-versus-memory tradeoffs. Apply the
same principles to query ownership, rolling prefetch, exact-source reuse and
bounded graphics preparation. Their optional fast-rendering/resolution tradeoffs
are not appropriate substitutes for this task's full scientific/pixel fidelity.

AWIPS CAVE's plan-view animation makes the time/space distinction explicit.
Preserve model valid-time and live-scan identity in each panel rather than
blending scans/tilts simply because the display looks smooth. Its separate
server architecture is not needed for this local desktop application.

Primary references: [Metview Fieldset](https://metview.readthedocs.io/en/latest/data_types/fieldset.html),
[IDV performance tuning](https://docs.unidata.ucar.edu/idv/5.7/userguide/misc/PerformanceTuning.html),
[SSEC McIDAS-V guide](https://www.ssec.wisc.edu/mcidas/doc/mcv_guide/current/mcv_guide.pdf),
[Unidata CAVE plan views](https://www.unidata.ucar.edu/blogs/news/entry/awips-tips-explore-the-cave1).
These sources inform design choices, not local benchmark claims. No competing
interface, external service or viewer implementation was copied.

## D08 — reuse exact prepared science before replacing calibrated charts

Accepted: one pristine decoded collection in the serial Qt worker, isolated by
deep copy per request; retain only the ordinary independently calculated oracle
after a successful render. Full file/sidecar/science/profile identity prevents
stale reuse, and the default `cli.render` remains the fallback/reference. Thirteen
paired worker requests preserve exact PNG bytes, pixels and metadata; three warm
unchanged requests show 7.77% less elapsed time in the post-review run. Input caps and a single retained
collection limit admission/count, but are not a hard process-memory ceiling.

Keep calibrated Qt charts and the existing interactive image transforms/edit
pipeline. A new Plotters/tiny-skia/WebGPU renderer would need verified matching
fonts, clipping, pressure/log axes, pixel calibration, all insets, masks, export
density, edits and context-loss behavior. Current measurement supports eliminating
duplicate decoding/oracle construction; it does not prove a replacement can meet
that gate or improve end-to-end editing. Native/GPU replacement is therefore a
documented deferred experiment, not a completed renderer migration. No chart
information, resolution or scientific tolerance is sacrificed to obtain a gain.

## D09 — prepare the rolling field window with cooperative browser tasks

Accepted for integration: share the exact existing pixel packing between lazy
display and preparation, and await only active sampling modes before advancing.
Use the native scheduler yield feature when present, with the existing platform
timer as fallback; no worker framework, service or dependency is needed. Browser
task yielding gives input/render work an opportunity to run but does not make
the packing computation disappear or impose a hard frame-time guarantee.

The CPU benchmark demonstrates a smaller first scene after preparation and exact
bytes; it does not demonstrate real presented FPS or a GPU speedup. Retain the
query's existing observer through preparation so cache eviction/cancellation do
not orphan a live consumer. Keep images weakly owned by frames, preserve the
existing five-times-uint16 cache estimate and prepare only the rolling window.
Disk Load hours remains distinct from ready-in-memory graphics. Manual cold
selection retains the synchronous fallback. Actual native loaded-weather checks
remain the acceptance gate for browser/device behavior.

Research: [Chrome scheduler.yield guidance](https://developer.chrome.com/blog/use-scheduler-yield),
[Scheduler.yield API](https://developer.mozilla.org/en-US/docs/Web/API/Scheduler/yield),
and [deck.gl performance](https://deck.gl/docs/developer-guide/performance).
Feature detection avoids assuming the API exists on every supported WebView.
