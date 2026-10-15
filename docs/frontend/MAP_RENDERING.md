# Map rendering

How the new frontend draws the map, the station layers, and the weather
layers. Code lives under `frontend/src/map/` unless noted. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the boundary rule and
[DATA_CONTRACTS.md](DATA_CONTRACTS.md) for the data the layers consume.

## Roles

| Library | Owns |
|---|---|
| MapLibre GL JS 6.12.0 | Projection (Web Mercator), camera, pan and zoom, the basemap style layers, attribution, keyboard and pointer interaction |
| deck.gl 9.4.0 (`@deck.gl/maplibre` `MapLibreOverlay`) | Scientific and data layers: model fields, radar, SPC outlooks, the model domain, station markers and labels, and the selection; offline fixtures also draw local boundaries and place names |
| React | Orchestration only: it mounts the maps, renders panels and popovers, and never renders per-station DOM or re-renders per frame |

`WeatherMap.tsx` only wires: it lays out one, two, or four panels
(`panels/MapPanel.tsx`), portals the map toolbar, notices, and layer panel into
the shell's overlay slots, and runs playback (`usePlayback.ts`). Each panel
creates its own `MapRuntime` (plain TypeScript, `runtime/MapRuntime.ts`) in a
mount effect, feeds it its scene (`panelScene.ts`), and disposes it on
unmount. Panel 0 is the primary map: the startup watch, the notices, the
camera snapshot, and the test hooks are its. `WeatherMap` is loaded with
`React.lazy`, so the shell paints before the MapLibre and deck.gl chunk is
parsed, and it is keyed by the profile's antialias value and
`graphicsStore.mapAttempt`, so a context-level change or Reload map remounts it.

## The overlay, before `load`

`MapRuntime` adds the deck.gl overlay (`runtime/overlay.ts`) with
`map.addControl` right after constructing the map, without waiting for `load`,
so the overlay is ready as soon as the style loads. MapLibre's `load` only sets
the `sharppy:map-loaded` mark.

**Live and desktop maps use `interleaved: true`.** The MapLibre adapter supports
MapLibre 6 through public APIs, replacing the Mapbox adapter that failed on its
removed private transform. Weather layers receive `beforeId` in the shared style
stack. Original political boundaries, waterways, and labels draw above weather;
roads and their casings remain below it. Stations and selections draw last.
No duplicate geographic outlines are added. The profile's MapLibre pixel ratio
sets the shared canvas resolution. Basemap opacity scales only its original
paints, preserving zoom curves and leaving weather opacity independent.

Offline fixtures retain `interleaved: false` and their local boundary pass so
missing-source and startup scenarios remain independent of network styles.

## Layer registry

`layers/registry.ts` holds the types, `LAYER_IDS`, `ORDERABLE_LAYER_IDS`,
defaults, and pure helpers (`buildDeckLayers`, `applyStyleLayers`,
`controllableLayers`, `clampOpacity`, `moveInOrder`); `layers/descriptors.ts`
holds the descriptors:

| Id | Kind | zOrder | User control | Module |
|---|---|---|---|---|
| `basemap` | MapLibre style layers | 0 | visibility, opacity | `basemap/basemapLayers.ts` |
| `model-field` | deck.gl `BitmapLayer` | 10 to 15, in the user's order | visibility, opacity, product, order | `layers/rasterLayers.ts` |
| `radar` | deck.gl `BitmapLayer` | 10 to 15 | visibility, opacity, product, order | `layers/rasterLayers.ts` |
| `spc-cat`, `spc-torn`, `spc-wind`, `spc-hail` | deck.gl `SolidPolygonLayer` fill, `PathLayer` hatch and outline, `TextLayer` labels | 10 to 15 | visibility, opacity, order | `layers/SPCOutlookLayer.ts` |
| `boundaries` | deck.gl `GeoJsonLayer` per basemap family | 60 | none; drawn while a weather layer is shown | `layers/geographyLayers.ts` |
| `model-domain` | deck.gl `PathLayer` | 70 | visibility | `layers/geographyLayers.ts` |
| `place-labels` | deck.gl `TextLayer` | 80 | visibility | `layers/geographyLayers.ts` |
| `stations` | deck.gl `ScatterplotLayer` | 100 | visibility, opacity, availability release | `layers/StationLayer.ts` |
| `station-labels` | deck.gl `TextLayer` | 110 | visibility | `layers/StationLayer.ts` |
| `selection` | deck.gl `ScatterplotLayer`s: the station halo and marker, or the forecast point | 200 | none; shown while something is selected | `layers/SelectionLayer.ts` |

The six weather layers share zOrders 10 to 15 in `mapStore.layerOrder`, which
the layer panel's up and down buttons change (top first, as the legacy
`LAYER_PAINT_ORDER` stacks them by default: fields, radar, then the outlooks).
`buildDeckLayers` sorts by `zOrder` and passes `visible` and `opacity` as layer
props, so a toggle keeps the GPU buffers. The layer panel
(`controls/LayerPanel.tsx`) lists the controllable descriptors of the active
panel; their state is in `stores/mapStore.ts`. The first panel's visibility
and opacity persist; the order and the other panels' states last for the
session.

**Adding a layer:** write its module, add a descriptor to `descriptors.ts`, and
add its id to `LAYER_IDS`. `WeatherMap.tsx` does not change;
`layers/registry.test.ts` proves it with a test-only descriptor.

## Basemap

- **Pipeline.** `frontend/tooling/basemapPlugin.ts` (a Vite plugin, using
  `tooling/basemap.ts`) converts `sharpmod/resources/basemap.json`, the data the
  Qt map uses, into `geo/states.geojson`, `countries`, `lakes`, and
  `coastline` at dev and build time. Nothing generated is committed.
- **Style.** `basemap/basemapStyle.ts`: style version 8, four `geojson`
  sources (`maxzoom: 8`), a `background` layer and `basemap-states`,
  `basemap-countries`, `basemap-lakes`, `basemap-coastline` line layers, with
  colours and widths from the `--map-*` tokens. No glyphs, sprites, or symbol
  layers; station labels come from deck.gl.
- **Attribution.** Each source carries "Natural Earth"; the compact
  attribution control is restyled by `maplibre-overrides.css` and moved above
  the timeline; with two or four panels only the first shows it. The weather
  layers name their sources in the layer panel. No API key, no remote style,
  no tile server.
- **Worker.** MapLibre 6 tiles GeoJSON in a module worker whose URL it computes
  from `import.meta.url`, which Vite cannot see, so a plain build ships no
  worker and no source ever loads. `runtime/maplibreWorker.ts` imports
  `maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url` and calls
  `setWorkerUrl()`; `worker.format: "es"` in `vite.config.ts` makes the chunk a
  module. The worker is same-origin, which `worker-src 'self'` covers. In the
  desktop app its requests reach the embedded assets because wry 0.57.0
  registers Tauri's protocol filter for all request source kinds on WebView2
  runtimes that expose `ICoreWebView2_22`. Each build check requires exactly
  one `assets/maplibre-gl-worker-*.js` in `dist/`, `dist-browser/`, and `dist-fixture/`.

## Startup watch

`runtime/startupWatch.ts` is a pure state object fed by `MapRuntime`.

- **Marks** (`lib/metrics.ts`): `sharppy:shell-painted`, `map-loaded`,
  `basemap-loaded` (all four sources loaded, none failed),
  `catalog-requested`, `catalog-received`, `stations-rendered` (the first
  deck.gl render after a visible, non-empty station layer).
- **Visible-time deadline.** `MAP_STARTUP_TIMEOUT_MS` is 15 s of visible time.
  MapLibre draws only from `requestAnimationFrame`, which browsers pause in a
  hidden page, so a wall-clock deadline would fail every map built in a
  background tab. The timer pauses while the page is hidden and fires at most
  once.
- **Basemap sets.** A source named by an `error` event is *failed* until Reload
  map. A source still pending at the deadline is *slow*, and leaves that set
  when it later loads. Either set raises "Some map layers could not be drawn"
  with Reload map (`notices/MapNotices.tsx`); a slow source that loads withdraws
  it. The stations keep rendering.
- **Renderer.** The first deck.gl `onAfterRender` sets `renderer: "ready"`,
  independent of the catalogue. A constructor throw, an overlay error before the
  first render, or the deadline with no render sets `renderer: "failed"` and
  the shell shows the reduced view with Try the map again.
- **Other errors.** Later MapLibre `error` events and overlay errors are logged
  once with `console.warn` and counted in `mapNoticeStore.runtimeErrors`; a
  lost WebGL context raises "Map rendering stopped because the graphics device
  was reset" with Reload map.
- **Remount and dispose.** Reload map and Try the map again call
  `graphicsStore.retryMap()`, which remounts `WeatherMap` with a fresh runtime
  and watch. `dispose()` sets a disposed flag first, so late callbacks from the
  old map (including its deliberate context loss in `map.remove()`) are no-ops.

## Station presentation

- **Markers** (`layers/StationLayer.ts`): a stable data array, `pickable`,
  `autoHighlight` with `--map-station-hover`, fill and edge by availability
  from canvas tokens. `unknown` is a neutral grey (`--map-station-unknown`),
  never the legacy "available" red, which is reserved for `listed`. No
  clustering: 933 points do not need it.
- **Selection** (`layers/SelectionLayer.ts`): a one-point halo and marker in
  the legacy selected amber, so changing the selection never rebuilds the
  933-point buffers.
- **Labels and tiers.** `labels/labelPolicy.ts` picks a tier from the visible
  longitude span and the profile; a two- or four-panel map takes the span the
  window-wide map would show at its zoom (`equivalentSpan`), so a narrow panel
  is labelled as the single map is. `labels/declutter.ts` keeps labels at
  least one cell apart on either axis on `moveend` (selected station first,
  then by id), so two stations a few pixels apart across a cell edge get one
  label.

| Profile | Ids when span ≤ | Names too when span ≤ | Declutter cell |
|---|---|---|---|
| High | 55° | 16° | 48 px |
| Balanced | 55° | 16° | 64 px |
| Low | 30° | 8° | 96 px |

  High and Balanced use the legacy thresholds (`sharpmod/ui/maps/station_labels.py`).
- **Tooltip** (`hover/StationTooltip.tsx`): a glass popover near the pointer
  with the id, the normalized name, coordinates, "Availability unknown", and
  the source. Hover state lives only in `hover/hoverStore.ts`.
- **Click** selects; Esc or Clear selection clears it. Search and the
  inspector's Zoom to station issue a `focusRequest`, which flies to the
  station with the `--motion-camera` duration (0 under reduced motion).
- **Formatting** (`frontend/src/stations/stationFormat.ts`) is display only:
  whitespace runs collapse (`displayName`), empty names show "Unnamed
  station", coordinates read "35.18° N, 97.44° W".

**Notice slots.** The map overlay is a three-column grid (map toolbar, centre,
right column). The centre column stacks `CatalogueStatusNotice` (loading,
empty, seed fallback, or the structured catalogue error) above `MapNotices`
(basemap, runtime, and context-loss notices), at `--z-notice`. Grid columns
cannot overlap, so a notice never covers the map toolbar or the panels.

**Availability** (`controls/AvailabilityControl.tsx`, `stations/availability.ts`).
Choosing a recent release asks the backend for UWyo's list at that time; the
catalogue query's `select` marks every station `listed` or `notListed` and
adds the listed stations the bundled file lacks, so the map, search, and the
inspector all see the same list. A failed list keeps the bundled stations and
says so.

## Weather layers

- **Model field** (`layers/rasterLayers.ts`, `field/colourize.ts`). A frame is
  2048 by 1024 uint16 cells over HRRR's bounds, row 0 at the north edge, rows
  evenly spaced in latitude ([DATA_CONTRACTS.md](DATA_CONTRACTS.md#binary-frames)).
  `colourize` builds the product's 1024-entry lookup table from its sharpmod
  palette (stops, `stepped`, `floor`, `ceiling`) the way
  `hrrr_field_images.build_lut` does, then one 65,536-entry table per frame
  from cell to colour, so colouring is two lookups a cell; no data and values
  outside the drawn range are transparent. `field/colourize.test.ts` compares
  the table with values Python computed. The image goes to a `BitmapLayer`
  with `_imageCoordinateSystem: "lnglat"`: deck.gl finds
  each screen pixel's place in the equirectangular image, so rows land at
  their latitudes in Web Mercator. **Pixel rendering** in the Model field
  controls offers Sharp (nearest sampling, the default) and Smooth (bounded
  Catmull–Rom interpolation of model values before palette lookup). Smooth
  reconstructs field shapes instead of blending the finished RGBA image.
  It retains source sample values, clamps interpolation to the surrounding
  cell range, and applies the same 1024-entry color table. Palette boundaries
  receive only one screen pixel of antialiasing. Missing cells stay empty;
  near missing neighbors the valid bilinear weights replace cubic sampling.
  The choice applies to every model parameter in that panel;
  the main panel's choice persists across reloads. Smooth uploads a cached,
  lossless RG encoding of the uint16 grid with a separate validity channel;
  deck.gl owns and releases both grid and palette textures.
  With the dev server running, `/tooling/raster-smoothing-check.html` checks
  the real GPU shader against Sharp and the former color-space linear filter
  at three zooms, plus peaks, missing data, and palette display limits.
  See [NVIDIA's cubic filtering discussion](https://developer.nvidia.com/gpugems/gpugems2/part-iii-high-quality-rendering/chapter-20-fast-third-order-texture-filtering)
  and [Matplotlib's data versus RGBA interpolation](https://matplotlib.org/3.11.2/api/_as_gen/matplotlib.pyplot.imshow.html).
- **Legend and values** (`controls/FieldLegend.tsx`, `panels/MapPanel.tsx`).
  The legend draws the palette with sharpmod's ticks and says which values are
  not drawn. The panel chip names the product and frame and, under the
  pointer, the value of the cell there (`sampleAt`: the backend's number, no
  interpolation).
- **Radar** (`layers/rasterLayers.ts`). The service's PNG of the newest
  4096 by 2048 mosaic, or of one antenna (`useRadarSiteFrame` with the effective site from
  `useEffectiveRadarSite`: the pinned choice or the nearest catalogue antenna
  to the map centre, `nearestSite.ts`), decoded once with `createImageBitmap`
  and drawn like a field. Both refetch at the service's `updateIntervalS`
  and are not tied to the forecast time, which the layer panel says; the
  scope switch and the site picker (the bundled catalogue, `useRadarSites`)
  live in the radar row, each scope with its own product list from the
  catalogue (`siteProducts` versus `radarProducts`).
- **Satellite** (`layers/rasterLayers.ts`). One GOES frame over the map's
  extent, rendered by `satellite_context.fetch_satellite_overlay` in the bridge
  (`bridge/satellite.py`) and drawn like a field. The layer follows the
  forecast valid time (the scan nearest it within the provider's tolerance);
  the channel (visible or clean-IR) is the row's own control, and the status
  states the actual scan time, its offset from the requested one, the view
  coverage, and the native resolution. Uncovered ground is transparent in the
  frame itself. Output keeps source detail up to four million pixels, without
  the previous 1024-pixel width cap. Radar and satellite use nearest sampling
  at every zoom, so source cells stay sharp rather than blending together.
  Radar and satellite keep their last good image if a refresh fails; the
  layer controls show the failure and the time of the retained frame.
- **Storm reports** (`layers/StormReportLayer.ts`). The provider's own
  screen-marker spec — symbol name, pixel radius, fill and stroke — drawn as
  fixed-size glyphs: a stroke pass behind a fill pass (TextLayer's outline
  colour is flat in this deck.gl version, so the provider's two colours are two
  marks), plus the short caption beside each marker at close zoom
  (`span ≤ 16°`, the zoom station names appear). Hover reads the full report
  (`hover/ReportTooltip.tsx`). The layer follows the forecast valid time with
  the provider's event windows (±1/3/6/12 hours) and hazard filter, whose
  labels come from `get_report_options`; the filter stays the provider's own.
- **Surface observations** (`layers/SurfacePlotLayer.ts`, `layers/plotMarks.ts`,
  `map/surfaceQueries.ts`). Nearby stations as typed point records
  (`get_surface_plots`) around the map's own selection at the forecast valid
  time. Each station model is drawn in the paint path's pixel layout: a wind
  staff whose pennants, barbs, and half arrive as the mark spec (the provider's
  own speed split), the reading pair in the plot's upper and lower slots, and
  one shape per state — calm circle, cross, square with a question, a solid
  ring and cross for a checked station, a dashed ring and question for a failed
  query, and a dashed ring over a stale plot. Hover reads the provider's
  description (`hover/ReportTooltip.tsx`).
- **Contour lines** (`layers/ContourLayer.ts`, `map/contourQueries.ts`). The
  product's contour field as lon/lat segments (`get_field_contours`), drawn
  with the catalogue's own `ContourSpec` (colour, width, interval) over its
  coloured field. They ride with `model-field`: one row, one product, and the
  row states the interval. The level set is the multiples of the interval, the
  same lines `draw_contours` stamps into the legacy PNG; cells with a
  non-finite corner contribute nothing, so holes stay empty.
- **SPC outlooks** (`layers/SPCOutlookLayer.ts`, `layers/outlookGeometry.ts`).
  Four layers, each its own query at the forecast valid time. An area draws
  SPC's fill as a wash (alpha 0.4) and its stroke, a label inside it
  (`labelPoint`, inside concave and holed areas too), and for significant
  severe even-odd diagonal hatching (`hatchSegments`: level 1 every 1°, the
  others every 0.5°, level 3 cross-hatched). Hovering an area shows its label
  and description (`hover/OutlookTooltip.tsx`). An outlook loaded for an
  earlier time keeps answering while it covers the new one (`coveringTime`).
- **Boundaries over the weather** (`layers/geographyLayers.ts`). The deck.gl
  canvas covers MapLibre's, so a field at full opacity would hide every
  border. While a weather layer is shown, the basemap's state, country, and
  coastline GeoJSON (the same assets) is drawn again above the weather.
- **Model domain and place names** (`layers/geographyLayers.ts`,
  `labels/placeLabels.ts`). The domain is the model's grid perimeter from
  `get_model_catalog`. Place names follow the legacy map: major cities at
  every span, towns once the view spans 30° or less, names twice the station
  cell apart and clear of the station labels, each name up to its first comma.

## Forecast time and playback

- **One time.** `stores/forecastStore.ts` holds the model, run, forecast hour,
  and whether it plays; `features/forecast/useForecast.ts` resolves them
  against the model list and the map catalogue (HRRR hours stop at
  `maxForecastHours[run hour]`). The toolbar, the timeline, every panel, the
  SPC queries, and the point inspector read that one value.
- **Frames.** Each field frame is a TanStack Query keyed by product, run, and
  hour (`mapQueries.ts`). While a new hour loads, a panel keeps the previous
  frame of its product, marked as loading. After each new frame the cache
  drops the oldest frames no panel shows until it is within the profile's CPU
  budget (`trimFieldCache`).
- **Playback** (`usePlayback.ts`). Playing steps on 750 ms after every drawn
  product's frame for the hour has loaded or failed, so a slow download holds
  the animation instead of skipping it, and loops at the run's last hour. The
  next hours are prefetched one frame at a time for every drawn product: the
  profile's `next` frames, plus `prefetched` while playing.

## Panels

- **Layout.** One map fills the window under the chrome. Two or four panels
  share the box inside the toolbar, rail, timeline, and the inspector and
  layer column (`WeatherMap.module.css`), so no panel is covered.
- **Shared.** One camera (`runtime/cameraLink.ts`: a panel's `move` jumps the
  others, behind a re-entrancy flag), forecast time, run, and selection.
  Queries are shared, so panels showing one product fetch it once.
- **Per panel.** Product, layer visibility, opacity, and field smoothing (`mapStore.panels`);
  the layer panel edits the active panel, which is outlined. A new panel
  starts with the first product of another category, its field on.
- **Forecast point.** A click on empty map selects a point (never at the same
  time as a station). The inspector lists each panel's value there and, per
  outlook shown, the highest-ranked area containing it (`containsPoint`), and
  opens an HRRR sounding at it.
- **Rendering.** Each panel has its own MapLibre map and deck.gl scene, so
  GPU buffers are per panel; the data they draw is shared on the CPU. Four
  panels scale the pixel ratio by the profile's four-panel scale, never below
  one device pixel per CSS pixel (text blurred at 0.75), so the scale lowers
  resolution only on high-DPI screens.

## Graphics profiles

**Probe** (`frontend/src/lib/graphics/probe.ts`, run in `main.tsx` before React
renders): WebGL2 availability, `MAX_TEXTURE_SIZE`, the float extensions
(`EXT_color_buffer_float`, `OES_texture_float_linear`, `EXT_float_blend`), a
`failIfMajorPerformanceCaveat` context, device pixel ratio, viewport, and the
unmasked renderer string as a hint only. Both probe contexts are released at
once.

**Selection** (`lib/graphics/profile.ts`, pure, unit-tested); the first
matching row wins:

| # | Condition | Result |
|---|---|---|
| 1 | No WebGL2 | reduced mode (station list), glass at `low` |
| 2 | Override is not Automatic | the override |
| 3 | Performance caveat, or a software renderer | Low |
| 4 | `MAX_TEXTURE_SIZE` < 8192 | Low |
| 5 | Integrated GPU and ≥ 8,294,400 physical pixels | Low |
| 6 | Integrated GPU | Balanced |
| 7 | No float render targets | Balanced |
| 8 | Discrete GPU | High |
| 9 | Otherwise | Balanced |

Automatic is the default; it is re-evaluated at startup and when the device
pixel ratio changes. The override is persisted in `sharppy.prefs.v1`.

**Per-profile settings** (`lib/graphics/profileSettings.ts`; glass values in
`frontend/src/styles/tokens.css`):

| Setting | Low | Balanced | High | Applied now |
|---|---|---|---|---|
| MapLibre `pixelRatio` and deck.gl `useDevicePixels` | 1 | min(DPR, 1.5) | min(DPR, 2) | yes |
| Antialiasing | off | off | on | yes |
| Label tiers and declutter cell | 30° / 8° / 96 px | 55° / 16° / 64 px | 55° / 16° / 48 px | yes |
| Glass | no blur, fill 0.94 | blur 12 px, saturate 120%, fill 0.74 | blur 18 px, saturate 130%, fill 0.62 | yes |
| GPU texture budget | 128 MB | 256 MB | 512 MB | no: each panel holds one field and one radar texture |
| CPU field cache | 256 MB | 512 MB | 1,024 MB | yes (`trimFieldCache`) |
| Frame window: previous / next / prefetched | 1 / 1 / 0 | 1 / 1 / 1 | 2 / 2 / 1 | next and prefetched (`usePlayback.ts`); previous frames stay only while the cache holds them |
| Four-panel render scale | 0.5 | 0.75 | 1.0 | yes, never below one device pixel per CSS pixel |
| Render scale while interacting | 0.75 | 0.85 | 1.0 | no |

`prefers-reduced-motion` zeroes the motion tokens; `prefers-reduced-transparency`
and missing `backdrop-filter` support make glass opaque under every profile
([DESIGN_SYSTEM.md](DESIGN_SYSTEM.md#glass)).

**Reduced-rendering mode** (`frontend/src/features/fallback/`). When WebGL2 is
missing, or the renderer fails to start, the map workspace shows
`ReducedRenderingView`: a fixed notice per cause, `CatalogueStatusNotice`, and
`StationList`, a keyboard-navigable listbox of every station. Search,
selection, and the inspector keep working. With no WebGL2 the lazy map chunk is
never loaded and the notice has no button (the probe cannot change); every
start-up failure offers Try the map again.

## Projection: display resampling versus scientific interpolation

The field routes select **3 km CONUS output**, not each model's native domain.
HRRR, NAM CONUS nest, RRFS CONUS, and REFS m001–m005 use the common
1799 × 1059 Lambert grid. A live check of each model's 15 October 2026 06Z,
F001 reflectivity record confirmed 3000 m spacing, a 38.5° tangent cone,
262.5°E central meridian, 6,371,229 m sphere, and identical scan directions.
NAM rounds its first point to 21.138°N, 237.280°E; the other files encode
21.138123°N, 237.280472°E, a difference under 55 m on the 3 km output grid.
[NOAA's NAM change notice](https://www.weather.gov/media/notification/tins/tin16-41nam_updates_aac.pdf)
identifies the shared HRRR output grid, and the
[RRFS product catalogue](https://www.nco.ncep.noaa.gov/pmb/products/rrfs/)
lists its CONUS dimensions. Native North American or other output domains
would require their own geometry; those files are not selected by these routes.

MapLibre renders Web Mercator. HRRR's native grid is Lambert conformal. The
rule:

- **Scientific interpolation is backend work.** sharpmod reprojects the
  derived field to an evenly spaced latitude-longitude frame
  (`hrrr_field.index_map` and `reproject`, the legacy map's code) before it is
  quantized. Values the user reads are backend values.
- **Display resampling is frontend work.** deck.gl places the equirectangular
  image in Web Mercator per screen pixel, with optional value interpolation for
  model fields. Either display mode leaves readouts at the cell's own value.

## Not built

| Layer or feature | Data it needs | Seam |
|---|---|---|
| `WindBarbLayer` | Backend wind components at grid points (the station staffs draw with the surface plots) | Instanced GPU geometry, density by zoom and profile; no DOM per barb (REQUEST §12) |
| Radar grid mode | Decoded radar values, which `providers.radar_mosaic` does not produce | The model field's path: an `SHRF` frame of cells and a palette |
| `CountyLayer` | County boundaries (not bundled) | A style layer like the basemap, and a `boundaries` family |

State boundaries are drawn by the basemap style layers (`basemap-states`) and
again over the weather by `boundaries`, so REQUEST §5's `StateBoundaryLayer`
is not a separate module.

## GPU budgets and the frame window

- No texture cache: each panel holds the current field and radar textures,
  which deck.gl replaces when the frame changes. Field frames are cached on the
  CPU as cells plus a shared coloured image, both counted against the profile's
  CPU budget. A frame is coloured once per palette across panels and toggles;
  its image is released with the frame. Surface-plot glyph data is likewise
  reused while the observations are unchanged. Opacity and weather-layer
  toggles do not rebuild station labels or unchanged basemap properties.
- A field texture is 2048 by 1024, about native HRRR resolution and within
  the 2048 `MAX_TEXTURE_SIZE` every WebGL2 device supports. There is no
  level-of-detail representation.
- The render scale does not drop during drag, zoom, or animation yet; four
  panels use the four-panel scale (above). Each panel has its own WebGL
  contexts, so GPU geometry is not shared between panels.
- WebGL2 is the production API. Layer code stays device-agnostic through
  luma.gl, and only the probe touches WebGL directly, so WebGPU can arrive later
  without changing backend APIs.
- Measured with the real backend (`e2e/desktop/map.spec.ts`, run by hand): the
  first HRRR frame of a session in about 2 to 4 s, another product or hour in
  1 to 3 s, a prefetched hour at once, and four panels ready in about 3 s.
