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
