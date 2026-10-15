# Frontend migration audit (Phase 0)

This audit maps the PySide6 application before the Tauri 2 + React frontend is
built beside it. It covers Section 30, item 1 of the migration request:
which code is scientific backend, data retrieval, Qt application logic, Qt-only
presentation, or Qt scientific rendering, plus the seams the new bridge can
reuse.

Audited source: `main` at `f354fd5` (v2.0.0), clean working tree. The audit
changed no source, test, or workflow file.

Method: reading the modules cited below, a static `ast` import scan of every
production module under `sharpmod/` (excluding `tests/` and `_compat/`),
runtime import probes in the canonical `.gribenv` environment (Python 3.11.14,
`sharpmod_rs` 2.0.0 active), and the baseline JUnit reports of the fast and
compatibility lanes. [Appendix A](#appendix-a-reproducing-the-measurements)
has the commands.

Paths without a prefix are relative to `sharpmod/`. `:N` is a line number at
the audited commit.

## 1. Key findings

- **The scientific stack is already Qt-free.** No production module under
  `sharptab/`, `backends/`, `io/`, `analysis/`, `models/`, `providers/`,
  `maps/`, `state/`, `tools/`, `core/`, or `upstream/` imports Qt at module
  scope. The only Qt touchpoint is the PNG fallback in
  `providers/hrrr_field_images.py::encode_png`. Decoding the bundled HRRR
  example, building its profile, and reading derived parameters loads zero Qt
  modules (§6).
- **Qt is concentrated in the interface packages.** `ui/` has 148 modules and
  65,804 lines, 115 of which import Qt at module scope. `viz/` has 25 modules
  (11,029 lines), `render_patches/` 16, `rendering/` 7, plus the root
  composition modules `gui.py`, `gui_picker.py`, and `gui_viewer.py`.
- **Rendering and meteorology mix in three places**: the HRRR field pipeline,
  which derives a numeric grid and immediately colours it into a PNG; the
  sounding canvas (`viz/`, `render_patches/`), which runs SharpTab calculations
  inside chart and paint code; and the overlay geometry contract, which bakes
  colours and pixel sizes into frozen shapes (§4).
- **The canonical station catalogue** is the bundled
  `resources/uwyo_stations.json`, loaded by `io/uwyo_catalog.py::load_catalog()`
  and consumed through `all_stations()` at `gui_picker.py:490`. The picker then
  replaces that list per observation time with the live UWyo list from
  `fetch_stations_for_datetime()`. The module imports in about 80 ms without
  NumPy or Qt (§5.1).
- **`rust/sharpmod-rs` is reusable but not yet cleanly linkable.** It already
  builds an `rlib` and confines PyO3 to `src/lib.rs`, but `pyo3` (with
  `extension-module`) and `numpy` are unconditional dependencies, so a path
  dependency from `src-tauri` would pull PyO3 and a build-time Python
  interpreter into the Tauri build (§7).
- **A no-API-key basemap already ships**: Natural Earth 1:50m coastline, lake,
  country, and state/province lines in `resources/basemap.json` (1.66 MB,
  public domain). It is line geometry only (§8).
- **`ui/styles/theme.py` is a Qt-free, test-enforced token system** with
  semantic colour roles, a WCAG contract, spacing, control-height, type,
  and motion scales, and a separate map palette. Its roles map onto CSS
  variables; its QSS mechanics do not need to be carried over (§9).
- **Scientific behaviour is well protected**: 486 fast-lane science tests, 46
  Hypothesis property tests, 152 Python/Rust equivalence and selection tests,
  and Rust contract tests. On this machine both local lanes already exceed
  their timing budgets before any change (§10).

## 2. Legacy application map

| Surface | Entry point | Composition |
|---|---|---|
| Launcher | `sharpmod-gui` → `gui.py::main` → `gui_picker.main` | `gui.py` sets `QT_QPA_PLATFORM` and `QT_API` before any Qt import |
| Picker window | `gui_picker.py::PickerWindow` | `QMainWindow` plus 19 workflow mixins from `ui/picker/`. Six sources in a `SourceSelector` (`ui/shell.py`): Station Map, Station List, Forecast Model, Field Panels, Reanalysis (ERA5), Open File; all but Station Map are built lazily |
| Maps | `ui/features/gui_maps.py` | `StationMapWidget` is 13 mixins from `ui/maps/` plus `QWidget`; `PointMapWidget` and `BoxFieldMapWidget` extend it. Everything is drawn with `QPainter` over a cached basemap raster, using flat or Lambert projection math from `ui/maps/projection.py` |
| Map overlays | `ui/maps/overlays/*_controller.py` | Controllers start `QThread` workers (`ui/features/gui_network_workers.py`) that call Qt-free providers and return frozen `OverlayLayer` or `OverlayRaster` objects (`maps/map_overlays.py`) |
| Field panels | `ui/picker/panels_workflow.py`, `ui/maps/panel_view.py`, `ui/maps/panels.py` | Up to four field controllers sharing a crosshair and domain outline |
| Sounding viewer | `gui_viewer.py::compose_interactive` | Upstream `sharppy.viz.SPCWindow`, composed by `viz/SPCWindow.py`, patched by `render_patches/`, wrapped by `ui/viewer_*.py` |
| Analysis workspace | `ui/analysis/workspace.py` | Trends, Compare, Ensemble, and Animation tabs over `analysis/` engines |
| Headless renderer | `sharpmod-render` → `rendering/cli.py` | Same widget stack under the Qt `offscreen` platform |

Concurrency follows one pattern throughout: a debounce `QTimer` (300 to
450 ms), a monotonically increasing token per request, and results dropped
when their token is stale (for example `ui/picker/availability.py:131`). Long
batch work runs in a child process (§5.8).

## 3. Module classification

The five categories come from Section 30, item 2. Category (f) adds Qt-free
presentation policy and application state: no meteorology and no Qt, but
behaviour the new frontend will own.

### (a) Meteorological and scientific backend

Authoritative. The bridge calls these; the frontend never reimplements them.

| Area | Modules | Notes |
|---|---|---|
| Derived parameters | `sharptab/`: `profile.py` (`Profile`, `create_profile`), `params.py`, `winds.py`, `interp.py`, `derived.py`, `derived_thermo.py`, `derived_indices.py`, `ecape.py`, `hazard.py`, `fire.py`, `winter.py`, `parcels.py`, `vertical_interpolation.py`, `constants.py` (`MISSING`, `PARAM_REGISTRY`), `accelerated_profile.py` (`AcceleratedConvectiveProfile`, `accelerate_profile_collection`) | 16 modules, 5.8k lines; 7 import the pinned upstream `sharppy.sharptab` |
| Backend facade | `backends/`: `selector.py` (`get_backend` :137, `backend_info` :142), `protocol.py` (`BACKEND_API_VERSION = 7` :22, 18 `REQUIRED_RUST_CAPABILITIES` :30), `python_backend.py`, `rust_backend.py`, `kinematics.py`, `parcels.py`, `grib.py`, `grib_decode.py`, `_common.py` | The single authority for Rust/Python selection (`SHARPMOD_BACKEND=auto\|python\|rust`) |
| Native kernels | `rust/sharpmod-rs/src/`: `wind.rs`, `interpolation.rs`, `quality_control.rs`, `records.rs`, `parsing.rs`, `kinematics.rs`, `parcels.rs`, `batch_analysis.rs`, `grib.rs` | See §7 |
| Decoders, QC, formats | `io/decoder.py` (registry, `load_npz` :547, `attach_json_sidecar`), `io/uwyo_decoder.py`, `io/igra2.py`, `io/portable_sounding.py`, `io/sharppy_export.py` | `decoder.py` imports upstream `sharppy` |
| Analysis engines | `analysis/`: `box_analysis*.py`, `box_sounding.py`, `box_mean.py`, `profile_metrics*.py`, `comparison.py`, `ensemble_thresholds.py`, `profile_timeline.py`, `timeline_frames.py` | Shared by the GUI and the CLIs |
| Model-sounding science | `models/model_surface.py` (verified ground-row merge); derivations in `providers/hrrr_products.py` (`_significant_tornado` :590, `_supercell_composite` :574, EHI, lapse rates, shear magnitude) | `hrrr_products.py` also holds palettes (§4) |
| Upstream compatibility | `upstream/upstream_patches.py`, `upstream/upstream_warnings.py`; pinned `SHARPpy==1.4.0a5` | Installed by `scripts/install_sharppy_compat.py`, not as a normal dependency |

### (b) Data retrieval and providers

All Qt-free.

| Area | Modules |
|---|---|
| Station catalogues | `io/uwyo_catalog.py` (bundled and live UWyo lists), `io/igra2_decoder.py` (IGRA station lookup, including ICAO callsigns) |
| Observed soundings | `providers/observations.py` (`UWyoObservedProvider`, `IEMObservedProvider`, `IGRAObservedProvider`, `get_observed_provider` :611) |
| Forecast-model soundings | `tools/model_extract.py` (`available_models` :422, `get_config` :582), `tools/model_extract_{config,retrieval,run,probe,cli}.py`, `models/model_{sources,transfer,transport,fields,disk_cache,hour_cache}.py`, `providers/{rrfs_nomads,hrrr_zarr,openmeteo,openmeteo_runtime,openmeteo_access,eccc_geomet,eccc_geomet_runtime}.py`, `tools/{era5_extract,era5_columns,ifs_extract,wrf_extract}.py`, `analysis/batch_extract.py`, `analysis/ensemble_members.py` |
| Map products | `providers/hrrr_field.py` with `hrrr_field_fetch.py` and `hrrr_field_images.py`, `providers/spc_outlook.py` with `spc_outlook_fetch.py`, `providers/{radar_mosaic,radar_site,satellite_context,storm_reports,surface_observations}.py` |
| Places and geography | `maps/place_names.py` (offline Census index, Nominatim fallback); resource builders `tools/build_{basemap,coastlines,radar_sites,uwyo_catalog}.py` and `scripts/build_conus_{place_index,county_tiles}.py` |
| CLIs | `tools/{uwyo_sounding,observed_sounding,batch_extract,box_extract,model_extract_cli,rust_backend_sync}.py` |

### (c) Qt application logic

Workflows and state machines written against Qt. The new frontend re-homes
this behaviour (stores, a query layer, bridge services) instead of porting the
classes.

| Area | Modules |
|---|---|
| Composition roots | `gui.py`, `gui_picker.py` (`PickerWindow`, `main`), `gui_viewer.py` |
| Picker workflows | `ui/picker/`: `availability.py` (availability probes and the datetime-aware catalogue refresh), `map_workflow.py`, `observed_workflow.py`, `model_selection.py`, `model_jobs.py`, `model_point.py`, `panels_workflow.py`, `box_workflow.py`, `fetch_handlers.py`, `layer_session.py`, `layer_preferences.py`, `preferences.py`, `viewer_state.py`, `startup.py`, `cycles.py`, `era5.py`, `wrf.py`, `files.py`, `locations.py`, `recents.py` |
| Workers and jobs | `ui/features/{gui_workers,gui_network_workers,gui_availability_worker,gui_threading,gui_jobs,gui_batch_process}.py`, `ui/box_workers.py`, `ui/analysis/{jobs,workspace_jobs,workspace_session}.py` |
| Overlay lifecycle | `ui/maps/overlays/`: `controllers.py`, `hrrr_controller.py`, `outlook_controller.py`, `radar_controller.py`, `reports_controller.py`, `locator_selector.py` |
| Persistence wiring | `ui/features/gui_settings.py` (`_settings_file_path` :133, `_build_settings` :150), `ui/features/{gui_sessions,gui_cache,gui_collection_state,gui_closed_profiles,gui_edit_history,gui_locations}.py` |
| Time, comparison, ensembles | `ui/features/{gui_timeline,gui_timeline_playback,gui_model_compare,gui_ensemble_thresholds,gui_box}.py`, `ui/model_compare_coordinator.py`, `ui/threshold_evaluation.py`, `ui/box_plan.py` |

### (d) Qt-only presentation

Chrome, layout, dialogs, and map interaction. Replaced, not ported.

| Area | Modules |
|---|---|
| Shell and styles | `ui/shell.py`, `ui/styles/theme_qss_{base,forms,selections,menus,finishing}.py`, `ui/features/gui_theme.py` |
| Picker chrome | `ui/picker/`: `chrome.py`, `layout.py`, `pages.py`, `station_pages.py`, `forecast_pages.py`, `layers.py`, `layer_list.py`, `layer_compact.py`, `presentation_controls.py`, `selection.py`, `coordinates.py`, `station_catalogue.py` |
| Viewer chrome | `ui/viewer_{controls,sidebar,export,locator,scaled}.py`, `ui/calendar.py`, `ui/analysis_controls.py`, `ui/edit_overlay.py` |
| Feature chrome | `ui/features/`: `gui_common.py`, `gui_export.py`, `gui_selectors.py`, `gui_workspace_layouts.py`, `gui_metric_columns.py`, `gui_commands.py`, `gui_interaction_mode.py`, `gui_environmental_context.py`, `gui_locator.py`, `gui_locator_fetch.py`, `gui_sounding_readout.py`, `gui_edit_feedback.py` |
| Map widget and interaction | `ui/features/gui_maps.py`; `ui/maps/`: `basemap.py`, `navigation.py`, `station_interactions.py`, `station_labels.py`, `station_state.py`, `chrome_drawing.py`, `crosshair_overlay.py`, `presentation.py`, `profile_markers.py`, `panel_view.py`, `panels.py`, `point.py`, `box.py`, `export.py`, `regions.py`, `hatching.py` |

### (e) Qt scientific rendering

| Area | Modules |
|---|---|
| Sounding canvas | `viz/`: `skew.py`, `hodo_levels.py`, `hodo_render.py`, `hodo_locator.py`, `hodo_overlay_drawing.py`, `height_charts.py`, `height_inset.py`, `inset_layout.py`, `inset_readout.py`, `index_board.py`, `index_columns.py`, `param_board.py`, `ship.py`, `streamwiseness.py`, `thermo.py`, `hazard.py`, `custom_panel.py`, `custom_barbs.py`, `spc_products.py`, `SPCWindow.py`, `box_field.py`, `unit_text.py`, `_qt6_compat.py`; upstream `sharppy.viz` |
| Patches to upstream widgets | `render_patches/` (16 modules, gated by SHARPpy version in `render_patch_registry.py`) |
| Headless output | `rendering/{cli,density,export,input,parcel,map_point_symbols}.py`, `analysis/animation_exports.py` (GIF through Pillow), `ui/animation_export.py` |
| Analysis charts | `ui/analysis/{charts,trend_chart,envelope_chart,comparison,ensemble,trends,workspace,workspace_ui}.py`, `ui/comparison_canvas.py`, `ui/comparison_chart_view.py`, `ui/features/{gui_comparison_charts,gui_visual_comparison,gui_trend_tracks}.py` |
| Map scientific layers | `ui/maps/raster.py` (field, radar, and satellite rasters, including the curved-projection warp), `ui/maps/vector.py` (SPC polygons, reports, surface station plots), `ui/maps/legend.py` and `legend_drawing.py` (colour bars), `ui/maps/point.py::_draw_domain` (:526, model domains), `ui/maps/box_field.py` |

### (f) Qt-free presentation policy and application state

| Area | Modules |
|---|---|
| Map policy | `maps/map_geography.py` (boundary order and widths, label tiers, `MAJOR_CITIES` :84, scale bar), `maps/{map_layers,map_legends,map_time,map_field_inspection,locator_overlay,locator_presentation,overlay_hatch}.py`, `maps/map_overlays.py` (overlay contracts, see §4); `ui/maps/{projection,layers,time_state,basemap_data}.py` |
| Tokens and colour rules | `ui/styles/theme.py` (§9), `viz/colors.py` (tier thresholds, WCAG `contrast_ratio` :139) |
| Application state | `state/{sessions,session_merge,saved_locations,saved_box_regions,recent_destinations,collection_identity,run_updates}.py`, `core/local_time.py`, `export_paths.py`, `ui/features/{export_presentation,coordinate_input,metric_controls,profile_inspector}.py`, `ui/edit_formatting.py` |

`_compat/` holds 127 ten-line shims that keep historical import paths working.
New code, including any bridge, imports the canonical packages.

## 4. Modules that mix rendering with meteorological logic

| Module | What is mixed | Evidence | Where to cut |
|---|---|---|---|
| HRRR field pipeline: `providers/hrrr_field_fetch.py::render_field` (:98), `hrrr_field.py`, `hrrr_field_images.py` | Derivation, reprojection, palette colouring, contour stamping, and PNG encoding in one call. The numeric grid survives only in an 8-entry in-memory cache used for hover sampling (`remember_derived` :653, `cached_derived` :670); the disk cache stores PNGs only | `product.derive(decoded)` → `reproject` → `colourize` (`hrrr_field_images.py:95`) → `draw_contours` (:122) → `encode_png` (:158) | Expose the `derive` output (native 1059×1799 Lambert grid) or the reprojected grid with product metadata. Colouring and contours become frontend shader work; the PNG path stays for the legacy app |
| `providers/hrrr_products.py` | Field arithmetic and `Palette` definitions in one catalogue | `Palette` :141, `HrrrProduct` :657, `PRODUCTS` :877 | Keep one catalogue and serialize palette stops, `stepped`, `floor`, `ceiling`, `ticks`, `units`, and `caveat` as data rather than copying colour tables into TypeScript |
| SPC outlooks: `providers/spc_outlook.py::parse_outlook` (:600) and `_feature_style` (:525), producing `maps/map_overlays.py::OverlayShape` (:156) | GeoJSON validation and ring normalization combined with stroke, fill, hatch, rank, and pixel marker sizes | `OverlayShape.stroke`, `fill`, `hatch`, `hatch_level`, `marker_radius_px` | Send normalized rings with semantic properties (label, DN, CIG level, significant flag, product, valid window, issue time, source URL). SPC's published colours can travel as hints; the frontend owns styling |
| `providers/surface_observations.py`, `providers/storm_reports.py`, both producing `OverlayShape` | Observation values (temperature, dewpoint, u/v wind, gust, `wind_state`) carried inside drawable shapes | `OverlayShape` station-plot fields | Expose typed point records; the frontend draws station plots, barbs, and symbols |
| `viz/height_charts.py` | A chart module lifts a parcel from every level (`params.parcelx` :345 in `stepwise_cape_profile` :302) and computes θ/θe (`thermo.theta` :222, `thermo.thetae` :257) and the storm-relative wind profile (:147) | upstream `sharppy.sharptab` imports | Move the calculations into `sharptab/` or `analysis/` before a React chart needs them |
| `viz/streamwiseness.py::streamwiseness_profile` (:90) | Streamwiseness calculation and its Qt plot in one module | `sm_winds.storm_motion` (:69) | Same as above |
| `viz/skew.py` (:181), `viz/spc_products.py` (:264, :277, :281), `viz/hodo_levels.py` (:50), `viz/index_board.py` (:477) | Paint-time hail-growth-zone bounds, wet-bulb-zero and freezing levels, AGL conversion, SWEAT | `interp.pres_at_isotherm`, `params.temp_lvl`, `params.sweat` | These become profile fields before the Phase 10 Skew-T and hodograph |
| `viz/SPCWindow.py::_derived_profile` (:585) | Profile construction inside window composition | | The bridge builds profiles through `io/decoder.py` and `accelerate_profile_collection`, as `rendering/input.py` does, never through `SPCWindow` |
| `render_patches/hodograph.py` (:217, :331), `hodo_legacy.py` (:41 to :61), `panel_fonts.py` (:291), `skewt_labels.py` (:36, :528), `text_panels.py` (:869) | Interpolation, thermodynamics, and fire-weather `ventilation_rate` inside monkeypatched paint methods | upstream `sharppy.sharptab` | Phase 10 concern: the values must arrive precomputed |
| `ui/features/gui_sounding_readout.py`, `ui/features/gui_edit_feedback.py`, `ui/edit_overlay.py` | `sharptab.interp` for cursor readouts and edit feedback | | Readouts become backend queries or precomputed per-level arrays |
| `viz/colors.py` | Meteorological significance thresholds bound to colours | `TIER_THRESHOLDS` :510, `tier_color` :748 | Serve tier indices or thresholds with the analysis DTOs (Phase 9) |

Elsewhere the UI calls science correctly: `ui/` modules import `analysis/` and
`providers/` (for example `ui/analysis/trends.py` uses `ProfileMetricsEngine`)
instead of computing values themselves.

## 5. Reusable backend interfaces

### 5.1 Canonical station catalogue

The chain the legacy app uses:

1. **Resource.** [`resources/uwyo_stations.json`](../../sharpmod/resources/uwyo_stations.json):
   933 fixed UWyo radiosonde stations, built offline by
   `tools/build_uwyo_catalog.py` from `weather.uwyo.edu/wsgi/sounding_json`
   (sampled years 2024 and 2015) and shipped as package data
   (`pyproject.toml`, `[tool.setuptools.package-data] "sharpmod.resources"`).
2. **Loader.** [`io/uwyo_catalog.py`](../../sharpmod/io/uwyo_catalog.py)
   `load_catalog()` (:90, `lru_cache(maxsize=1)`) reads it through
   `importlib.resources`, normalizes each record, skips empty ids, and falls
   back to the 10-station `SEED_STATIONS` when the resource is missing or
   unreadable.
3. **Accessor.** `all_stations()` (:122) returns copies sorted by id.
4. **Call site.** `PickerWindow.__init__` runs
   `self._all_stations = _uwyo_catalog().all_stations()` (`gui_picker.py:490`)
   through the lazy importer `_uwyo_catalog()`
   (`ui/features/gui_common.py:600`). The same list builds
   `StationMapWidget(self._all_stations)` (`ui/picker/station_pages.py:67`) and
   feeds the Station List filter (`ui/picker/observed_workflow.py:31`,
   case-folded substring match on id and name).
5. **Live refinement.** When the observation time changes,
   `_refresh_station_catalog` (`ui/picker/availability.py:120`) debounces a
   `_StationListWorker` (`ui/features/gui_network_workers.py:435`) that calls
   `fetch_stations_for_datetime(when_utc)` (`uwyo_catalog.py:190`: HTTPS to
   `https://weather.uwyo.edu/wsgi/sounding_json`, 30 s timeout, same schema,
   de-duplicated, sorted). `_on_station_list_loaded` (:151) replaces the list
   and calls `StationMapWidget.set_stations` (`ui/maps/station_state.py:16`).
   `_on_station_list_failed` (:178) keeps the current list and reports "Using
   offline station list".

Other helpers: `get_station(id)` (:128), and `search_stations(query, limit)`
(:136: exact id first, otherwise case-folded substring on id and name, sorted
by id), used by the `uwyo-sounding` CLI. The display label comes from
`_station_label` (`ui/features/gui_workers.py:119`) as `"72357 — <name>"`.

Record schema:
`{"id": "72357", "name": "Norman/Max Westheimer A, Ok., Usa", "lat": 35.18095, "lon": -97.43787, "src": "FM35"}`.
`id` is the WMO index. `src` is the UWyo data source (`FM35` 844, `BUFR` 46,
`FM37` 43). There is no elevation, ICAO or three-letter identifier, or country
field. No ids repeat and every coordinate is finite; latitudes span −78.0 to
82.49 and longitudes −177.91 to 179.21. The list is about 94 KB as JSON.

Availability: the catalogue has no `available` flag. The legacy app has two
separate notions. One is "listed by UWyo for the selected time": the live list
replaces the plotted set, and every plotted station uses the map palette's
"available station" colour. The other is a per-selection probe
(`_AvailabilityWorker`) with the states `unknown`, `checking`, `available`,
`fallback`, `insufficient`, and `unavailable` (`ui/features/gui_workers.py:80`),
backed by `providers/observations.py` and the source choice in
`ui/picker/observed_sources.py`.

Import cost: `import sharpmod.io.uwyo_catalog` takes about 80 ms, loads 159
modules, and pulls no NumPy, SHARPpy, or Qt.

### 5.2 Model registry and domains

- `tools/model_extract.py::available_models()` (:422) returns `ModelConfig`
  records (`tools/model_extract_config.py:238`) with `key`, `label`, `cycles`,
  `fxx_values`, `domain`, `domain_bounds`, `grid_spacing_km`,
  `domain_outline`, `notes`, and `unavailable_reason`. Twenty configs are
  defined and 19 are selectable (AIGFS is withheld). `get_config(model)`
  (:582) resolves aliases.
- Only HRRR (a 129-point perimeter from
  `models/lambert_grid.py::HRRR_GRID.outline()`, :178 and :221) and RDPS (a
  113-point rotated-grid perimeter from `providers/eccc_geomet.py`) carry real
  outlines. The others fall back to boxes such as `CONUS_DOMAIN`
  (`model_extract.py:148`).
- `models/lambert_grid.py` is pure `math` and imports in 23 ms;
  `tools/model_extract.py` pulls NumPy transitively and takes 375 ms.
- The Qt map paints domains in `ui/maps/point.py::_draw_domain` (:526), fed by
  `ui/picker/model_selection.py:122`.
- HRRR field run and hour rules live in `providers/hrrr_field.py`
  (`latest_run`, `resolve_request`, `pin_request`, `max_forecast_hour`).

### 5.3 HRRR field products

- Catalogue: `providers/hrrr_products.py::available_products()` (:885) and
  `get_product()` (:901). 23 products in seven categories: Height and Wind
  (5), Surface (2), Radar (1), Instability (6), Wind Shear (4), Composite
  Parameters (4), Explicit Convective (1). The default product is composite
  reflectivity (`refc`, a stepped dBZ palette with 15 stops and a 5 dBZ
  floor). The CAPE fields are single-record pass-throughs; STP and SCP are
  fixed-layer forms with stated caveats.
- Grid sizes: the native Lambert grid is 1059×1799 (1,905,141 points, about
  7.6 MB per field as float32). Today's render frame is plate carrée
  3072×1536 over `COVERAGE_BOUNDS = (-134.5, -60.5, 20.5, 53.0)` (:112, :118),
  about 18.9 MB as float32. Raster overlays are capped at 16 MiB
  (`MAX_RASTER_BYTES`, `maps/map_overlays.py:344`).
- Retrieval uses byte-range downloads from the AWS open-data bucket guided by
  the `.idx` inventory, decoded with ecCodes (`decode_message` :479). Numeric
  inspection today goes through `sample_derived()`
  (`hrrr_field_images.py:199`). Failures raise `HrrrFieldError`,
  `HrrrFieldUnavailable`, or `HrrrFieldOffline`.

### 5.4 SPC outlooks

`providers/spc_outlook_fetch.py::fetch_layer(valid_time, ...)` (:16) resolves
the issuance covering a valid time (Days 1 to 3, archive from 2020), fetches
the `*.nolyr.geojson` product, and returns an `OverlayLayer` with validity
window, issue time, source URL, attribution, and shapes carrying exterior and
hole rings. Products are listed in `spc_outlook.PRODUCTS` (:196). Results are
cached in memory and in a bounded disk cache; payloads are a few kilobytes.
Failures raise `OutlookError`; "no outlook for this time" is a `None` result,
not an error.

### 5.5 Radar and satellite

`providers/radar_mosaic.py` (MRMS through the NCEP GeoServer WMS 1.3.0
`GetMap`, `CRS:84`, `https://opengeo.ncep.noaa.gov/geoserver/conus/wms`),
`providers/radar_site.py` (single-site NEXRAD from the same server), and
`providers/satellite_context.py` (GOES ABI L2 from NOAA's public bucket) all
return an `OverlayRaster` (`maps/map_overlays.py:390`): encoded image bytes
with plate-carrée bounds and times. These are images from upstream servers,
which fits the tile/image mode of Section 11; no grid-mode radar exists yet.
Whether those GeoServer layers answer `EPSG:3857` tile requests for a MapLibre
raster source was not checked.

### 5.6 Soundings and analysis

`rendering/input.py::_decode_local_input` is the canonical Qt-free decode
path: `io/decoder.load_npz()` for portable `.npz` files, otherwise each
registered decoder in turn, then `accelerate_profile_collection()`. On the
bundled `examples/soundings/hrrr_point_36.68N_95.66W_f018.npz` it yields an
`AcceleratedConvectiveProfile` with MLCAPE 2465.3 J/kg, SBCAPE 2989.4 J/kg,
fixed-layer STP 3.64, STP (CIN) 5.16, and right-mover SCP 21.22, in about one second from
a cold process, with no Qt module loaded. These are deterministic values for
bridge contract tests. The analysis engines (`analysis/profile_metrics.py`
`ProfileMetricsEngine`, box analysis, ensembles) are Qt-free and already serve
both the GUI and the CLIs.

### 5.7 Backend health

`sharpmod.backends.backend_info()` returns a stable, serializable dict:
`requested_backend`, `active_backend`, `rust_installed`, `rust_version`, and
`fallback_reason`. With `sharpmod.__version__` it is a ready-made health-check
payload.

### 5.8 Existing process boundary

`ui/features/gui_batch_process.py::IsolatedBatchRunner` (:104) already runs
Python work in a child process. It writes a JSON spec, starts
`python -m sharpmod.tools.batch_extract ... --json-progress` (or
`sys.executable --model-batch-worker` when frozen, dispatched by
`packaging/sharpmod_gui_launcher.py:155`), reads `SHARPMOD_EVENT {json}` lines
from stdout (:35), and cancels with terminate, then kill. It is a working
precedent for a Python sidecar that speaks JSON lines, including dispatch
inside the frozen app.

### 5.9 Settings and state

Legacy preferences live in a Qt `QSettings` INI at `_settings_file_path()`
(`%APPDATA%\SHARPpy Reimagined\settings.ini` on Windows, overridable with
`SHARPMOD_SETTINGS_PATH`), defined in the Qt module
`ui/features/gui_settings.py`. `state/saved_locations.py` and the other
`state/` stores are Qt-free and accept any QSettings-like object.

## 6. Python import footprint

Cold-process measurements in `.gribenv` on Windows:

| Import | Time | Modules | Heavy dependencies |
|---|---:|---:|---|
| `sharpmod` | 6.7 ms | 83 | none |
| `sharpmod.maps.map_geography` | 8.7 ms | 86 | none |
| `sharpmod.models.lambert_grid` | 22.7 ms | 98 | none |
| `sharpmod.state.saved_locations` | 39.9 ms | 116 | none |
| `sharpmod.maps.map_overlays` | 42.8 ms | 100 | none |
| `sharpmod.ui.styles.theme` | 43.5 ms | 98 | none (Qt-free despite living under `ui/`) |
| `sharpmod.io.uwyo_catalog` | 79.8 ms | 159 | none |
| `sharpmod.maps.place_names` | 83.2 ms | 153 | none |
| `sharpmod.providers.spc_outlook` | 147.6 ms | 165 | none |
| `sharpmod.providers.radar_mosaic` | 151.6 ms | 159 | none |
| `sharpmod.backends` | 205.7 ms | 212 | NumPy (the native module loads on first use) |
| `sharpmod.analysis.profile_metrics` | 302.2 ms | 305 | NumPy |
| `sharpmod.sharptab.profile` | 305.5 ms | 223 | NumPy |
| `sharpmod.providers.observations` | 305.6 ms | 301 | NumPy |
| `sharpmod.io.decoder` | 368.0 ms | 337 | NumPy, upstream SHARPpy |
| `sharpmod.ui.features.gui_settings` | 370.3 ms | 191 | PySide6 |
| `sharpmod.tools.model_extract` | 375.5 ms | 282 | NumPy |
| `sharpmod.providers.hrrr_products` | 473.7 ms | 197 | NumPy |
| `sharpmod.providers.hrrr_field` | 845.4 ms | 263 | NumPy |
| `sharpmod.viz.colors` | 985.1 ms | 300 | PySide6 and NumPy, through `viz/__init__.py` |

For a bridge: `import sharpmod` is light, and its only process-level side
effect is `configure_windows_unicode_streams()`. Avoid `sharpmod.gui*`,
`sharpmod.ui.features.*`, `sharpmod.viz.*`, and `sharpmod.rendering.*`: they
import Qt, and `gui.py` and `viz/SPCWindow.py` set `QT_QPA_PLATFORM` and
`QT_API` as import side effects. Anything under `viz/`, including the Qt-free
`colors.py`, loads PySide6 through the package `__init__`.

## 7. sharpmod-rs reuse feasibility

Facts:

- [`rust/sharpmod-rs/Cargo.toml`](../../rust/sharpmod-rs/Cargo.toml):15
  declares `crate-type = ["cdylib", "rlib"]`, so another crate can take a path
  dependency.
- `pyo3 = { version = "0.29", features = ["extension-module"] }` (:22) and
  `numpy = "0.29"` (:21) are unconditional; there is no `[features]` table.
  `Cargo.lock` pins pyo3 0.29.2 and numpy 0.29.0. The other dependencies are
  `libloading`, `memchr`, `memmap2`, and `rayon`.
- PyO3 is confined to `src/lib.rs` (the `#[pyfunction]` wrappers and
  `#[pymodule] fn sharpmod_rs` at :770). No other module imports `pyo3` or
  `numpy`. The pure-Rust API is the `pub mod`s `wind`, `interpolation`,
  `quality_control`, `records`, `parsing`, `kinematics`, `parcels`,
  `batch_analysis`, and `grib`.
- `tests/kernel_contracts.rs` and `tests/batch_analysis_core.rs` already use
  the rlib through those modules. On this Windows machine their test
  executables import only system and C runtime DLLs, with no `python3xx.dll`.
  Linux and macOS were not checked.
- Any dependent still compiles PyO3 and runs its build script, which needs a
  discoverable Python interpreter; CI activates a Python 3.11 venv before
  `cargo test`. Since PyO3 0.27, maturin applies the extension-module linking
  mode on its own ([maturin tutorial](https://www.maturin.rs/tutorial)).
  [PyO3 issue #5202](https://github.com/PyO3/pyo3/issues/5202) explains that
  the feature only disables libpython linking, which breaks executables that
  need those symbols.
- Native GRIB decoding needs the ecCodes shared-library path at runtime. Today
  Python supplies it (`backends/rust_backend.py:48`, `_eccodes_library_path()`
  from the `eccodes` binding).
- The Python facade validates the native module (exact version, API 7, 18
  callables). Rust code linking the crate directly bypasses that handshake and
  does not need it.

Options for the design phase:

| Option | Effect | Cost |
|---|---|---|
| 1. No native link in Phase 1 | The station catalogue needs no kernels. Meteorological operations go through Python, which already runs `sharpmod_rs` behind `sharpmod.backends`, so nothing is duplicated | None now; revisit when a Rust-native command is needed |
| 2. Path dependency as-is | Works on Windows today | Every Tauri build compiles PyO3 and numpy and needs a Python interpreter |
| 3. A default `python` feature gating `pyo3`, `numpy`, and the `lib.rs` bindings | `src-tauri` depends with `default-features = false`; maturin builds keep the default | A small manifest change that must keep `cargo clippy --all-features`, `cargo test --all-targets`, maturin builds, and `test_backend_packaging.py` green |
| 4. Workspace split into a kernel crate and a bindings crate | Cleanest dependency graph | Most churn: packaging tests, `rust.yml` paths, the release workflow, and the `Cargo.lock` location |

Design input: option 1 for Phase 1 and option 3 when a native call is first
needed.

## 8. Geography and basemap data

| Resource | Source and license | Contents | Size | Current consumer |
|---|---|---|---:|---|
| `resources/basemap.json` | Natural Earth 1:50m, public domain ([`tools/build_basemap.py`](../../sharpmod/tools/build_basemap.py)) | Lines only. `coastline` 1,429 polylines and 60,392 points; `lakes` 370 and 17,525 (lake rings whose bounding box covers at least 0.05 square degrees); `countries` 422 and 19,364 (land boundary lines, split where they cross lakes); `states` 612 and 16,181. Coordinates rounded to 0.01°, world extent | 1.66 MB | `ui/maps/basemap_data.py::_load_basemap` for every picker map |
| `resources/coastlines.json` | Natural Earth 1:110m | 134 coastline polylines | 76 KB | Fallback in `_load_basemap` |
| `resources/conus-counties.zip` | U.S. Census 2025 county cartographic boundaries, 1:500k | 944 one-degree tiles with 1e-5° delta-encoded paths; 3,109 counties, 14,490 lines, 499,888 points; CONUS and DC; geometry only | 1.97 MB | Sounding locator outlines |
| `resources/conus-places.tsv.gz` | Census 2025 Gazetteer places and county subdivisions, plus state boundaries | 52,818 records (31,540 places, 21,278 towns and townships) and 831 state polygons | 2.34 MB | `maps/place_names.py` reverse lookup; station-map town labels through `_conus_place_cells()` (`ui/maps/presentation.py:241`) |
| `MAJOR_CITIES` (`maps/map_geography.py:84`) | Curated | 52 metros for continental views | | Station-map labels |
| `resources/radar_sites.json` | `tools/build_radar_sites.py` | WSR-88D sites | 28 KB | Radar markers |
| `resources/fonts/` | JetBrains Mono and Space Grotesk, published under the SIL Open Font License; the repository does not carry their license texts | TTF files | | Qt chrome and canvas |

Candidate no-API-key basemap: `basemap.json`, converted to one GeoJSON
`MultiLineString` feature per layer (as a build step or a bridge command) and
drawn as MapLibre `line` layers over a `background` layer. It has no land or
water polygons, which matches today's outline-on-flat-background look.
MapLibre `symbol` layers need a `glyphs` endpoint, so offline labels need
either self-hosted glyph PBFs or deck.gl `TextLayer`. The Qt map's hierarchy
is recorded in `maps/map_geography.py`: `BOUNDARY_ZORDER` (:24) draws states,
countries, lakes, then coastline, with widths 1.0, 1.2, 1.1, and 1.4; major
cities always label, towns appear below 30° of longitude span
(`LABEL_SPAN_STANDARD` :51), and the dense tier below 12° (:52). The county
tiles are a ready source for a later `CountyLayer`.

## 9. Design-token concepts in `ui/styles/theme.py`

Worth carrying into CSS variables
([`theme.py`](../../sharpmod/ui/styles/theme.py)):

- **Semantic colour roles** (`Theme`, :351): surfaces from back to front
  (`surface`, `surface_raised`, `surface_sunken`, `surface_overlay`); a
  `border` versus `border_strong` split, where the decorative hairline differs
  from the control boundary that must reach 3:1 for WCAG 1.4.11; four text
  levels; exactly one accent with hover, pressed, subtle, and on-accent text;
  `success`, `warning`, `danger`, and `info`; `focus_ring`.
- **Three themes**: `GRAPHITE_DARK` (:435, near-neutral graphite so data
  colours are the only saturated thing on screen), `PAPER_LIGHT` (:509), and
  `PROTANOPIA_DARK` (:561, status colours moved to the blue/yellow axis and
  separated by luminance). They follow the canvas `color_style` setting through
  `COLOR_STYLE_THEMES` (:754).
- **A contrast contract** enforced by `test_gui_theme_tokens.py` using
  `viz/colors.py::contrast_ratio`.
- **Scales**: `SPACE` on a 4 px base from 2 to 32 (:88); `RADIUS` 4, 6, 8, 10,
  and pill (:100); `CONTROL_H` 24 to 40, documented with 28 px as the minimum pointer target (:111); `FONT_PT`
  ramp from 8.5 to 20 pt (:143); `WEIGHT`; `MOTION_MS` 120, 180, and 240
  (:199); text scale 100 to 200 percent, independent of density (compact
  scales spacing by 0.75 and control heights by 0.875).
- **Font stacks**: `FAMILY_UI_STACK` led by Space Grotesk (:305) and
  `FAMILY_MONO_STACK` led by JetBrains Mono for tabular figures (:320).
- **`MapPalette`** (:571) as its own vocabulary: neutral geography, chromatic
  data, and marker meanings that stay stable across themes (amber selected,
  red available station, cyan saved location, blue model domain).
- **`AVAIL_STATUS_ROLES`** (:862): availability states map to roles, with
  `checking` on `info` so it is never confused with `fallback` on `warning`.
- **The scope rule**: chrome tokens never change scientific canvas colours,
  which belong to `viz/colors.py`.

Not worth carrying: QSS generation and `OBJ_*` object-name selectors,
point-based type sizes (a Qt DPI choice), the defence against the `QFont`
monkeypatch, popup row clamps, and pixel widths sized for Qt widgets
(`RAIL_W`, `FIELD_W`, `NAV_RAIL_W`, `VIEWER_SIDEBAR_W`). The glassmorphic
direction in the request has no precedent here: every Qt surface is opaque
and flat, so translucency, blur, elevation, and highlight tokens are new.

## 10. Tests that protect scientific behaviour

| Protection | Where | Size |
|---|---|---|
| Science category in the fast lane | `test_box_analysis`, `test_box_sounding`, `test_box_mean`, `test_winter_snow_ratio`, `test_fire_ventilation`, `test_profile_metrics`, `test_interp`, `test_params_*`, `test_profile_*_backend`, `test_derived_*`, `test_ecape_*`, `test_hazard_*`, `test_winds_*`, and others | 37 modules, 486 tests |
| Property contracts | 46 Hypothesis tests marked `property`, such as `test_derived_physical_range_property`, `test_reference_agreement_property`, and `test_decode_legacy_equiv` | At least 100 examples each in the `property` lane, 10 in fast and compatibility |
| Python/Rust equivalence and selection | `test_backend_equivalence.py`, `test_grib_backend_equivalence.py`, `test_backend_integration.py`, `test_backend_selection.py`, `test_python_backend.py` | 152 tests |
| Providers and decoders | Providers category | 39 modules, 972 tests |
| Rust contracts | `cargo test` (`tests/kernel_contracts.rs`, `tests/batch_analysis_core.rs`, unit tests) and Criterion benches | |

The suite collects 4,721 tests from 228 files. The other fast-lane categories
are maps (1,086 tests), interface (891), charts (469), analysis (341),
platform (276), and exports (151).

How CI runs them:

- **`.github/workflows/tests.yml`** runs on pushes and pull requests to `main`,
  weekly, and from the release workflow, with `QT_QPA_PLATFORM=offscreen` and
  `SHARPMOD_TEST_WORKERS=4`:
  - `compatibility`: Python 3.11 and 3.12 on the Python fallback, 2 workers.
  - `fast`: Python 3.13 with the Rust wheel built by maturin,
    `SHARPMOD_BACKEND=rust`, 4 workers, coverage (`fail_under = 35`).
  - The "test tree" category jobs re-report the fast JUnit without rerunning
    tests.
  - `property`: Python 3.13 on both the `python` and `rust` backends, 2
    workers each.
  - `serial-release` (release only), `windows-wrf`, `quality` (two Ruff runs
    and `pip-audit`), and `live-provider` (weekly or opt-in).
- **`.github/workflows/rust.yml`** runs on pull requests touching the crate,
  the backends, and other listed files: `cargo fmt`,
  `cargo clippy --all-targets --all-features -D warnings`, and
  `cargo test --all-targets` on stable and the 1.88 MSRV, then
  `maturin develop`, a forced-Rust smoke test, equivalence tests on NumPy 1.26
  and 2.x, and wheels for Windows, Linux, and macOS on CPython 3.11 and 3.12.
- **The lane runner** `scripts/run_test_lane.py <lane>` writes JUnit and timing
  JSON under `.test-results/` and enforces
  `constraints/test-performance-baseline.json`. Local budgets: fast 370 s
  (baseline 308.18 s) and compatibility 380 s (baseline 318.72 s). On this
  machine the pre-change runs took 393.3 s and 556.8 s. Every assertion passed
  (4,671 and 4,717 passed, one skipped each), but both budget gates already
  fail.

Constraints on adding tests:

- New Python test modules must be classified in
  `scripts/summarize_test_categories.py::category_for_module`; unmatched names
  fall into `providers`.
- Workflow-structure tests parse `tests.yml`, `release.yml`, and `rust.yml`
  (`test_test_lane_runner.py`, `test_test_category_summary.py`,
  `test_backend_packaging.py`, `test_model_fetch_packaging.py`,
  `test_release_packaging.py`). A separate workflow file for the frontend is
  lower risk than editing `tests.yml`.
- Every added Python test lengthens lanes that are already over their local
  budgets.

## 11. Migration boundaries

The rule: the backend produces meteorological data, and the frontend produces
pixels.

| Domain | Backend produces | Frontend produces | Legacy seam to reuse |
|---|---|---|---|
| Stations | Catalogue records (id, name, lat, lon, src) and an honest availability state | Markers, zoom-dependent labels (the Qt map adds ids at or below 55° and names at or below 16° of longitude span, `ui/maps/station_labels.py:21`, :143), hover, selection, and search UI | `uwyo_catalog.all_stations()`, `fetch_stations_for_datetime()` |
| Station search | Optionally `search_stations()` | Filtering UI | Matching ids and names is interface logic, not science; mirror it exactly or call the backend |
| Models and runs | `ModelConfig` metadata, run and hour publication rules, availability probes | Selectors, the central forecast-time state, the timeline | `model_extract.available_models()`, `hrrr_field.resolve_request()` and `pin_request()`, `tools/model_extract_probe.py` |
| Model domains | GeoJSON from `domain_outline`, or `domain_bounds` when there is no outline | `ModelDomainLayer` | Replaces `ui/maps/point.py::_draw_domain` |
| Model fields | A binary numeric grid, its grid definition, run, forecast hour, valid time, units, palette data, and caveat | GPU colouring, contours, opacity, legend | Cut `render_field()` after `derive` and `reproject` |
| SPC outlooks | Normalized GeoJSON with semantic properties and validity windows | Fill, outline, hatch, labels, z-order | `parse_outlook()` without `_feature_style()` |
| Radar and satellite | A WMS request description or an image with bounds; grid data later | MapLibre raster or image sources | `radar_mosaic.build_query()` and `fetch_frame()` |
| Storm reports and surface observations | Typed point records | Symbols, station plots, barbs | The providers, before `OverlayShape` is built |
| Basemap | Static geometry | MapLibre style | `basemap.json` |
| Soundings and indices (Phases 9 and 10) | Profile values from SharpTab and the backends; parcel traces as typed arrays | Tables, Skew-T, hodograph | The `rendering/input.py` decode path. The paint-time calculations in §4 move into the backend first |
| Errors | Typed provider exceptions (`HrrrFieldError`, `HrrrFieldUnavailable`, `HrrrFieldOffline`, `OutlookError`, `StationListError`, the observed-provider errors, `BackendUnavailableError`) | Structured user-facing messages | Map exception classes to stable error codes in the bridge |
| Preferences | The legacy INI at `_settings_file_path()` | New application settings | Decide whether the two apps share settings |

Nothing has migrated yet; every user-facing surface is still Qt. Progress is
tracked in [MIGRATION.md](MIGRATION.md).

## 12. Risks, gaps, and environment

1. The requested `Station.available: boolean` has no direct source (see the
   two legacy notions in §5.1).
2. The catalogue has no ICAO or three-letter identifiers (the request's
   inspector mock shows "KOUN"), and its names are UWyo free text.
3. The live station list depends on `weather.uwyo.edu`. Startup must not wait
   for it, and the bundled list remains the offline answer.
4. A Python sidecar has to find its interpreter. Development uses `.gribenv`
   (`.griblenv` is stale); release builds are PyInstaller bundles with the
   `--model-batch-worker` dispatch precedent. Shipping Python inside a Tauri
   build is a later decision.
5. Profile work needs the upstream SHARPpy wheel from
   `scripts/install_sharppy_compat.py`; the station catalogue does not.
6. The numeric HRRR grid is never cached on disk (PNG only), so a numeric
   field API needs its own bounded cache.
7. A new Python package, such as a bridge package, must be added to the
   explicit `[tool.setuptools] packages` list in `pyproject.toml`. Any new
   dependency, extra, or console script must also be mirrored in
   `installation.txt` (see `CONTRIBUTING.md`).
8. `.gitignore` patterns apply repo-wide: `build/`, `dist/`, `env/`, `*.spec`,
   `*.manifest`, `*.log`, `*.tmp`, and `.cache/`. `node_modules/` and
   `src-tauri/target/` are not ignored yet; only `/rust/**/target/` is.
   `.editorconfig` has no TypeScript or Rust sections.
9. Production Python modules are meant to stay under 1,000 lines
   (`docs/CODE_STRUCTURE.md`); `ui/maps/point.py` (1,048) and
   `ui/picker/forecast_pages.py` (1,318) already exceed it. Section 29 of the
   request extends the no-giant-files rule to TypeScript and Rust.
10. Release CI builds only the PyInstaller Windows app; no Node or Tauri
    toolchain runs in CI yet.
11. Local toolchain at audit time: Node 24.12.0, npm 11.6.2, Rust 1.97.0
    (`x86_64-pc-windows-msvc`), Visual Studio 2022 C++ build tools, and the
    WebView2 runtime 154.0.4258.48. The Tauri CLI is not installed globally;
    it would come from the frontend's development dependencies.

## Appendix A: Reproducing the measurements

From the repository root in PowerShell:

```powershell
$py = ".\.gribenv\Scripts\python.exe"

# Import footprint of one module in a cold process.
& $py -c "import sys, time; t = time.perf_counter(); import sharpmod.io.uwyo_catalog; print(round((time.perf_counter() - t) * 1000, 1), 'ms', len(sys.modules), [m for m in ('qtpy', 'PySide6', 'numpy', 'sharppy') if m in sys.modules])"

# Qt-free decode, profile, and derived parameters on the bundled example.
& $py -c "import sys; from sharpmod.io import decoder; from sharpmod.sharptab.accelerated_profile import accelerate_profile_collection as accelerate; col, _ = decoder.load_npz('examples/soundings/hrrr_point_36.68N_95.66W_f018.npz'); prof = accelerate(col).getHighlightedProf(); print(round(float(prof.mlpcl.bplus), 1), round(float(prof.stp_fixed), 2)); print('Qt loaded:', any(k.startswith(('qtpy', 'PySide6')) for k in sys.modules))"

# Station catalogue facts.
& $py -c "from sharpmod.io import uwyo_catalog as c; rows = c.all_stations(); print(len(rows), rows[0])"

# Model registry.
& $py -c "from sharpmod.tools import model_extract as m; print(len(m._CONFIGS), len(m.available_models()))"

# Property-test count (collection only).
$env:QT_QPA_PLATFORM = "offscreen"
& $py -m pytest --collect-only -q -m "property and not live_provider" -p no:cacheprovider | Select-Object -Last 1
```

The static import scan parsed every production module with `ast`, recorded
module-scope imports separately from function-scope imports, and flagged Qt
(`qtpy`, `PySide6`), upstream SHARPpy, network, GRIB, and cross-package
imports. Per-category test counts come from classifying the baseline fast-lane
JUnit with `scripts/summarize_test_categories.py::category_for_module`.

## Corrections and clarifications

Added during Phase 1, after the facts below were rechecked against the
repository at `f354fd5`. The sections above are unchanged.

1. **Empty station names.** 28 of the 933 bundled stations have an empty name,
   for example `03023`, `03882`, `04089`, and `06458`. The new frontend shows
   them as "Unnamed station".
2. **Silent seed fallback.** `load_catalog()` falls back to the 10-station
   `SEED_STATIONS` list on any exception without telling its callers
   (`sharpmod/io/uwyo_catalog.py:117`). The bridge detects the fallback by
   comparing the records with `SEED_STATIONS` and reports
   `origin: "seed_fallback"`, so the UI can warn.
3. **More ignore rules.** Besides the patterns §12 item 8 lists, `.gitignore` also
   ignores `.env`, `.env.*`, and `/.agents/`.
4. **Data defect in the catalogue.** Record `73111`'s name is 135 characters
   with six newlines and pandas repr text (`"Stationid\n73111    Kapuskasing\n…"`),
   and 13 names contain runs of spaces. The canonical file is unchanged,
   because resource data is out of scope; the frontend normalizes whitespace
   for display only (`displayName` in `frontend/src/stations/stationFormat.ts`).
5. **Catalogue size (a clarification, not a correction).** The "about 94 KB"
   in §5.1 is the record list under Python's default `json.dumps` separators
   (93,591 bytes). The bridge sends it compact: 84,262 bytes for the list and
   84,312 bytes for the `stations.catalog` result. The public camelCase DTO is
   120,741 bytes.
