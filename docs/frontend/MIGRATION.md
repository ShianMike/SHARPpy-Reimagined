# Frontend migration

SHARPpy Reimagined is moving its interface from PySide6 and Qt Widgets to a
Tauri 2 desktop shell built with React, TypeScript, Vite, MapLibre GL JS, and
deck.gl. The scientific engine is not being rewritten: the Python `sharpmod`
package and the Rust `sharpmod-rs` extension stay authoritative, and the new
frontend renders the data they produce.

The Phase 0 audit, [AUDIT.md](AUDIT.md), classifies every part of the current
codebase, lists the backend interfaces the bridge can reuse, and defines the
migration boundaries. The new app is described in
[ARCHITECTURE.md](ARCHITECTURE.md), [DATA_CONTRACTS.md](DATA_CONTRACTS.md),
[MAP_RENDERING.md](MAP_RENDERING.md), and [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).

## Release status

The new interface ships in **v3.0.0**. The
[changelog](../../CHANGELOG.md#v300---2026-10-15) records its changes
and release limitations. **v2.0.0 Legacy** is the final PySide6/Qt GUI release;
its [existing downloads](https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v2.0.0)
remain available after the new interface is released. The phase descriptions
below are historical migration records, not the current release checklist.

## Current interface (2026-10-12)

The shell now follows SoundScopepy's layout and styling, using the current
SHARPpy backend. Overview, Model Viewer, Soundings, Data Status, and Settings
are the supported workspaces. Analysis, Compare, Ensemble, and ensemble model
choices have been removed. The history below records earlier migration work;
it does not restore those removed UI features.

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Architecture audit | Done ([AUDIT.md](AUDIT.md)) |
| 1 | Tauri shell, design tokens, typed commands, MapLibre and deck.gl, first real backend data (stations) | Done ([ARCHITECTURE.md](ARCHITECTURE.md)) |
| 2 | Station and location map | Done: the station map, hover, selection, and search (pulled into Phase 1), UWyo availability for a chosen release, and place names |
| 3 | Forecast map: model, run, forecast hour, valid time, domain | Done: toolbar controls, the model domain, and a forecast point picked on the map |
| 4 | Weather field layers | Done for sharpmod's 23 HRRR field products, coloured in the frontend; contour lines are not drawn (below) |
| 5 | SPC outlook overlays | Done: categorical, tornado, wind, and hail, four layers |
| 6 | Radar and additional layers | Done: the MRMS mosaic, layer order, opacity, and the layer controls |
| 7 | Timeline and animation | Done: the timeline, play and pause, stepping, frame loading, caching, and prefetch |
| 8 | Two- and four-panel views | Done: one camera, time, run, and selection; each panel its own product and layers |
| 9 | Analysis panels | Removed from the React UI by user request, 2026-10-12; backend calculations retained |
| 10 | Skew-T and hodograph | Done as sharpmod's own image: the Sounding workspace shows the PNG `sharpmod.rendering.cli.render` draws, the image `sharpmod-render` writes. There is no React Skew-T (a user decision) |

## What is React now

The new app (`frontend/`, `src-tauri/`, `sharpmod/bridge/`) runs beside the
legacy one. It provides:

- the application shell: opaque header, collapsible sidebar, forecast strip,
  contained map, point/layer pane, and forecast-hour rail;
- the station map: an offline Natural Earth basemap (from
  `sharpmod/resources/basemap.json`) and the 933 bundled UWyo stations from
  `uwyo_catalog.all_stations()`, with hover, selection, zoom-dependent labels,
  and availability "unknown" until a UWyo release is chosen (below);
- station search over the loaded catalogue, matching `search_stations()`;
- the station inspector (id, name, coordinates, availability, source,
  catalogue origin);
- backend health and Restart backend;
- graphics settings: the capability probe, Automatic, Low, Balanced, and High
  profiles, and a reduced-rendering station list when WebGL2 is unavailable.

The sounding workspaces landed after Phase 1. Every value and every pixel of
the sounding image comes from Python `sharpmod`; TypeScript only lays them out:

- **Sounding**: open an observed sounding (station, UTC date and launch hour,
  provider: automatic, UWyo, IEM, or IGRA, through
  `providers.observations.fetch_observed`), a forecast-model sounding (model,
  one of its recent runs, forecast hour, and the selected station or typed
  coordinates, through `tools.model_extract.extract`), or a sounding
  file (SPC text, BUFKIT, UWyo text, `.npz`, and the other formats sharpmod's
  decoders read). The inspector's Open sounding fills the Observed form. The
  image is sharpmod's renderer output, drawn in a separate Qt render worker,
  with the renderer's five ascent choices (SFC, ML, FCST, MU, EFF). Up to 12
  soundings stay open for the session.
- **Removed Analysis UI (historical)**: the SPC possible hazard type (`prof.right_watch_type`, the
  image's "Psbl Haz. Type"), `sharptab.hazard.classify`, the parcel and storm
  motion tables, and every parameter of the `box_analysis` registry by group,
  with sharpmod's labels, units, and decimals, from
  `analysis.profile_metrics.ProfileMetricsEngine`. A file with several valid
  times also shows the Trends of the legacy Trends tab
  (`ProfileMetricsEngine.timeline` with `DEFAULT_METRIC_KEYS`).
- **Removed Compare UI (historical)**: up to four open soundings side by side, each drawn by sharpmod,
  and their parameters in one table.

The forecast map landed in Phases 2 to 8. Python produces every value and
every geometry; TypeScript chooses the pixels
([MAP_RENDERING.md](MAP_RENDERING.md)):

- **Forecast time.** Model, Run, and Forecast hour in the toolbar, the valid
  time beside them, and the timeline (previous, play or pause, next, and a
  hour rail) all drive one forecast time (`stores/forecastStore.ts`). HRRR hours
  stop at each cycle's last hour (`hrrr_field.max_forecast_hour`).
- **Model fields.** Every product of `hrrr_products.available_products()`,
  derived and reprojected by `providers.hrrr_field` as the legacy map does,
  sent as numbers and coloured in the frontend with the product's sharpmod
  palette; opacity, product, legend, the valid time, and the value under the
  pointer.
- **SPC outlooks.** Categorical, tornado, wind, and hail as four layers from
  `spc_outlook_fetch.fetch_layer` at the forecast valid time, with SPC's
  colours, hatching for significant severe, labels, and a hover tooltip.
- **Radar.** The newest MRMS mosaic (composite or base reflectivity, echo
  tops) from `providers.radar_mosaic`, refreshed at the service's interval,
  or one antenna's own frame (base reflectivity, velocity, hydrometeor
  classification, accumulations) from `providers.radar_site`, by scope switch
  in the layer panel: the CONUS mosaic, or a pinned WSR-88D from the bundled
  catalogue (155 antennas from `radar_sites.json`) or the nearest antenna to
  the map centre (the legacy controller's auto pick, by great-circle
  distance).
- **Satellite.** One GOES frame over the map's extent from
  `providers.satellite_context`: the satellite chosen for the view (GOES-18 or
  GOES-19), the visible or clean-IR channel, and the scan nearest the forecast
  valid time within the provider's tolerance. The status states the actual scan
  time, its offset from the requested one, the view coverage, and the native
  resolution. Uncovered ground stays transparent.
- **Storm reports.** Local Storm Reports from the IEM feed through
  `providers.storm_reports`, narrowed to the three convective hazards and read
  around the forecast valid time. Each report is a typed point record with the
  provider's own marker spec (symbol, pixel radius, fill and stroke) drawn as
  fixed-size symbols; significant reports keep the provider's white fill. The
  row's controls are the provider's own event windows (±1/3/6/12 hours) and
  hazard filter, and the hover tooltip reads the full report.
- **Geography.** The model domain, place names (the legacy map's major cities
  and one town per quarter degree), and state boundaries redrawn over the
  weather.
- **Layers.** Order, visibility, and opacity per layer; the first panel's
  visibility and opacity persist.
- **Forecast point.** A click on empty map picks a point: the inspector shows
  each panel's value there and opens an HRRR sounding at it.
- **Availability.** The stations marked against the UWyo list of a chosen
  release (`uwyo_catalog.fetch_stations_for_datetime`), with the listed
  stations the bundled catalogue lacks added to the map.
- **Panels.** One, two, or four synchronized panels: one camera, time, run, and
  selection; each panel its own product, opacity, and layers.

The Ensemble workspace landed after the map. Its members come from the same
extract pipeline the sounding form uses, one portable file per member, and the
readout is sharpmod's own words:

- **Ensemble.** Request the control member and any number of an ensemble
  model's members (`tools.model_extract.extract` per member, through
  `analysis.profile_timeline.combine_ensemble_collections`), and read the
  acquisition ledger (`analysis.ensemble_members.EnsembleAcquisition`: what was
  asked for, what loaded, and why the rest did not), the labelled spreads
  (`analysis.profile_metrics.ProfileMetricsEngine.ensemble`), and one row per
  requested member. One member's failed download is that member's ledger row,
  not a failed request.
- **Threshold explorer.** Define a rule of one to three conditions over the
  registry's diagnostics (`analysis.ensemble_thresholds.ThresholdDefinition`),
  and classify every requested member against it
  (`analysis.ensemble_thresholds.evaluate_threshold`): the agreement and
  coverage sentences with their denominators, the counts that partition the
  request, one row per member in its own state phrase, and the CSV
  (`analysis.ensemble_thresholds.export_threshold_csv`) as a download.

## What remains Qt-based

The supported desktop application is still `sharpmod-gui`
(`sharpmod/gui.py`). Still only there:
the interactive
sounding viewer (a user-defined
parcel, the readout cursor, stepping through the times of a forecast file,
inset and table swaps), and every export. The new app's sounding image is drawn by the same Qt
widget stack, headless, in its render worker process.

## Scope decisions in Phase 1

The deferred and designed-only rows have landed since, except wind barbs
([Phases 2 to 8](#scope-decisions-for-phases-2-to-8)).

| Requirement | Disposition | Reason |
|---|---|---|
| `Station.available: boolean` (REQUEST §6) | Replaced by `availability: "unknown" \| "listed" \| "notListed"` plus `availabilityBasis` | The bundled catalogue has no availability flag; `true` would be fabricated |
| Live UWyo station list | Deferred to Phase 2 | Availability depends on an observation time, which Phase 1 has no control for |
| Inspector with model values (KOUN, STP, MLCAPE) | Replaced by real catalogue fields | No model data in Phase 1; no fabricated meteorology |
| Toolbar Model, Run, Forecast, Valid and the timeline | Kept as honest disabled controls | No model data in Phase 1 |
| Station search (Phase 2) | Pulled forward | 933 records; mirrors `search_stations()` exactly |
| City and town labels | Deferred to Phase 2 | Needs a place-data command |
| Station clustering | Not implemented | 933 points do not need it |
| Model fields, SPC, domains, radar, wind barbs, timeline animation, multi-panel, analysis, Skew-T | Designed only | Later phases; what is still unbuilt is in [MAP_RENDERING.md](MAP_RENDERING.md#not-built) |
| Binary transport | Format fixed, built with its first consumer (Phase 4) | Phase 1 has no binary payload |
| CHANGELOG.md entry | Added for v3.0.0 and dated for release | `test_versioning.py` checks the release entry separately from the dated v2.0.0 Legacy release |
| Python packaging, macOS and Linux, hosted CI | Open items | Not part of this milestone; see [ARCHITECTURE.md](ARCHITECTURE.md#open-items) |
| Settings as a glass surface | Replaced by an opaque workspace | A window-sized blur over the live map is the costliest glass case on integrated GPUs |
| deck.gl interleaved with MapLibre | Live and desktop maps use `MapLibreOverlay`; offline fixtures retain a separate canvas | The MapLibre adapter replaces the incompatible Mapbox private APIs and keeps geographic outlines above weather ([MAP_RENDERING.md](MAP_RENDERING.md#the-overlay-before-load)) |

## Scope decisions for the sounding workspaces

| Requirement | Disposition | Reason |
|---|---|---|
| Skew-T and hodograph in React or Canvas | Replaced by sharpmod's own image | The user's decision: the image stays sharpmod's, so it matches the legacy viewer and `sharpmod-render` exactly |
| The image of a file with several valid times | The first time, labelled "first of N times" | `cli.render` has no time option; stepping through times stays in `sharpmod-gui` |
| Parameters and Trends | Labelled values from sharpmod's registry and `timeline` | No quantity is named or computed in TypeScript; `check-boundaries.mjs` allows one visible word, the control label "Parcel" |
| Ensemble | Migrated: member acquisition and the summary readout land in the Ensemble workspace, and the threshold explorer classifies the members against a user-defined rule and exports its CSV | One member's failed download stays that member's ledger row (`EnsembleAcquisition`), never a failed request |
| Toolbar Model, Run, Forecast, Valid and the timeline | Still honest disabled controls (live since Phases 3 and 7) | They drive map forecast layers (Phases 3 to 7); model soundings have their own form |
| Model domains on the map | Not drawn yet (drawn since Phase 3) | `get_model_catalog` already returns each domain outline for Phase 3 |

## Scope decisions for Phases 2 to 8

| Requirement | Disposition | Reason |
|---|---|---|
| Fields as numbers the frontend colours (REQUEST §7) | uint16 cells with a scale and offset in an `SHRF` frame, coloured on the CPU through a lookup table built from the sharpmod palette (`map/field/colourize.ts`, checked against `hrrr_field_images.build_lut`) | Less code than a custom shader layer, and about 6 ms for a 2048 by 1024 frame (measured in Node's V8); a shader colormap is the marked upgrade path (`ponytail:` in `colourize.ts`) |
| Field models | HRRR only; another model shows "Use HRRR" | sharpmod's map field pipeline (`hrrr_field`, `hrrr_products`) is HRRR's, as in the legacy map |
| Per-panel palette (REQUEST §14) | Each panel uses its product's sharpmod palette; there is no palette picker | sharpmod defines one palette per product; offering others would invent colour scales |
| Native grid projection | sharpmod reprojects the Lambert grid to a 2048 by 1024 latitude-longitude frame | The legacy map's `hrrr_field.reproject`; the frontend only places the image |
| Contour lines (500 mb heights and other products) | The model field's `PathLayer` over backend line geometry (`get_field_contours`), coloured and spaced by the catalogue's own `ContourSpec`; the row states the interval | `draw_contours` stamps the same level set into the legacy PNG; the new `bridge/contours.py` extracts it as lon/lat segments (marching squares at each multiple of the interval) so a client can read it |
| Wind barbs (REQUEST §12) | Not built | REQUEST §12 asks for a plan; the seam is in [MAP_RENDERING.md](MAP_RENDERING.md#not-built) |
| SPC significant-severe hatching | Diagonal lines computed in the frontend, spacing fixed in degrees | `FillStyleExtension` needs `@deck.gl/extensions`, a new dependency |
| Radar grid mode (REQUEST §11) | Tile mode only: the service's PNG of the newest mosaic, not tied to the forecast time | `radar_mosaic.fetch_frame` returns a rendered image and no history; values need a decoder there |
| Single-site radar scope | The scope switch in the radar row: the CONUS mosaic, or one NEXRAD antenna's frame (`get_radar_site_frame` with a pinned site id or the nearest antenna to the map centre, `get_radar_sites` for the picker, its own site product list per scope) | The legacy map's two scopes (`SCOPE_SITE`/`SCOPE_MOSAIC`) and its nearest-to-centre auto pick; frames are the service's PNG in an SHRF `radar-site` frame, refreshed at the product's cadence, never re-fetched on pan |
| Panel products and layers, the layout, the layer order, and the forecast time | Session only; the first panel's layer visibility and opacity persist, as in Phase 1 | Not asked for; persisting them is additive in `stores/preferences.ts` |
| GPU texture budget, render scale while interacting, frame window `previous` | Defined, not enforced | Each panel holds one field and one radar texture |
| GOES satellite | The `satellite` weather layer: one frame over the map's extent (`get_satellite_frame` at the forecast valid time, visible or clean-IR channel), the scan's own provenance beside it | `satellite_context.fetch_satellite_overlay` renders the GOES fixed grid to a plate-carree PNG over the same fixed extent the field and radar frames use; the frame's scan time, offset, coverage, and resolution come with it |
| Storm reports | The `storm-reports` weather layer: `get_storm_reports` around the forecast valid time with the provider's own event windows and hazard filter (`get_report_options` for their labels), typed point records marked at the provider's own screen spec | `storm_reports.fetch_layer` produces the symbols, radii, colours, and text as data (`layer_from_reports`); the frontend draws them as fixed-size glyphs with a stroke pass behind the fill (TextLayer's outline colour is flat in this deck.gl version), and the hazard filter stays the provider's own (`filter_report_layer`) |
| Surface observations | Not migrated | Not in REQUEST Phases 2 to 8 |
| County boundaries | Not drawn | Not bundled |

## Ground rules

- The backend produces meteorological data; the frontend produces pixels. The
  per-domain seams are in [AUDIT.md §11](AUDIT.md#11-migration-boundaries).
- The legacy application stays operational and keeps its tests. New work lands
  beside it.
- No meteorological calculation is reimplemented in TypeScript. Calculations
  that currently run inside Qt paint or chart code
  ([AUDIT.md §4](AUDIT.md#4-modules-that-mix-rendering-with-meteorological-logic))
  move into Qt-free backend modules before a React view needs them.

This page records each phase as it lands.
