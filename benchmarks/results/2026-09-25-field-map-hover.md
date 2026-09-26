# Four-panel field-map interaction benchmark

The offline benchmark drives four visible Qt map panels at 800 × 435 logical
pixels each, with a generated 1024 × 768 numeric field raster. It sends actual
middle-button pan, wheel-zoom, and mouse-move events through the map widgets.
No provider requests or forecast downloads are made.

Environment: Windows AMD64, Python 3.11.14, Qt offscreen. Each interaction has
40 measured events. The baseline disables only the new cursor-frame cache; all
other code and benchmark inputs are the same.

| Interaction | Without frame cache median / p95 | With frame cache median / p95 | Median change |
| --- | ---: | ---: | ---: |
| Pan move | 11.22 / 16.08 ms | 8.34 / 13.25 ms | 25.7% faster |
| Wheel zoom | 8.57 / 17.54 ms | 6.30 / 11.84 ms | 26.4% faster |
| Shared hover across 4 panels | 48.07 / 59.26 ms | 2.24 / 3.77 ms | 95.3% faster |

During the 40-event hover phase, legend, place-label, and basemap draw calls
fell from 160 each to 1 each. Pan and zoom keep the direct gesture-preview paint
path; the reusable frame is used after the view settles, so it is not rebuilt
for every movement event. The hovered numeric-value marker is drawn by the
crosshair overlay above the cached map.

Checks: all four cached map frames matched a direct render pixel-for-pixel;
the benchmark confirmed all four field legends were present and the shared
crosshair cleared when the cursor left. Ruff passed for every changed Python
module.

Reproduce the optimized run and direct-paint baseline with:

```powershell
python benchmarks/benchmark_gui_maps.py --repeat 40 --json optimized.json
python benchmarks/benchmark_gui_maps.py --no-frame-cache --repeat 40 --json baseline.json
```

These are local offscreen measurements, so they compare this paint path under a
controlled workload; they do not substitute for a packaged release run on a
user's display and graphics driver.

The benchmark now also exercises a 40-event left-button pan, the default map
gesture. After keeping hover inspection out of that drag path, its median was
11.07 ms (p95 21.14 ms) in one offscreen run. The pan performed 40 basemap and
legend draws, one for each gesture preview, with no extra hover redraws. The
result is recorded in `.test-results/field-map-left-pan-20260925.json`; it is a
single-run observation rather than a before/after speedup claim.
