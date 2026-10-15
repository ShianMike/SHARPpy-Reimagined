# v3.0.0 interface captures

Captured on 15 October 2026 from the running v3 live browser preview at
`http://127.0.0.1:5174/`. All six images are unedited viewport screenshots,
1864 pixels wide, with the app's dark appearance. The map captures are 1026
pixels tall, the sounding capture is 1051, and Overview/Settings are 1052.
They show the development
interface before release; they are not proof of a packaged Windows runtime.

| Image | Visible workflow and data |
| --- | --- |
| [Model Viewer](model-viewer.jpg) | HRRR composite reflectivity; 14 October 2026 13Z run, F000, valid 13Z. The selected point is 35.44° N, 96.41° W with a 34.0 dBZ readout. |
| [Two panels](two-panels.jpg) | The same run/hour: 300 mb Height, Wind and Composite Reflectivity, with separate scales and zoom-aware barbs. |
| [Four panels](four-panels.jpg) | The same run/hour: 300 mb Height, Wind; Composite Reflectivity; Supercell Composite; and 2 m AGL Dewpoint. |
| [Sounding](sounding.jpg) | Imported `examples/soundings/hrrr_point_36.68N_95.66W_f018.npz`: HRRR 25 June 2026 06Z, F018, valid 26 June 00Z, at 36.68° N, 95.66° W. The native renderer produced a 4564 × 3077 lossless PNG, fitted into the viewer. |
| [Overview](overview.jpg) | Current map context, sounding-source shortcuts, and live provider availability during the capture session. Availability changes over time. |
| [Settings](settings.jpg) | Appearance choices and navigation to forecast playback, map rendering, and device diagnostics. |

## Reproduce

1. Follow the [source setup](../../../README.md#run-v3-from-source) and start
   `npm run dev:browser` from `frontend/`.
2. In Model Viewer, choose HRRR, the 14 October 2026 13Z run, F000, and
   Composite Reflectivity. Frame the southern/central Plains and select the
   forecast point shown above.
3. Close the point inspector. Choose two panels and assign the wind and
   reflectivity products above. Choose four panels and assign the remaining
   two products. Wait for all fields to finish loading before capture.
4. Open Soundings, choose File → Point sounding, browse to the bundled NPZ,
   and open it. Wait for the UHD image to render and use Fit sounding to view.
5. Open Overview and Settings for their captures. Preserve provider/map
   attribution and capture the real interface without annotations or overlays.

Archived provider availability can change. If a case is unavailable, choose
another real case and update its caption and this record together. The older
`scripts/capture_readme_cases.py` captures the legacy Qt interface, not this tour.
