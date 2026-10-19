# v3.2.0 interface captures

Captured on 19 October 2026 from the live browser preview at
`http://127.0.0.1:5178/`, using the v3.2.0 source and real Python/Rust backend.
All ten images are original 1920 × 1080 JPEG viewport screenshots in the app's
dark appearance. Captures wait for map initialization and displayed data;
Overview loads its background without navigating to Model Viewer first.
These interface captures do not replace the packaged Windows runtime checks.

| Image | Visible workflow and data |
| --- | --- |
| [Overview](overview.jpg) | Startup workspace over the loaded basemap, with Model Viewer, Live Tracking and Soundings entry points. Forecast context is HRRR 19 October 2026 09Z, F000. |
| [Model Viewer](model-viewer.jpg) | The same HRRR run/hour: 2 m AGL Theta-e (K) with 10 m AGL wind barbs. |
| [Two panels](two-panels.jpg) | The same run/hour: Theta-e with surface winds beside 2 m AGL Dewpoint (°F). |
| [Four panels](four-panels.jpg) | The same run/hour: Theta-e with surface winds, Dewpoint (°F), Temperature (°F) and Relative Humidity (%). |
| [Forecast downloads](download-details.jpg) | Four-panel download details, distinguishing hours saved to cache from frames ready in memory. Counts describe this session. |
| [Live Tracking](live-tracking.jpg) | PGUA, Guam, at 0.5°: native Level II Base Reflectivity, scan 19 October 2026 11:18Z, and Level III Velocity, scan 11:24Z. The timeline's viewing time differs from each product's actual scan time. |
| [Sounding](sounding.jpg) | Imported `examples/soundings/hrrr_point_36.68N_95.66W_f018.npz`: HRRR 25 June 2026 06Z, F018, valid 26 June 00Z at 36.68° N, 95.66° W. Native 4564 × 3077 lossless rendering fitted into the workspace, with sources collapsed. |
| [Settings](settings.jpg) | Storage after fetching forecast and radar data. Cache sizes describe this session and change with use. |
| [Updates](updates.jpg) | Installed version 3.2.0 with its release summary and the View highlights action. |
| [What's new](whats-new.jpg) | The installed release's five highlights, reopened from Settings → Updates. |

## Reproduce

1. Follow the [source setup](../../../README.md#run-v3-from-source) and start
   `npm run dev:browser -- --port 5178` from `frontend/`. Set a 1920 × 1080
   browser viewport and dark appearance. Save original JPEG bytes without resizing.
2. Let Overview's map finish loading before capturing. Open Settings → Updates
   and View highlights for the two update views.
3. In Model Viewer, enable the model layer and select the available HRRR run,
   F000 and the products listed above. Wait for all fields and wind barbs to load.
   Open Forecast download details for the download view.
4. In two-panel Live Tracking, select PGUA with Base Reflectivity and Velocity
   at their verified 0.5° tilts. Use station search for Guam (91212) in Model
   Viewer, Zoom to station, and let the camera finish moving before returning to
   Live Tracking. Zoom in one level and wait for both native radar scans.
5. Import the bundled NPZ through Soundings → File, wait for the UHD renderer,
   hide sources and fit the image. Open Settings → Storage for the final view.

Provider availability and retention change. If a case is no longer available,
choose another real case and update its caption and this record together.
Keep map/provider attribution visible. Earlier release tours remain in Git history.
