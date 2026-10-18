# v3.1.0 interface captures

Captured on 18 October 2026 from the running live browser preview at
`http://127.0.0.1:5177/`, using the v3.1.0 source and real Python/Rust backend.
All seven images are unedited 1280 × 720 viewport screenshots, saved directly
as JPEGs in the app's dark appearance. Each image was visually inspected after
the displayed data and native sounding renderer finished loading. These are
interface captures, not evidence of a packaged Windows runtime pass.

| Image | Visible workflow and data |
| --- | --- |
| [Overview](overview.jpg) | The new startup workspace with direct Model Viewer, Live Tracking, and sounding entry points. Forecast context is HRRR 18 October 2026 02Z, F000. |
| [Model Viewer](model-viewer.jpg) | The same HRRR run/hour: 2 m AGL Theta-e (K), with 10 m AGL wind barbs. The point inspector reads 303 K at 30.94° N, 95.52° W. |
| [Two panels](two-panels.jpg) | The same run/hour: 2 m AGL Theta-e with 10 m AGL winds, beside 2 m AGL Dewpoint (°F). |
| [Four panels](four-panels.jpg) | The same run/hour: 2 m AGL Theta-e with 10 m AGL winds; Dewpoint (°F); Temperature (°F); and Relative Humidity (%). Each panel has its own parameter and scale. |
| [Live Tracking](live-tracking.jpg) | PGUA, Guam, at 0.5°: Level II Base Reflectivity (scan 18 October 2026 04:30Z) and Level III Velocity (04:37Z). The timeline shows viewing time 04:42Z, and a decoded velocity gate reads −10.1 mph. The reflectivity delay indicator is visible because the scan is more than ten minutes behind the selected time. |
| [Sounding](sounding.jpg) | Imported `examples/soundings/hrrr_point_36.68N_95.66W_f018.npz`: HRRR 25 June 2026 06Z, F018, valid 26 June 00Z at 36.68° N, 95.66° W. The native renderer produced a 4564 × 3077 lossless PNG, fitted into the current Soundings workspace with sources collapsed. |
| [Settings](settings.jpg) | Storage after fetching the forecast and radar cases: 163.9 MiB total, including 122.1 MiB of map images and 41.7 MiB of radar data. Sizes describe this session and change with use. |

## Reproduce

1. Follow the [source setup](../../../README.md#run-v3-from-source) and start
   `npm run dev:browser -- --port 5177` from `frontend/`. The isolated origin
   keeps capture preferences separate from another running preview.
2. Open Model Viewer, enable its model layer, and select HRRR, 18 October 2026
   02Z, F000. Choose the parameters listed above for the corresponding layouts.
   Wait for the fields, wind barbs, and scales to finish loading.
3. In Live Tracking, choose PGUA independently in two panels, with Base
   Reflectivity and Velocity at the available 0.5° tilt. Use station search for
   Guam (91212), choose Zoom to station, close the inspector, then zoom in one
   level. Hover a velocity gate to expose its measured value.
4. Open Soundings, select File → Point sounding, browse to the bundled NPZ,
   and open it. Wait for the UHD renderer, hide sources, and fit the image.
5. Capture Overview and Settings → Storage. Preserve provider/map attribution
   and retain the real interface without annotations or replacement imagery.

Live radar times, provider availability, and forecast retention change. If a
case is unavailable, select another real case and update its caption and this
record together. The previous v3.0.0 captures remain available in Git history;
the README now uses this current tour.
