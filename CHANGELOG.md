# Changelog

All notable changes to SHARPpy Reimagined are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-09-12

This release turns the sounding viewer into a broader analysis workspace while
making the release path faster and more exact. Forecast trends, aligned model
comparisons, ensemble distributions, locator overlays, storm reports, and
portable session state now work together; exports share one predictable home,
and the Python and Rust compute paths reuse substantially more work.

### Added

- **One analysis workspace for trends, exact-time comparisons, ensembles, and
  notes.** Open it from *View → Analysis Workspace* (`Ctrl+Shift+A`). Forecast
  timelines plot one parameter for every loaded sounding on shared axes, with
  explicit missing-hour gaps, and let a point activate that valid time in the
  sounding it belongs to; the plotted series export to CSV.
  Multi-sounding views compare MLCAPE, MLCIN, 0–6 km shear, 0–1 km SRH, and
  effective-layer STP against a selectable reference only when valid times
  align exactly, including value deltas and CSV export. Ensemble views report
  the number of available members, p10/median/p90 parameter distributions, and
  pressure-level temperature/dewpoint p10–p90 envelopes. The thermodynamic
  envelope uses a fixed -50 to +50 °C axis with a visible zero guide so one
  warm or cold member cannot tilt the visual scale.

- **Aligned model, run, and ensemble acquisition from the forecast picker.**
  *Workspace…* plans several models, successive runs, or all published members
  at one point and valid time, fetches them through the bounded persistent
  cache, and opens the corresponding Compare or Ensemble tab. Partial failures
  remain visible instead of discarding successful soundings.

- **Complete workspace state in portable analysis sessions.** Version 2 adds
  analysis notes and selected tab/metric/reference, visible panel, fit or exact
  zoom, picker map extent, all loaded times and overlays, and lightweight
  locator-overlay provenance that can be refetched without embedding raster or
  polygon payloads. Version 1 sessions still migrate on read.

- **Choose what the sounding's locator inset shows.** `sharpmod-render
  --locator-overlay` takes `risk[:hazard]`, `hrrr[:product]`, `radar-site`,
  `radar-mosaic`, or a comma-separated combination, and the picker offers the
  same choice in *Map overlays* on both map tabs. The rules live in one module,
  `sharpmod.locator_overlay`, so the command line and the interface cannot
  disagree about them:

  - A risk area and a model field coexist; radar replaces both, because two
    reflectivity ramps are not twice the information and radar over a risk area
    buries it.
  - Radar is offered only within an hour of the sounding's valid time. There is
    no archive behind those frames.
  - The nearest WSR-88D is preferred at 0.38 km/px against the mosaic's 1.51,
    but an extent wider than one antenna's box falls back to the mosaic.

  The risk area also carries its own hazard choice, beside the switch that turns
  it on. It defaults to *Match the map*, which follows whichever outlook the
  picker map is showing, so the inset stays context for the map the sounding came
  from without being told twice. Naming a hazard there instead pins the inset to
  it, and outranks the map — the same precedence `--locator-overlay risk:torn`
  already had, which the control previously had no way to express.

  Selecting nothing is the default and reaches for no network. The selection now
  gates what the sounding window fetches; the gridded field and the outlook were
  previously requested unconditionally. Only the *choice* persists between
  sessions, never whether it was switched on.

- **Storm reports overlay.** Tornado, wind and hail reports draw as ranked
  markers on the picker map (*Map overlays → Show storm reports*, both tabs) and
  on the sounding's inset. Clicking one gives the location, time, magnitude,
  source, remark and issuing office; where markers overlap the tornado stays on
  top and is what a click reports. The switch requires the convective outlook on
  the same map and turns itself off if the outlook does — reports with no risk
  areas behind them cannot answer whether the forecast verified.

  The map request is narrowed to the visible extent and re-asked once panning
  settles, debounced so scrubbing a date is one fetch, with late results
  discarded. An empty result and a failed feed each say so, since a blank map
  otherwise reads as a day on which nothing happened.

  Source is NWS Local Storm Reports via the Iowa Environmental Mesonet query
  service, not SPC's own file — that concatenates three CSV tables with the
  hazard implied by the header block, carries bare `HHMM` times over a 12Z day,
  reports hail in hundredths of an inch, and cannot be filtered. Reports reach
  back to at least 2015, so archived soundings get them; the outlook beside them
  only archives to 2020.

  Two service quirks are pinned by measurement. `type=` answers 200 OK with zero
  rows for every code, including ones present in the response's own `TYPECODE`
  column, so hazards are selected from the response instead; the bounding box
  does work, narrowing 190 reports to the 115 genuinely inside. And `sts` is
  accepted only with a trailing `Z`, so past windows use the numeric date
  fields. Responses are cached in memory and on disk like model fields, and live
  windows are never stored.

- **Click a coloured area on the picker map to identify it.** A wash of colour
  is not self-describing: a probability band reads `5%` without naming its
  hazard, and two products put the same red in the same place. A click now names
  the product, the category at that point, and the category's own description.
  The graded band is reported before the hatched qualifier drawn over it, so
  answering with the hatch alone cannot discard the probability underneath.
  Describing runs after station and radar-site hit-testing, so it never costs a
  selection, and a hidden overlay is never described.

### Fixed

- Official Windows freezes now resolve native dependencies from a hermetic
  Python/Windows DLL search path. Unrelated tools on the build host can no
  longer inject a same-named ICU or C-runtime library that lets PyInstaller
  finish but makes the packaged GUI fail while importing `PySide6.QtCore`.

- **Exports now have one predictable application-local home.** Every image,
  sounding-text, CSV, GeoJSON, saved-location, and analysis-session save dialog
  starts in the existing `rendered_soundings` directory beside the source
  project or installed application; the upstream save actions and **Open Export
  Folder** use the same path. Stale Desktop or other user-folder settings are
  ignored and removed, one-off destinations are not remembered, and launches
  from another working directory still resolve the application location. The
  directory is created when needed, while creation or write failures report the
  actual path instead of silently falling back to a user folder. The
  `sharpmod-render`, extractor sounding pairs, and extractor `--render`
  defaults follow the same rule and still honor an explicit output path.

- **The sounding's locator inset drew the categorical outlook whichever hazard
  was selected.** Opening a sounding while looking at the tornado, wind, or hail
  probability — for Day 1 or Day 2, where SPC issues them — silently reverted the
  inset to the categorical risk. The selected hazard was resolved and then
  dropped: it was only read on a code path the interface never takes, because a
  picker offering a locator selection always states one, and the reconciliation
  meant to merge the two was unreachable. The hazard now travels with the
  selection from every entry point — sounding window, model comparison, reopened
  session, and `sharpmod-render`.

  A hazard SPC does not publish for the sounding's own outlook day still falls
  back to the categorical outlook rather than leaving the inset empty. That is
  asked as a general question of the outlook archive rather than encoded as a
  rule here, since which product covers which day is not fixed: the hazard
  probabilities stop after Day 2, the combined total-severe probability is Day 3
  only, and nothing is published beyond Day 3 or before 2020.

- **A forecast hour streamed after an edit no longer disappears on undo.**
  Timeline insertion now preserves modification and interpolation state by
  valid time, while history snapshots merge later additive hours before
  undo/redo. The live collection object and selected valid time are retained,
  so toolbar callbacks do not point at an obsolete pre-undo collection.

- **Closing the picker no longer force-terminates Qt workers.** Cooperative
  workers receive bounded cancellation and can finish under a retained owner;
  long multi-hour/model/box batches run behind a killable child-process
  boundary. Cancellation reaps that child promptly, while validated manifests
  and cache hits remain resumable.

- **The HRRR domain was outlined as a box, so the outline and the data were
  never the same shape.** On the flat map the model field drew as a curved fan
  inside a straight dashed rectangle; switch to the curved map and the field
  straightened while the rectangle bowed. The two swapping places between
  projections reads as though the projections themselves were swapped.

  They were not. Both renders were correct, and measurement says so: the field
  raster tracks the vector path that draws the coastlines and graticule to
  within 0.4 px in either projection. HRRR runs on a Lambert conformal grid,
  which is a rectangle in *its own* projection plane, so its geographic boundary
  genuinely is a fan on a flat map and genuinely does straighten on a conic one.
  The outline was the part that was wrong: it traced `(-130, -60, 20, 55)`, a
  hand-rounded longitude/latitude box that is not the grid and did not even
  agree with the field's own coverage bounds.

  HRRR now publishes its real perimeter, walked around the grid edge in Lambert
  metres and converted back, so the dashed line follows the data to within 2 px
  in both projections. The envelope is measured off that same perimeter rather
  than rounded — `(-134.10, -60.92, 21.14, 52.62)`, which differs from the old
  box by 4.1 degrees of longitude at the west edge and 2.4 of latitude at the
  north — and a point is now accepted or refused on the grid itself. That
  matters beyond drawing: the southern boundary reaches 24.36N over Kansas and
  only 21.14N at its own corners, so a box either claims ground the model does
  not carry or, tightened to stop that, refuses ground it does.

  The transform is a new pure-Python module rather than a call into pyproj,
  because the picker's import path is kept free of NumPy so the interface opens
  promptly. It is checked against pyproj at the grid corners to 1e-13 degrees,
  and its south-west corner reproduces the `21.138123N 237.280472E` that HRRR's
  own GRIB header publishes as its first grid point. The other CONUS models
  still fall back to the shared box; each runs on a different grid and needs its
  own.

- **The scrollbar handle sat on the content it was scrolling.** It filled the
  whole 12 px column a control rail reserves, butting against the card border
  beside it. The handle is now inset inside that reservation, so it gains a
  gutter without the content losing a pixel. Padding on the bar is the only
  spelling Qt applies — a margin or a transparent border on the handle both
  leave it at full width — so the regression test measures the rect the style
  paints rather than checking the declaration is present. The bar's width also
  interpolates `SCROLLBAR_W` directly instead of a token that happened to match
  it, and the handle's minimum length is declared per orientation rather than
  putting a 24 px floor across each bar's short axis.

- **Map overlays and coastlines were soft on any scaled display.** The gridded
  field, radar frame and basemap linework were rasterised in *logical* pixels
  and then stretched onto a backing store larger by the device pixel ratio; on a
  1.5x display a 1200x800 map drew its overlays at 1200x800 and enlarged them to
  1800x1200. Buffers now match the screen's real pixels and rebuild on a move to
  a different density.

  The same arithmetic inverted the rule that keeps published data cells crisp.
  It compared a logical destination against a source measured in image pixels,
  understating the destination by exactly the device pixel ratio, so it smoothed
  imagery it was in fact enlarging — inventing intermediate categories along
  every class boundary of a banded scale.

- **A dewpoint above the temperature no longer refuses the sounding.** The
  portable `.npz` loader and the Open-Meteo, ECCC GeoMet, ERA5 and model
  extraction paths all raised on it. It is a real reading, usually meaning
  contamination and turning up most often near the tropopause, so refusing the
  profile hid the problem instead of showing it. It now travels in the
  quality-control issue list and the sounding renders. Physically impossible
  data is still refused: a pressure column that does not decrease, or a negative
  wind speed, is unusable rather than contaminated.

- **The property lane ignored the grouping its own tests asked for.** Files
  sharing process-global Qt state are marked to stay on one worker, and three
  carry property tests, but the lane distributed individual tests — honouring
  neither the marker nor file cohesion. It now groups as intended, ungrouped
  tests still spread, and a contract test refuses any parallel lane that would
  scatter tests marked to stay together.

- **The release gate's timing budget could never fail.** The serial pass was
  allowed 4200 s in a job destroyed at 45 minutes, and its stated baseline of
  3000 s already exceeded that. Because the runner tears the step down no timing
  report is written either, so an overrun was indistinguishable from an
  infrastructure failure. The budget now fits inside the job with room for
  setup, and a contract test asserts every lane's budget can still fail before
  its job is killed.

### Changed

- **Sounding calculations, repeated GRIB points, and complete-profile analysis
  now reuse the work they actually share.** Parcel integration has distinct
  CAPE/CIN-only, diagnostics-only, and traced modes; kinematics prepares each
  interpolation series once; and the API-7 native boundary returns contiguous
  trace buffers with offsets while preserving the public tuple contract. One
  owned profile snapshot now feeds parcel and DCAPE preparation instead of
  being copied repeatedly.

  Direct GRIB decoding keeps an eight-entry, file-identity-invalidated message
  inventory and opens each selected message once for a native multipoint read.
  Atomic same-size replacements invalidate the cache, duplicate requests and
  mixed grids retain their order and semantics, model-cache leases are
  unchanged, and ecCodes remains serialized.

  Box fast-tier and built-in ensemble analysis now use a stable-order complete
  sounding batch. It stays serial below eight profiles, otherwise defaults to a
  process-wide Rayon pool capped at four available CPUs, and stays serial when
  nested in Rayon. Simultaneous default GUI callers share the same pool instead
  of multiplying its thread bound. Release measurements support that cutoff;
  the dated backend
  optimization report records the raw improvements, regressions, environment,
  and reproducible commands. Thin LTO and the release profile are unchanged.

- **The workspace's parameter trends plot every loaded sounding at once, on one
  graph.** The tab used to carry a sounding picker and draw one model at a time,
  so comparing runs meant switching between them and remembering the previous
  shape. It now draws one line per sounding on shared axes, with a colour per
  sounding named in a legend, so the disagreement between runs over the same
  hours is a single read. The sounding picker is gone, since the chart no longer
  shows a subset.

  Points carry the sounding they came from: hovering names it, and activating one
  opens that valid time in *its* sounding rather than in whichever was selected.
  A missing hour is marked in the colour of the series that is missing it. CSV
  export gains a leading `sounding` column when more than one is plotted, and
  keeps the previous columns when there is only one. The session records which
  sounding was last opened from the chart instead of which one was being filtered
  to.

- Renderer monkeypatches now have explicit panel ownership and a checked,
  ordered registry; the largest hodograph, winter, and fire implementations
  live in focused modules. Forecast timeline, comparison, locator, shutdown,
  and isolated-batch orchestration likewise live outside the picker window.
  Installer failures name the patch that failed instead of being swallowed.

- Analysis work uses a bounded shared worker pool and tiered metric cache, NPZ
  decoding leaves unrequested arrays lazy, and test lanes retain file/group
  cache locality. On the verified Windows environment the deterministic lane
  finishes in 66.4 s and full 100-example property coverage in 99.8 s; timing
  budgets remain enforced in CI.

- **The heavy test lanes now build the accelerated backend that ships.** Hosted
  runners tested only the pure-Python fallback, roughly 6.7x slower on the
  parcel and kinematics kernels, which made the complete serial pass 35 minutes
  of a 45-minute run. The deterministic, property and serial lanes install the
  Rust extension; the compatibility matrix stays on the fallback so that path
  keeps whole-suite coverage on Python 3.11 and 3.12, and the property contract
  runs once per backend. Worth noting: the backend-equivalence checks skip
  themselves when the extension is absent, so until now they had never run on a
  hosted runner at all.

- **The Windows release build no longer queues behind the test matrix.** It took
  8m34s while waiting on a 35-minute gate it does not depend on. Publishing
  still requires every lane to pass, so nothing unverified ships; the only cost
  is a discarded build when a test run fails.

- **The overlay-control tests spent 67 of their 73 seconds asleep.** They waited
  out the real debounce windows 102 times over. The controllers are driven
  directly under test, so the window is shortened there and the waits are
  expressed against it: 18 s, with the coalescing behaviour still proven.

## [1.1.0] - 2026-09-06

This cycle is about the step *before* the sounding. Previous releases sharpened
the Skew-T; this one builds the mesoanalysis around it, so you can read the
environment on the map, decide where the story is, and only then pull a profile.
Gridded HRRR fields, single-site radar, and whole-area sampling all serve that,
and the maps were rebuilt to carry them.

### Added

- **Mesoanalysis fields on the picker maps (HRRR).** *Map overlays → Show HRRR
  model field* paints any of 23 HRRR products across the map at the model's native
  3 km. The list is ordered the way a forecaster works down the scales:

  - **Pattern** — geopotential height and wind at 200, 300, 500, 700, and 850 mb
  - **Surface** — 2 m temperature and 2 m dewpoint
  - **Convection** — composite reflectivity, run-maximum 0–3 km updraft helicity
  - **Instability** — SB/ML/MU CAPE, ML CIN, 0–3 km and 700–500 mb lapse rates
  - **Kinematics** — 0–1 and 0–6 km bulk shear, 0–1 and 0–3 km storm-relative
    helicity
  - **Composites** — 0–1 and 0–3 km energy helicity index, supercell composite,
    significant tornado parameter

  One field shows at a time, so a second colour ramp never stacks over the first.
  The SPC convective outlook and the radar frame keep their own layers, so a risk
  area, an echo, and a field can be read together — with the field drawn *beneath*
  radar, because the field is the airmass and the echo is the storm sitting inside
  it, and a storm is far too small to survive being washed over by a
  continent-wide ramp. A colour bar comes with the field: a continuous quantity
  cannot be read off a row of categorical swatches.

  **Two things to know before you trust a number.** SCP and STP as SPC publishes
  them are built from *effective inflow layer* ingredients, which gridded 2-D
  output cannot supply — these use the fixed-layer forms HRRR's own fields
  support, and each says so in the panel and in its tooltip rather than leaving
  the difference unstated. And the updraft helicity is the **maximum over the
  forecast hour**, not a snapshot, which is why a track shows as a swath.

  Every product resolves to records HRRR actually publishes, checked against a
  live inventory, and only the records a product needs are transferred —
  composite reflectivity is 257 KiB out of a 135 MiB file. A field costs 0.3–4.8 s
  to fetch and about 0.2 s to draw, and panning with a field and radar attached
  runs 8.5 ms a frame.

- **The field you were reading follows the sounding.** Open a profile while a
  gridded product is on the picker map and that same field is now drawn under the
  locator thumbnail in the sounding window, cropped to the ground around the
  point. The inset previously showed county and state outlines only, so the
  environment you had just been looking at on the map vanished the moment you
  asked for the sounding it belonged to — and reading a supercell composite maximum
  and then a profile from inside it is one act, not two.

  It is   the *same* image, not a second request: the picker already fetched it to
  draw the map, so the sounding window is handed the frame that is in memory
  rather than spending another GRIB subset. The field is matched to the hour the
  profile depicts and withheld otherwise, because a field an hour away is a
  different forecast; and it is drawn beneath the risk areas and the boundary
  lines for the same reason it is on the map — the field is the airmass, the
  outlook is a judgement about it, and the geography has to stay readable through
  both.

  **The thumbnail says which field it is showing.** A chip in the lower-right
  corner names the product: `STP`, `0-3 km SRH`, `500 mb Wind`, `700-500 LR`. It
  needs one, because without it the inset is a wash of colour — a significant
  tornado parameter and a CAPE field are the same reds in the same places, and the
  picker's colour bar and legend are in a different window. The lower-*left* corner
  keeps the outlook category chip, so a risk area and a field can be named at the
  same time. Long names shorten rather than run over their neighbour, and each
  product's chip is spelled the way a forecaster writes it rather than the way the
  catalogue keys it — the height-and-wind products name the *wind*, since the wind
  is what the colours are and the height is contour lines.

- **You can choose which radar you are looking at.** The single-site scope grew
  an antenna list. It still defaults to the one nearest the map centre, which
  answers *what is happening where I am looking*, but that is not the only
  question: interrogating a storm means watching one radar, by name, and staying
  on it. Picking `KTLX` now gets KTLX whether or not a closer antenna exists and
  whether or not the map has been panned away from it.

  **Every antenna is on the map.** While a single site is showing, all 155
  WSR-88Ds are drawn as markers you can click — the same way you pick a sounding
  station — because pointing at the radar you want is a more direct answer than
  finding its identifier in a list. They are cyan diamonds rather than the
  stations' red circles, since on the station map both networks draw at once and
  one shape in one colour would read as one network. Identifiers appear as the
  view closes in, and the hovered and chosen antennas are always named. The
  markers come and go with the overlay: they are cleared for the national mosaic
  and whenever radar is switched off, so they never outlive the reason to show
  them.

  Where a radar shares a mast with a sounding site — Norman with KTLX, Dodge City
  with KDDC — the two markers land within a few pixels of each other, so a click
  resolves to whichever is *nearer* the cursor. Giving the radar an unconditional
  claim would have made those stations unselectable for as long as the layer was
  showing, and on the forecast-model map it would have moved the profile location
  at the same time as retargeting the radar: two answers to one gesture.

  The list itself is type-to-find, because four letters is how a radar is
  addressed and 155 identifiers is too many to scan. Each carries its antenna's
  coordinates, since the catalogue has no place names to sort by. A named radar is
  also filtered to the products it actually publishes — offering velocity from a
  site that does not carry it would fail with nothing the reader could act on —
  and if the frame lands outside the current view the panel says so, because an
  overlay that draws nothing off-screen otherwise looks broken rather than
  distant. The choice is remembered between sessions.

- **Area soundings: sample an airmass, not a point.** Shift-drag a rectangle on
  the Forecast Model map (or arm **Box…** and drag) and every model grid point
  inside it is extracted from a *single* download. By default the points are
  averaged into one composite profile that opens in the ordinary analysis window —
  same Skew-T, same hodograph, same parcel logic, same outlook overlay — because
  "what does the airmass over this area look like" is a question a sounding
  answers, not a dashboard.

  Averaging soundings is easy to do wrong, so four things are deliberate. Winds
  average as *u*/*v* components, never as speed and direction, because the mean of
  350° and 10° is 0°, not 180°. Moisture averages as **mixing ratio** and converts
  back, because dewpoint is nonlinear in vapour pressure and averaging it biases
  the column dry. The averaged dewpoint is then clamped to the averaged
  temperature and the clamps are counted, because saturation mixing ratio is
  convex in temperature, so several subsaturated points can average to implied
  saturation. And only the layer every point shares is averaged: terrain varies
  across a box, so the mean starts at the **highest ground in it** rather than
  blending a real column against nothing, with the levels dropped at each end
  reported. A box straddling too much relief to share two levels is refused rather
  than fudged.

  **The one caveat that cannot be engineered away** is stated in the dialog, the
  docs, and the file's own metadata: the indices of the mean sounding are *not*
  the mean of the individual points' indices. CAPE of the average column is not
  average CAPE. A mean describes the airmass; the field map below is what answers
  a question about the extreme.

  Every sampled point is a complete sounding — true surface pressure, terrain
  height, 2 m and 10 m values, below-ground levels removed — so no point is a
  pressure ladder standing on an invented ground row. Sample spacing is rounded
  **up** to a whole multiple of the product's published grid spacing, so two
  soundings can never come from one grid cell and no gradient is drawn between two
  copies of the same number. Points outside the model domain stay in the lattice
  and are left blank, so a partly-covered box shows real holes.

- **The parameter field: where inside the box something peaks.** One radio button
  away from the mean, the same sample becomes a field map over the real basemap,
  with area statistics that say *where* the significant extreme is and a ranked
  list of the twelve most significant grid points. Any cell opens as a full
  Skew-T. Ranking follows the parameter rather than the arithmetic, because the
  significant end of a field is not always the large end: CAPE and shear rank
  downward while CIN, LCL, and LFC rank upward.

  `BoxAnalysis.envelope()` reduces the area to a per-level band — minimum, 10th to
  90th percentile, median, maximum — for temperature or any raw column. A narrow
  ribbon is one airmass; a band that fans out at low levels is a **boundary inside
  the box**, which a single sounding from the middle cannot tell you. Levels below
  a point's own terrain do not contribute, so the sounding count thins toward the
  ground instead of the band quietly widening where there is no data.

  Instability, parcel-height, and kinematic fields resolve at about 2.3 ms a
  point, so a full 256-point box lands in under a second. The SPC composites need
  the upstream `ConvectiveProfile` at about 424 ms a point and are opt-in behind
  **Add SPC composites**, with the prompt quoting the wait. Both read the same
  cached parcel oracle the Skew-T does, so a field and a sounding opened from that
  cell cannot disagree.

- **Ingredient screens: where several thresholds hold at once.** One field answers
  "where is CAPE largest". Ingredients-based forecasting asks "where are all of
  these true together", and the **Ingredients** picker hatches exactly those grid
  points and reports the ground they cover — `34 of 90 points (38%), about
  59,160 km²` — in the same visual grammar as the SPC outlook, so the hatch
  qualifies the field underneath instead of hiding it.

  Seven screens ship: surface-based storms, organized convection, supercell,
  tornado ingredients, large hail, damaging wind, and elevated convection. They
  are **screening heuristics for narrowing attention — not official products and
  not a forecast.** Thresholds are deliberately permissive so a screen cannot hide
  a marginal signal, and all of them use fast-tier fields so a screen never forces
  the expensive tier. Custom thresholds are a `Criterion` away in the Python API.

  Thresholds are tri-state on purpose. A point missing one of the fields a screen
  needs is left **blank rather than shaded unfavourable**, and those points are
  counted, because "unread" and "ruled out" are different claims and an unread
  point must not be drawn as a verdict. A definite failure still outranks a
  missing field, so an incomplete ingredient cannot rescue a point that already
  failed another. Coverage divides by the points actually evaluated, so a box on
  the domain edge reports the fraction of the area it could read.

- **The same area through forecast time.** Tick **Step through forecast hours** and
  the box is sampled at up to twelve hours, with a slider, step buttons, looping
  playback, and **Jump to peak** — the most significant hour, or the hour with the
  largest qualifying area when a screen is active. A field can be watched building
  and decaying instead of being inferred from two static hours. Each hour is its
  own download, so the dialog states the hour count, the sounding count, and the
  transfer count before anything is fetched. The field list is the intersection
  across hours, so a selection cannot vanish as the slider moves.

- **Exporting an area.** **Export** writes the field map as a PNG exactly as drawn
  (legend and hatch included), every point's values as CSV, or the sampled cells
  as GeoJSON for GIS. The GeoJSON features are the **cells as polygons**, not bare
  markers, because what a model asserts is a value over an area of one grid
  spacing; antimeridian cells split per RFC 7946. A missing value is an empty CSV
  field and an explicit `null` in GeoJSON — never `0`, never `-9999`. A multi-hour
  box exports every hour into one file with an `fxx` column.

- **`box-extract` command.** The scriptable form of the same feature:
  `--dry-run` to price a box before fetching it, `--target-points` or
  `--spacing-km` for density, `--composites`, `--field` to print a field as a
  grid, `--csv`, `--geojson`, and `--list-fields`. `--screen` reports ingredient
  coverage and `--list-screens` prints every screen's thresholds; `--hours` and
  `--hour-step` walk forecast time using the model's own published cadence, so an
  unpublished hour is an argument error rather than a failed download. Unwrapped
  longitudes describe an antimeridian box, so `50 170 56 190` is 20 degrees wide,
  not 340.

- **A flat or curved map view.** **View → Map Projection** switches every map tab
  between the equirectangular view and a Lambert conformal conic whose standard
  parallels sit a sixth of the way in from the top and bottom of the extent.
  Parallels bow and meridians converge — the shape a regional forecast map
  normally has. The setting is remembered between sessions.

  Flat stays the default because it is the only view that stays correct at every
  extent the picker offers. A cone cannot represent an extent taller than 75° of
  latitude or one centred within 4° of the equator, so those views fall back to
  flat rather than draw a distorted cone. Every layer — outlines, imagery,
  markers, boxes, graticule labels, panning, and click-to-select — goes through
  one transform, so no layer can disagree with another about where a place is.

  **The cone follows the map.** A conic is cut along one meridian, and screen-up
  points at true north only there. So the cone is refitted whenever the view comes
  to rest, and the settled map always sits square to north. It has to be: the cone
  was previously fitted once when a region was picked and then held through every
  pan, which meant the map leaned further from north the further you travelled and
  never straightened up — 12° of lean after panning 20° west, 17° by the time you
  reached the west coast. A map that leans seventeen degrees is not one anybody
  asked for. During a drag the cone is still held, which is what keeps panning
  smooth rather than re-projecting the whole basemap on every mouse move; the
  refit lands when you let go, so a long pan visibly straightens as it settles.

- **Lake shorelines on every map.** The Great Lakes, Great Salt Lake, and other
  inland water were missing their outlines, which matters for a lake-effect or
  lake-breeze setup where the shore *is* the boundary you are looking for. The
  cause was not a bad source: `ne_50m_coastline` is strictly the ocean/land
  boundary and holds no inland shoreline at all, so lakes now ship as their own
  Natural Earth 1:50m layer. They draw in the coastline colour, slightly finer and
  just beneath it, because a lake shore *is* a coastline and a separate hue would
  imply a distinction that does not exist. Rings under 0.05 square degrees are
  dropped as specks; islands within a lake are kept.

- **Model grid spacing is published.** `ModelConfig.grid_spacing_km` and
  `model_extract.grid_spacing_km()` expose each product's nominal horizontal
  resolution — what lets area sampling refuse to invent detail the model does not
  have, and what now sizes every server-side subset request.

- **Every sounding panel is listed by name.** **View → Sounding Panel** in the
  sounding window offers all six swappable panels: sig-tor stats, the two
  EF-scale probability panels, sig-hail stats, **fire weather**, and **winter
  weather**. Each says what it is for, because the titles are abbreviations and
  two of them describe the same hazard from different angles.

  The fire and winter panels were already there, and already computed for every
  sounding whether or not anyone looked at them — but the only way to reach them
  was a right-click on one specific box, and not by design: the layout detaches
  the left-hand panel to make room for the index board, so the hit test can only
  land on the right-hand one. Nothing named the gesture, so two working panels
  read as absent features. The right-click still works; this adds a route you can
  find.

  Choosing a panel still carries what belongs with it. Fire weather marks the
  mixing height on the Skew-T and winter weather marks the dendritic growth zone,
  and switching away takes the marker with it rather than accumulating both. That
  pairing is the reason there is no separate "fire mode" or "winter mode": the
  mode would be a second name for something the panel choice already does.

- **The freezing level and wet-bulb zero are always on the Skew-T.** Both are
  labelled with their height, alongside the dendritic growth zone marked on the
  temperature trace — whichever panel happens to be showing.

  They used to trade places with other annotations. The Skew-T had a single
  either/or: with the winter panel selected you got the freezing level, the
  wet-bulb zero and the growth zone but *lost* the maximum lapse-rate layer and
  the parcel's 0, −20 and −30 °C levels; with any other panel you got those back
  and lost the first three. So picking a panel quietly changed which
  thermodynamic levels the Skew-T was willing to label, which is not something
  the physics has an opinion about — the freezing level is read for hail melting,
  precipitation type and icing no matter what else is on screen, and the wet-bulb
  zero is a hail-size predictor in its own right.

  Now all of it is drawn every time. Nothing was given up to do it: the two label
  families sit on opposite sides of their shared tick column, so they coexist
  without colliding. The freezing level and wet-bulb zero also now appear on
  soundings with no growth zone at all, which the old branch skipped entirely.

- **Ventilation rate on the fire panel.** Mixing height multiplied by transport
  wind, in m²/s — the number a prescribed burn or a smoke advisory turns on. Both
  factors were already computed and printed separately, and neither alone answers
  the question: a deep mixed layer with no wind ventilates as badly as a windy
  shallow one.

  It is built from the same two values printed two rows above it, so the three
  cannot disagree. No category is attached — poor, fair and good are set by
  whichever agency issues the forecast and differ between them, so the number is
  reported and you apply your own thresholds. It reads `M` when either factor is
  unavailable rather than showing a product of placeholders.

- **A snow-to-liquid ratio on the winter panel.** Neither SHARPpy nor this fork
  computed one anywhere, so the panel could describe the dendritic growth zone
  five different ways — depth, mean RH, mean precipitable water, mean mixing
  ratio, mean omega — without ever answering the question a snow forecast turns
  on: how much snow an inch of liquid will make. The fixed 10:1 rule of thumb
  that gap left people with is right about a quarter of the time.

  It uses **Kuchera's** method, and the row says so, because snow-ratio methods
  disagree widely and a bare number would not tell you which one you were
  reading. Kuchera keys the ratio to the *warmest* temperature in the column
  rather than to the surface, since it is the warmest layer the crystals fall
  through that decides whether they stay dendritic or rime and compact. Two
  straight lines meet at 12:1 at −2 °C: colder columns gain ratio one-for-one,
  warmer ones lose it twice as fast, which is why a column only a few degrees
  above freezing collapses to no accumulation at all.

  Zero and unavailable are kept distinct. A column whose warmest layer reaches
  about +4 °C accumulates nothing, and that is an answer, so it reads `0:1`; a
  profile the method cannot be applied to reads `M`. The warm branch runs
  negative without a floor, which is not a ratio, so it is clamped.

- **The growth zone is given in pressure as well as feet.** It was only ever
  reported as a depth in feet with its bounds in feet MSL, but the Skew-T's own
  axis is pressure and the band is drawn against it, so reading one off the
  other meant converting in your head. Both are now shown; nothing was traded
  away for the new row.

- **Observed soundings can come from NOAA's IGRA v2 archive.** The picker's
  *Sounding source* control is real now — it was a disabled label naming one
  fixed fallback chain — and it offers four choices: the established University
  of Wyoming → IEM fallback, either of those alone, and NOAA's **Integrated
  Global Radiosonde Archive**. The choice is remembered between sessions.

  IGRA is why this was worth doing: about 2,900 stations against the 933 in the
  bundled Wyoming catalogue, quality assured, and a record that at some sites
  reaches back over a century. If you want the sounding from a historic event,
  this is the source that has it.

  **It is chosen deliberately and never reached by fallback.** IGRA publishes one
  archive per *station*, not per sounding: 2.4 MB for the current year and
  **80 MB** for a long station's full record. Putting that in the automatic chain
  would mean an ordinary request quietly pulling tens of megabytes because the
  other two archives happened to be down. So the automatic setting still tries
  only UWyo then IEM, and a named source is never answered by a different
  archive.

  Each station archive is cached, so the first sounding for a station pays the
  download and the rest do not — a repeat fetch went from 3.6 s to 0.9 s. The
  smallest archive that can cover your date is used, so an ordinary recent
  request never touches the full record, and a date outside the station's
  published years is refused before anything is downloaded at all. The
  availability dot stays on the cheap archive too: for a date that would need the
  full record it reads *Not checked* rather than claiming there is no sounding.

  **The station map still works unchanged.** IGRA numbers the station the map
  calls `72357` as `USM00072357` — the third character is a network code, and for
  the WMO network the trailing five characters *are* the WMO number — so the
  identifier you already click resolves. Requests prefer an exact nominal hour but
  tolerate three, because IGRA carries special releases at 16–21Z and asking for
  18Z should find a 19Z ascent.

  Where the archive reports relative humidity but no dewpoint depression, common
  in the older record, the dewpoint is recovered from temperature and humidity and
  the number of levels that needed it is recorded with the sounding, so the
  substitution is never silent. Levels identified only by height carry no pressure
  and cannot be placed on a profile, so they are dropped and counted rather than
  guessed at. Checked against Wyoming for the same ascent, the two independent
  archives agree to **0.2 °C** at every mandatory level.

  On the command line: `observed-sounding fetch 72357 "2026-09-01 12:00"
  --provider igra2`. `observed-sounding providers` now also says which sources the
  automatic chain uses and which are select-by-name.

- **`StationMapWidget.set_extent()`** frames an arbitrary lon/lat extent,
  complementing `set_area()`'s named regions, and unrolls a wrapped extent.

### Changed

- **Radar is a single site now, and it follows the map.** The radar switch grew a
  scope — **Nearest single site** or **CONUS mosaic** — and defaults to the site.
  The mosaic spreads 70° of longitude over 4096 pixels, about 1.9 km each: the
  right trade for *where is the convection today*, and no use at all for *what is
  this storm doing*, because by then the echo is a handful of pixels. One radar
  covers ten degrees instead of seventy, so the same pixel budget lands near half
  a kilometre and structure inside a storm survives — a hook, an inflow notch, a
  bounded weak echo region. Base reflectivity, radial velocity, hydrometeor
  classification, and storm-total and one-hour accumulation are available per
  site.

  Which radar is not configured, it is derived: the antenna nearest the map
  centre, re-resolved as you pan, by **great-circle** distance rather than raw
  coordinate difference — a degree of longitude is 111 km at the equator and 57 km
  at 60°N, and comparing degrees picks the wrong Alaskan radar. A view with no
  antenna within about 460 km says so instead of quietly fetching a frame centred
  hundreds of kilometres away.

  The 155-site catalogue is built by asking each WSR-88D for its own capabilities
  (`python -m sharpmod.tools.build_radar_sites`), because NCEP's global
  capabilities document does not list the per-site workspaces. Each antenna
  position is read from its layer's own advertised bounding box and was checked
  against published coordinates for nine sites from Puerto Rico to Seattle,
  agreeing to about a kilometre.

- **Radar and model fields now draw on the curved map.** They used to be withheld
  there. The reason was sound — a conic has no rectangle to blit a flat image
  into, and an echo drawn a hundred kilometres from the storm is worse than no
  echo — but the conclusion was avoidable.

  A conic is smooth, so over a small enough patch it is indistinguishable from a
  scale, rotation, and shear. The window is cut into roughly two-degree cells,
  each cell's corners are projected exactly, and each is drawn through the affine
  transform carrying its source rectangle onto the resulting quadrilateral.
  Against projecting every pixel, the worst residual inside a cell stays under one
  pixel, and a test holds it there — so an echo lands on the storm. Dragging with
  a field and a radar frame attached measures 8.5 ms a frame curved against 8.3 ms
  flat.

- **An area opens as a sounding by default.** Drawing a box previously went
  straight to the parameter-field workspace. It now opens the averaged sounding,
  with the field map one radio button away, because the first question a box asks
  is about the airmass.

- **A box mean says so, everywhere it is shown.** An average that looks exactly
  like a point sounding is the one outcome worth engineering against — every
  parcel, index, and hodograph on the page belongs to an averaged column. The
  Skew-T title reads `HRRR box mean of 49`, the window title agrees, and the
  locator inset draws the sampled rectangle instead of a marker over a spot that
  was never sampled on its own. The inset widens until the whole rectangle fits,
  the one sanctioned exception to its fixed two-degree extent. The plain product
  name is kept alongside the decorated one so provenance stays machine-readable.

- **A box's forecast hour is choosable in the dialog.** It still opens on the hour
  selected in the sidebar, so a box follows the run already on screen, but the
  hour is now a picker listing every hour the product publishes — changing your
  mind no longer means cancelling, changing the sidebar, and drawing the rectangle
  again. A mean is one hour by definition; in the field workspace the optional
  hour sequence starts from whichever hour was picked, and the offer withdraws
  itself on the last published hour.

- **Image overlays have an explicit stacking order.** They used to paint in
  whatever order you happened to switch them on, which is not an order at all.

- **The two open Dependabot bumps are included here:** ruff 0.16.5 from
  [#52](https://github.com/ShianMike/SHARPpy-Reimagined/pull/52) and
  `softprops/action-gh-release` 3.0.3 from
  [#51](https://github.com/ShianMike/SHARPpy-Reimagined/pull/51). Both of CI's
  ruff invocations are clean on 0.16.5, including the focused
  `E,F,I,UP,B,SIM` pass, which is the one a version bump can actually break.
  The action stays pinned by commit SHA rather than by tag — `efb35369` —
  because a tag can be moved and a commit cannot.

### Fixed

- **The effective inflow layer drew its base label through its own top label.**
  The base label already moves *above* its line when the layer is surface based,
  because below it the label falls off the bottom of the plot. When the layer is
  also shallow, that flip puts it on the same line as the top label and in the
  same −33 °C column, so a layer of zero depth printed `SFC` straight over `0m`.
  A five-hPa-deep layer did the same over `45m`. The base label now steps
  sideways along its own line; the top bound and the helicity keep their column,
  since the base is the one label that already had somewhere to go.

  Those three rects were also being sized to 25 and 50 pixel floors inherited
  from upstream. The floors did nothing — the text is left-aligned with
  `TextDontClip`, so a rect's width never affected where a glyph landed — but
  they made every collision test up to 30 px pessimistic, which would have
  spread the labels much further apart than the ink needs. They are sized to
  their text now.

- **The maximum lapse rate is out of the crowded side of the Skew-T.** Its value
  was anchored five degrees warm of the temperature trace, which is where the
  parcel levels, the freezing level and wet-bulb zero, the significant-level
  ticks and the wind barbs all already are — measured at x 595–651 with the
  parcel-level labels occupying 662–746, so it collided by construction rather
  than by bad luck.

  The whole annotation now sits on the cold side, the way the effective inflow
  layer already did: a bracket in its own column with the value beside it. The
  bracket keeps the two pressures it marks, which is the part that carries
  meaning; only the column changed.

  **It gets a column of its own, right of the inflow annotation.** Sharing the
  left gutter with the inflow height labels does not work: those sit at whatever
  height the inflow layer happens to be, so they can come arbitrarily close to
  this one — near enough to read as a single smear without ever strictly
  overlapping, which no overlap test catches. Right of them there is real room,
  measured at 104 to 310 px across panel sizes against the 67 to 85 px the label
  needs.

  **The room is measured per sounding, not assumed.** It ends at whichever trace
  comes first, and a dry profile puts its dewpoint far further left than a
  saturated one, so the temperature and dewpoint are sampled across the layer's
  whole depth — the bracket spans it, not just its top. If that column will not
  fit, the annotation falls back to the gutter and steps sideways past the inflow
  labels there instead. It always slides along its own line rather than up or
  down, because the height is the pressure the number belongs to while the column
  is arbitrary.

  The inflow labels are held clear of the omega meter for the same reason, which
  matters on a narrow plot where their fixed −33 °C column can fall inside it.

  The meter's footprint is its *drawn* extent, not its nominal −49 to −41 °C
  bounds. It scales each bar by the reported value rather than clipping at the
  scale, so strong ascent is drawn well past the bound, and its `+10`/`−10`
  labels overhang both ends. A warm-tier lapse rate is red and so are the ascent
  bars, so had the nominal bounds been trusted the bracket would have landed
  among them and read as one of them.

  The height markers count as an obstacle too. The `0 km` through `15 km` labels
  down the left edge are drawn on *every* sounding, not just when there is a
  meter, and together they form a full-height column about 65 px wide. An
  observed sounding carries no vertical velocity and so has no meter, and with
  only the meter accounted for the value was placed against the left border and
  straight through those labels — visible on any radiosonde profile. The gutter
  now clears whichever of the two reaches further, which leaves the forecast case
  where it was and moves the observed one from 6 px inside the border to 71.

- **The winter panel drew its last rows below its own frame.** Row height came
  from font metrics with no reference to the height available, so the slot
  positions were byte-for-byte identical at every panel size. Measured on a real
  profile at 200×260, three rows were drawn past the bottom edge — the
  precipitation type itself among them, which is the one thing on that panel
  nobody can afford to lose. This is the same fault the fire panel had; the
  winter panel now sizes its rows by fitting them against its real frame walk
  rather than estimating.

  A second, quieter mismatch came out of adding a row to the growth-zone block:
  the frame reserved `row height + gap` per row while the row loops advanced
  `row height + gap + os_mod`. Three rows absorbed the difference and four did
  not, so the block's last row landed on top of the initial-phase line below it.
  Both now use the same figure.

- **The fire weather panel was unreadable.** Its rows were drawn wider than the
  columns holding them, and because the wind column is right-aligned the overflow
  ran *leftwards* across the moisture column, so the two interleaved: on a real
  sounding, up to twelve of twenty-one rows spilled, `0-1 km mean = 169/22`
  wanting 240 pixels of a 102-pixel column. The title overflowed too.

  A second fault was underneath it and had never been noticed. Row heights came
  from the font, and the font is scaled from the panel's height — so a taller
  panel grew its rows faster than it gained room for them, and the bottom rows
  were drawn below the frame where they simply vanished. The Haines row was off
  the panel at one common size and the whole derived block went under at another.

  Rows are now fitted to their column and the column widths use the panel's real
  padding, which also recovers the tenth of the width that was left empty on each
  side while the columns between them overflowed. Row height is capped by the
  space actually available, so all eleven rows are inside the panel at any size.
  This is the same repair the winter panel received earlier; the fire panel never
  got one, which is very likely why it did not read as a working feature.

- **The model domain outline was drawn in the wrong place on the curved map.**
  On the globe view the dashed boundary showing where a model has data was drawn
  as a straight-sided trapezoid, which is not the shape any of these domains are.
  A latitude line is only straight on the flat map; on a conic projection it bows,
  and the outline was drawn by joining the four corners of the domain box with
  straight lines, cutting straight across each bow. On a CONUS view that put the
  southern edge **118 pixels** from where the model actually ends and the northern
  edge 73 pixels, worst in the middle of the map — a fifth of the map's height, in
  the region you are most likely to be working in.

  That outline is the thing that tells you which ground you can pull a sounding
  from, so it is now walked along each edge and follows the projection: under a
  pixel of error at every zoom, on both views. The flat view is untouched, where
  the boundary genuinely is a rectangle and four corners were always right.

  The box-selection rectangle had the same treatment already but with a fixed
  number of steps, which thinned out across a wide drag; both now share one walk
  that follows the span, so a continent-wide box is as accurate as a county-wide
  one.

- **A model download no longer gives up because one mirror is having a bad day.**
  Every model file we fetch is hosted in several places — Amazon, Google,
  Microsoft, NOAA — and the download library tries them in turn so the others can
  answer when one cannot. Except it didn't: an error from *any single* mirror
  ended the whole search, and the run was reported as unavailable while the file
  sat there on the other three. On a corporate or campus network that blocks one
  host, that made models look permanently missing.

  It bit hardest through Microsoft, whose files need a short-lived access key
  requested before each download. When that service is busy it refuses the key,
  and the refusal arrives dressed as a success, so the library read a field that
  was not there and stopped. Several models list Microsoft *first*, so the mirror
  most likely to refuse was the one that took the working mirrors down with it.
  A third path: the key request had no time limit, so a service that accepted the
  connection and then went quiet stalled the fetch until you cancelled it.

  Each mirror is now tried on its own. One failing is logged with the reason and
  the search moves to the next; only when all of them fail is the run reported
  unavailable, exactly as before. Nothing changes when the mirrors are healthy.
  This is a local repair for a defect in the download library, sent upstream as
  [Herbie #554](https://github.com/blaylockbk/Herbie/pull/554) against the
  standing report [#246](https://github.com/blaylockbk/Herbie/issues/246); it
  removes itself automatically once a version carrying the real fix is installed.
  The indefinite wait is the one part that cannot be repaired from outside and
  needs that upstream release.

- **The field under the sounding thumbnail was misplaced near the edge of the
  model's domain.** For a profile anywhere within a degree or two of the HRRR
  boundary — the Pacific coast, southern Texas, the Canadian border, and the whole
  of the Gulf and Atlantic margins — the gridded field in the locator inset was
  drawn shifted, by as much as 0.84° of longitude, some seventy kilometres. The
  marker stayed in the middle of the inset, so the colours sitting under the point
  were not the colours at the point: you could read a CAPE gradient or a helicity
  maximum off the thumbnail and be looking at ground a county or two away.

  The inset asks the field for the small patch of ground around the sounding. Near
  the domain edge part of that patch is ground the model does not cover, and the
  request was being trimmed to the part that does exist while the area it was
  drawn into was left at full size — so the surviving strip was stretched across
  the whole inset and everything in it slid sideways. The patch and the area it is
  drawn into are now trimmed together, which keeps every pixel over the ground it
  describes and leaves the part of the inset beyond the model's edge empty. Empty
  is the honest answer there: the alternative is inventing a forecast for ground
  the model never ran on. Interior points are unaffected and still fill the inset.

- **Clicking a sounding site that shares a mast with a radar was a coin flip.**
  Forty-eight of the WSR-88Ds sit on top of a radiosonde site closely enough that
  at ordinary zooms the two markers are the same dot — KILX and Lincoln are six
  *thousandths* of a pixel apart, and Dodge City, Shreveport, Great Falls,
  Spokane, Brownsville, Nashville and Lake Charles are all inside a fiftieth of a
  pixel. A click went to whichever target was nearer, which at those separations
  is decided by floating-point noise. So clicking the one visible dot on the
  Station Map re-aimed the radar instead of picking the sounding site about half
  the time, and not the same way twice.

  The antenna now has to be *clearly* nearer — three pixels — before it takes a
  click, so the station keeps every shared mast. That is the right default on a
  map whose purpose is choosing a sounding site, and it costs the radar nothing
  reachable: an antenna standing on its own is still one click away, and every
  antenna including the co-located ones can still be chosen by name from the site
  list.

- **The single-site radar went blank when you moved the map.** Left on *nearest to
  map centre* — which is the default — the overlay kept the antenna that served
  the previous view. Pan from Oklahoma to New England and KINX's frame stayed
  attached: entirely off-screen, so the map showed no echoes at all, while the
  marker and the status line both still said KINX. Nothing indicated the map had
  outrun the data, and it stayed that way until the refresh cadence came round on
  its own, which for base reflectivity is two and a half minutes and for the
  accumulation products is five.

  Moving the map now re-aims the automatic choice as soon as the view settles, and
  the frame, the marker, and the status line arrive together. A pan that stays
  inside the serving antenna's range costs nothing, because the frame is still the
  right one. Panning somewhere no antenna reaches now says so instead of leaving
  the old frame sitting there looking current. A radar chosen *by name* is left
  alone — that choice is your answer to "which radar", and panning is not a
  retraction of it — and the national mosaic is unaffected, since a composite does
  not depend on where the map is looking.

- **The map legend was drawing on top of itself.** With a model field and an SPC
  outlook both showing, the colour bar's tick numbers, the outlook's category
  swatches, and the attribution prose all landed in the same few rows — the
  numbers under the bar and the `TSTM`/`MRGL`/`SLGT` boxes were painted over each
  other, so neither could be read.

  The colour bar is three stacked pieces: a caption, the bar, and a row of tick
  numbers. The legend reserved a single hard-coded height for it that had drifted
  five pixels short of what those three actually occupy, then advanced by the same
  wrong number — so everything below it started too high. Each block's height is
  now declared in one place and both the reserving and the drawing read it, with a
  gap between blocks so the tick numbers and the swatch boxes are not touching.
  The prose was also inset two pixels less than the bar and swatches above it, and
  now shares their margin.

  Tick labels no longer hang off the ends of the bar either, and the two end
  labels are placed before the middle ones so they cannot be dropped to make room:
  they are what tell you the scale's range. One scale was short of its own top —
  2 m temperature labelled up to 110 °F on a bar running to 120 — which is now
  checked for every product.

- **The model field on the map is now the same forecast as the sounding.** The
  gridded overlay ignored the cycle you had selected. It always drew the newest
  run at F000, so you could set the sidebar to the 12Z HRRR at F18, pull a
  profile valid 06Z tomorrow, and read it against a field valid *now* — a
  forecast eighteen hours away from the one in the window beside it. The overlay
  had the method to be told the time; nothing ever called it.

  It now follows the selection exactly. Choosing HRRR pins the field to that run
  and that forecast hour, which matters even when the valid time agrees: 12Z F18
  and 18Z F12 depict the same moment but are two different forecasts, and the six
  hours of extra data in the later one is the whole reason a forecaster looks at
  both. Selecting a different model instead matches the *valid time* with the
  freshest HRRR run that reaches it, because a GFS F120 does not name an HRRR
  forecast at all. The run, the forecast hour, and the valid time are all on the
  overlay's own caption either way, so the map cannot imply a currency it does
  not have.

  The observed tab follows its own sounding hour now too, and a past hour
  resolves to that hour's own analysis. Previously anything more than two days
  back fell through to a clamp and returned today's run — so a RAOB from last
  week sat under a field valid days later.

- **Colour scales were spending their range where the weather is not.** Measured
  against a real CONUS run, several products were mis-ranged badly enough to read
  as a rendering fault:

  - **Convective inhibition was the worst.** The scale stopped at −25 J/kg with
    nothing suppressed, so every point weaker than that — 83% of a summer
    domain, most of it not capped at all — was painted in the *same* colour as a
    genuine −25 cap, and the strong end clipped at −400 where the run reached
    −583. CIN is plotted negative, so its significant end is the bottom of the
    scale and the values to hide are the ones near zero; the scale had no way to
    express that. It now runs to −800 and draws only the ground that is actually
    capped, which on that run was 17% of the map instead of 100%.
  - **0–1 km bulk shear shared the 0–6 km scale**, whose floor is 20 kt. The
    shallow layer's median was 5 kt, so 98% of the field was below the floor and
    the map was blank. Shallow-layer shear now has its own scale, and 0–6 km
    reaches 100 kt rather than clipping at 85.
  - **0–1 km storm-relative helicity shared the 0–3 km scale** the same way, and
    lost 90% of itself under a 50 m²/s² floor.
  - **One isotach scale served every level**, so a colour meant one wind speed
    everywhere. That is a real virtue, but it cost more than it bought: on a
    20–180 kt scale the 850 mb map left 90% of the domain unpainted and used 4 of
    11 classes, because low-level winds do not reach jet speeds. Each level now
    gets the range its own winds occupy.
  - **Updraft helicity started at 15 m²/s²** — above where a rotating updraft
    first shows — and topped out at 275, which the 0–3 km layer does not reach.
  - **CAPE started at 250 J/kg**, hiding the weakly unstable ground where
    elevated convection forms. It starts at 100 now.
  - **Dewpoint clipped at 80 °F** on a run that reached 81.5, painting the
    moistest air the same colour as merely humid air. It reaches 85 now.
  - **2 m temperature and dewpoint are banded in 5 °F classes** rather than
    blended, with 32 °F kept as a class edge. A surface map is read for where a
    threshold lies, and a continuous wash makes every isotherm a judgement about
    shade.

  These are the ranges the fields actually occupy, checked by measurement rather
  than chosen by eye. They are not a copy of any commercial site's colour tables.

- **The picker could start on a Python it cannot survive.** The desktop GUI
  refuses to run on Python 3.14, which access-violates inside a worker thread's
  garbage collection with no catchable traceback, and it relaunches itself on the
  project's own 3.11 instead. A single environment variable disabled that guard
  outright — and because the guard sets that variable for the child it starts, a
  copy left behind in a shell disabled it for every later launch from that shell.
  The window then died mid-startup with a log that simply stopped: no exit code,
  no traceback, indistinguishable from the user closing it.

  An environment variable cannot overrule the interpreter's own version now. A
  stale flag is logged and ignored, and the relaunch target's version is verified
  from its `pyvenv.cfg` before it is used, so one relaunch attempt is provably the
  last. If no supported interpreter exists the window says so instead of
  crashing.

- **Soundings over saturated ground are no longer thrown away.** A model profile
  whose 2 m dewpoint came out a few hundredths of a degree above its 2 m
  temperature was refused outright — *"no verified surface merge"* — so fog, a
  marine layer, or a lake shore before sunrise could cost you the profile
  entirely. A point on the Lake Michigan shoreline at Milwaukee failed on the 06Z
  HRRR over an excess of 0.03 °C.

  That excess is not a data fault. Models publish 2 m temperature directly, but
  the 2 m dewpoint is derived here from specific or relative humidity, and that
  inversion does not use the model's own saturation formulation — so at RH near
  100% the derived dewpoint crosses the temperature by hundredths of a degree. The
  air is saturated, which is the physical reading, so the ground row is now
  clamped to saturation and kept. An excess large enough to mean a genuine decode
  error — half a degree, where a unit or level mix-up would be wrong by tens — is
  still refused, which is what the check existed for.

  HRRR and the Canadian RDPS both failed at the same point. The fix is in the
  shared surface contract, so ERA5, Open-Meteo, the ECCC models, and every GRIB
  product are covered together; the native decoder's stricter copy of the same
  test is now re-checked against that contract instead of ending the fetch.
  Verified against HRRR, RAP, NAM, NAM 3 km, HiResW ARW and FV3, RRFS-A, GFS, CFS,
  GEFS, ECMWF IFS, GDPS, RDPS, and ICON at a saturated lake shore, an inland
  plains point, and high terrain.

- **Coarse-resolution models could not return a sounding at all.** GFS, CFS, and
  GEFS failed with *"the point is out of the grid area"*. The server-side subset
  that keeps these downloads small asked for a fixed 0.15° box around the point —
  about 17 km, which is **narrower than one 0.25° GFS cell** and half a 0.5°
  CFS/GEFS cell. The filter returned a strip one grid row tall that the requested
  point was not actually inside, so the nearest-neighbour lookup had no cell to
  stand on. The box is now sized from each product's own grid spacing, two cells
  to a side, so the point is always bracketed on both axes — with the meridian
  convergence accounted for, since a degree of longitude buys less distance the
  further north you go. The 3–13 km models are unaffected: their requests were
  already wide enough and are unchanged.

- **Political boundaries ruled straight across the lakes.** Once the lakes were
  drawn, the borders stopped agreeing with them. Natural Earth publishes these as
  `boundary_lines_land`, but the data still bridges shore to shore with a single
  straight segment — the US/Canada line crossed Lake Superior as one 100 km chord,
  and state lines cut across Michigan and Erie. Over open water those chords read
  as a rendering fault rather than as geography.

  Segment length cannot be the test: real borders run dead straight for hundreds
  of kilometres, and this data has thousands of legitimate long runs (Australia's
  129°E line is a single 1239 km segment). Whether the segment lies *over water*
  is the test. The basemap build now samples each boundary segment against the
  lake rings and drops the ones crossing them, so a straight border over a prairie
  survives and a chord across a lake does not. 564 crossing segments went; every
  remaining segment of 150 km or more is a real border.

- **The inset panels only ever had three sides.** Storm Slinky, Theta-E v. Pres,
  SR Wind v. Height, Psbl Haz. Type, and the wind-speed strip each draw their own
  outline, but every one put its right-hand frame line one column past the last
  paintable pixel, where Qt clipped it away. Measured on a 170×180 panel: 161 lit
  pixels down the left edge, 2 down the right. Adjacent panels disguised it,
  because each one's left edge stood in for its neighbour's missing right edge —
  which left Psbl Haz. Type, last in the row, as the one that visibly refused to
  close. Every box now shuts.

  This was not the earlier border-thinning change: the clipped edge measures
  identically at a 2 px and a 1 px pen, so it had been there all along.

- **The skew-T was outlined twice as heavily as everything else.** The frame
  redraw that repairs the skew-T outline where in-plot label masks punch holes in
  it bypassed the proxy every other frame passes through, so its hardcoded 2 px
  width escaped the thinning. Every colour involved was already the same white,
  but a 2 px rule beside a hairline reads as a *brighter* white — which is how it
  surfaced, as a colour mismatch rather than a weight one. The page now carries
  one rule weight throughout, verified by pixel profile.

- **Box mode took the whole mouse.** Arming **Box…** made every left drag draw a
  rectangle, including the drag you wanted for panning — and because the mode
  stayed armed after a box was accepted, that pan became a *second* box on top of
  the extraction already running, which then reported "a box sounding is already
  in progress". The map still pans on a middle-drag or right-drag while the mode
  is on, and the mode releases itself once a box is accepted. A click without a
  drag is still a click, and a few pixels of hand jitter no longer commits an
  area.

- **Two soundings drew their titles on top of each other.** With more than one
  profile loaded at the same valid time, the Skew-T wrote the second title across
  the first: secondary titles were enumerated from zero, so the first of them
  landed on the focused title's own baseline, and both were drawn into a hardcoded
  150 px box with clipping disabled, so the real titles spilled through each
  other. Each sounding now gets its own line, the rects span the panel width, and
  an over-long title is elided.

- **A sounding opened from a box had no town name.** The box baked a synthetic
  grid-point label (`HRRR r009c000`) into every extracted file, specific enough to
  look deliberate, so the automatic town lookup treated it as a real label and
  left it alone. The averaged sounding now carries a coordinate label for its
  centre — the same thing a single-point fetch writes when no label is typed, and
  what invites the lookup to resolve a place name.

### Removed

- **The box workspace's cross-section and spread panels.** Both were vertical
  plots on a plain linear temperature axis, so beside this application's own
  Skew-T they read as broken rather than as a different view — and neither
  answered a question the field map and the averaged sounding do not answer
  better. The field map has the height back. Only the panels went:
  `BoxAnalysis.vertical_transect()` and `BoxAnalysis.envelope()` still return the
  slice and the per-level band, so a script can plot them its own way.

## [1.0.0-beta1] - 2026-09-01

First beta of 1.0.0. The scientific canvas is unchanged from 0.9.0; this release
is about the desktop application around it -- new data sources, map overlays, a
switchable chart slot, and a pass over the picker's layout.

### Added

- **The sounding window's streamwiseness slot is switchable.** Right-click it to
  choose between four charts instead of the one it always showed:

  | Chart | What it plots |
  | --- | --- |
  | Streamwiseness | unchanged, and still the default |
  | Storm-Relative Wind | storm-relative speed against height AGL |
  | θ / θe Profile | both traces, with the gap between them shaded |
  | Stepwise CIN & CAPE | CAPE and CIN for a parcel lifted from each level |

  The three new charts share one `HeightChartInset` base extracted from the
  streamwiseness chart's own drawing grammar -- title placement, dashed grid,
  rotated height axis, tinted fill, legend box, left border -- so they cannot
  drift apart from it or from each other. Each supplies only its data and its
  series. Axes scale to the sounding with rounded ticks rather than being fixed,
  because a chart that clips its own trace is worse than one with an unfamiliar
  axis.

  The slot is lazy. Setting a profile costs the visible chart only, so the
  stepwise CAPE chart -- which lifts a parcel per level and takes about
  three-quarters of a second -- is not computed unless it is actually opened,
  and is cached afterwards. It forwards the whole inset contract, so
  `sw.streamwiseness`, the deviant-vector toggle, and the profile refresh all
  keep addressing it as the single widget that column used to hold.

  Two upstream limitations had to be worked around to get the data. Upstream's
  `thermo.thetae` is scalar-only -- handed arrays it fails deep inside with
  `'float' object is not subscriptable` -- so it is applied per level. And
  `params.parcelx` needs virtual temperature, which this project's `Profile`
  does not publish, so every lift failed with `AttributeError: 'Profile' object
  has no attribute 'vtmp'`; the stepwise chart routes through the cached
  convective-oracle profile the derived module already builds, which also means
  its CAPE cannot disagree with the parcel values shown elsewhere in the window.

- **RRFS-A is selectable again, on a project-owned route.** All four domains had
  been withheld because no published file could complete the verified ground
  row. That was true of the file the previous adapter was looking at, and it is
  no longer the whole picture: RRFS splits each cycle into a pressure-level
  `prslev` product and a two-dimensional `2dfld` product, and the ground row
  lives in the second one. Pairing them produces a complete sounding, so the
  four domains are enabled and a fifth is added.

  Herbie cannot reach either file. Its `rrfs` template still points at the
  `noaa-rrfs-pds/rrfs_a/` prefix on AWS, which no longer carries operational
  output — the bucket now holds only retrospective, DESI, and sample
  collections, so `Herbie(...).grib` is `None` for every RRFS run — and its
  product list predates the split and rejects `2dfld` outright. RRFS is
  therefore the one product this project locates itself, in a new
  `sharpmod/rrfs_nomads.py`: it resolves the NOMADS release directory, parses
  the published wgrib2 `.idx` inventory, and pulls the selected GRIB messages
  over HTTP byte ranges through the existing shared transport. Nothing about
  decoding changes; only file location and inventory are project-owned, which is
  why these keys still require the GRIB runtime and still cache a grid-level
  dataset rather than becoming point providers.

  Every fact behind the route was measured against live inventories:

  | Domain | Grid | `prslev` file | Field plan | Ground file |
  | --- | --- | --- | --- | --- |
  | `rrfs-a` | 3 km CONUS | 596 MB | 338 MB | 14 MB |
  | `rrfs-a-alaska` | 3 km | 536 MB | ~305 MB | ~12 MB |
  | `rrfs-a-north-america` | 13 km | 312 MB | ~178 MB | ~6 MB |
  | `rrfs-a-puerto-rico` | 2.5 km | 51 MB | 29 MB | 1.2 MB |
  | `rrfs-a-hawaii` | 2.5 km | 21 MB | 13 MB | 0.4 MB |

  NOMADS publishes no spatial subsetting for RRFS: there is no `filter_*.pl`
  CGI, no OpenDAP dataset, and no pressure-level AWIPS subset, all three
  confirmed rather than assumed. A field plan therefore costs its full domain
  footprint. Three things make that acceptable. The payload is grid-level, not
  point-bound, so one transfer serves every point at that run and forecast hour.
  The transfer runs with eight range workers instead of the shared default of
  four, measured at 8.5 MB/s against 1.4 MB/s sequential, which puts a 3-km
  CONUS hour at about 35 seconds end to end. And the new 13-km
  `rrfs-a-north-america` domain covers CONUS for roughly half the bytes of the
  3-km domain, for callers who would rather trade resolution for time.

  Two smaller decisions are worth recording. The shared byte-range merge budget
  of a 2 MiB gap and 25 percent overhead is too loose here, because a field plan
  selects 270 of 675 messages and merging across those gaps re-reads whole
  unwanted ones: it cost 25 percent on a Hawaii plan and 24 MB on a CONUS plan.
  Tightening it to 512 KiB and 5 percent holds waste near one percent while
  still collapsing 270 messages into about 60 requests, comfortably more than
  the worker count. Separately, the two component subsets are deleted once they
  are concatenated, because keeping them would double a 3-km forecast hour to
  about 700 MB and let only four of them fill the default 3 GB cache budget,
  while buying nothing — pruning removes a whole model-hour entry, so a
  surviving component could never be reused alone. A small provenance sidecar
  beside the combined file records which fields were actually fetched, so a
  repeat request rebuilds its provenance truthfully and touches the network zero
  times instead of re-deriving a plan it would then have to re-download.

  The release directory is probed as `prod`, then `para`, then `v1.0`, and the
  winner is remembered for the process. RRFS reaches operational status at 12Z
  on 6 October 2026 and `prod` answers HTTP 403 until then, so this ordering
  migrates the route on implementation day with no code change, while a later
  removal of `para` still resolves because the full list stays available.

  **RRFS soundings have no vertical velocity.** It publishes DZDT and no VVEL,
  and although the shared field planner treats DZDT as a VVEL substitute, it is
  not one here: cfgrib decodes it as `wz`, geometric vertical velocity in m/s,
  while `Profile.omeg` is *pressure* vertical velocity in Pa/s with the opposite
  sign convention, and no decode path converts between them. Fetching it would
  add 45 messages — 37 MB on the 3-km domain — that no panel can read, and
  mapping it through unconverted would render inverted values two orders of
  magnitude too large. Those messages are not requested and omega reads as
  missing, the same call already made for Open-Meteo ICON. Absolute vorticity
  *is* fetched at all 45 levels, so NSTP computes; it was verified live at 2.17
  for Hilo and 2.46 for San Juan rather than reporting `--`.

- Added `rrfs-a-north-america`, the 13-km RRFS domain, aliased `rrfs-na`. It
  covers North America including all of CONUS for roughly half the transfer of
  the 3-km CONUS domain.

- Added map overlays: the picker maps can now host named, toggleable layers of
  geographic polygons above the basemap and below the station and point markers.
  Overlay geometry is decoded off the GUI thread into a frozen, Qt-free model
  with per-shape bounding boxes, so off-screen shapes are rejected without being
  projected, and one decoded layer can be shared between maps. Interior rings
  are honoured, which lets a category be filled exactly once instead of being
  blended with the categories stacked beneath it.
- Added **raster overlay support** to the picker maps, and with it the first
  image layer: a **live radar mosaic** from NOAA's MRMS products, on the Station
  Map and Forecast Model tabs. Composite reflectivity is the default, because
  the depth of a storm is the question a sounding is being drawn to
  investigate and base reflectivity would under-represent an elevated core;
  base reflectivity and enhanced echo tops are also selectable. Off by default,
  with an opacity slider.

  The imagery is served by NCEP's public GeoServer, which was chosen over the
  usual community NEXRAD mosaic for three reasons: it publishes true composite
  reflectivity rather than a mosaic of base reflectivity, it is the originating
  agency rather than a courtesy service that asks not to be leaned on, and it
  offers a plate-carrée projection that this application's own map transform can
  blit directly with no resampling. No credential is involved.

  Frames are requested at a **fixed continental extent** rather than at the
  map's current viewport. One request therefore serves every tab whichever way
  each is panned, panning and zooming cost nothing because only the image's
  corners are re-projected, and a frame stays locked to the basemap during the
  wheel-zoom preview. Only the visible portion of the frame is drawn, so zooming
  in does not scale the whole continent and then discard it.

  The frame is fetched at 1.9 km per pixel, against the source's own 1 km grid.
  An earlier draft asked for a quarter of those pixels and was visibly soft the
  moment the map was zoomed past a continental view. Magnification is
  nearest-neighbour rather than smoothed: interpolating a reflectivity field
  between data cells manufactures values the source never published, which is
  what reads as blur, so the overlay shows the cells that were actually
  measured. Shrinking is still smoothed, which keeps isolated cells from
  flickering as the map moves.

  Requests are made only while the overlay is switched on, at the source's own
  publication cadence of roughly two minutes and no faster, since polling ahead
  of publication returns the same pixels. A map looking somewhere outside the
  covered area says so instead of fetching a frame it could not draw. Changing
  opacity re-uses the frame already in hand and costs neither a request nor an
  image decode. A failed refresh leaves the previous frame on screen rather
  than blanking the map, and every frame is labelled with its own age and
  marked when it has outlived its refresh cycle, so an image that has quietly
  stopped updating cannot pass for a current one.

  Two failure modes specific to this kind of service are handled explicitly. A
  WMS reports errors as an XML document with a success status code, so payloads
  are identified by their own signature rather than by the response code, and
  the service's explanation is surfaced instead of the raw XML. And the WMS 1.3
  standard reversed the axis order of the usual latitude/longitude coordinate
  reference system, which would transpose the request and still return a
  plausible-looking image; the unambiguous longitude-first alternative is used
  instead.

  Overlay attribution is now drawn in the map legend. Every remote overlay has
  recorded its source since overlays were introduced, and none of them were
  crediting it on screen.
- Added the first overlay: the **time-aware SPC convective outlook**, available
  on the Station Map and Forecast Model tabs, with a selector for the
  categorical risk, the tornado, wind, and hail probabilities, or the Day 3
  total-severe probability. One hazard is shown at a time, as in SPC's own
  graphics, because the probability bands nest the way the categorical ones do
  and two hazards at once cannot be read. Areas that qualify the band beneath
  them are drawn hatched over it rather than as a solid wash that would hide it.
  Each product states which outlook days publish it, so a selection those days
  cannot reach reports that plainly instead of requesting products that do not
  exist.

  The overlay resolves the outlook that actually covers the selected valid time
  rather than simply fetching the latest one. SPC convective days run 12Z to
  12Z, so an overnight 06Z sounding is matched against the outlook issued the
  previous morning, and an 18Z sounding gets the 1630Z issuance in force at that
  hour rather than the later 2000Z update. Day 1 is preferred, falling back to
  Day 2 and Day 3 for forecast times whose Day 1 outlook has not been issued
  yet. The map legend names the product, states its validity window, and says so
  plainly when an attached outlook does not cover the selected time.

  The overlay is off by default and issues no network requests until enabled.
  Responses are size-bounded and fetched over verified HTTPS on a worker thread,
  never from the paint path.

  Request volume is bounded at four levels, so moving through times cannot turn
  into a request per selection: changes are debounced, so dragging a date field
  across forty days issues one request for the value it lands on rather than
  forty; a time already inside the loaded outlook's window issues none at all,
  which covers scrubbing forecast hours within a day; resolved outlooks and
  whole-day "nothing on file" verdicts are both cached, so revisiting a range
  already seen is free; and times before the service's 2020 GeoJSON archive or
  beyond Day 3 are answered locally without contacting SPC. A day's verdict is
  keyed by the set of products that existed when it was reached, so a newly
  issued outlook re-opens the question by itself. Transport and server errors
  are never cached as settled answers, so an outage does not suppress retries.
  Hiding the overlay keeps its geometry, making re-enabling free.

  Archived issuances are also cached on disk, so a case revisited in a later
  session costs nothing. Only archived products are stored, because those never
  change once published; the live endpoint advances through the day and a 404
  may become a real product later, so neither is persisted. The files are a few
  kilobytes each and the directory is capped by size and age. Set
  `SHARPMOD_OUTLOOK_CACHE=off` to disable it, or to a path to relocate it;
  `SHARPMOD_OUTLOOK_CACHE_MB` and `SHARPMOD_OUTLOOK_CACHE_DAYS` adjust the caps.
  A damaged or unwritable cache degrades to in-memory behaviour rather than
  failing.

  The outlook on screen is replaced whenever a different one now applies, which
  takes more than checking that the displayed product still covers the selected
  time — it almost always will. Every issuance of a convective day expires at
  the same 12Z, so the 1630Z outlook still covers 00Z the next morning even
  though the 2000Z update has superseded it for that hour; and a Day 3 outlook's
  window still contains the target long after the Day 1 for the same day has
  been issued. So each result records the ordered list of products that produced
  it, re-derived whenever the selection changes and every five minutes
  otherwise. Stepping forecast hours moves to the issuance in force at each
  hour, a target day advancing from Day 3 to Day 2 to Day 1 brings the overlay
  with it, and a hazard that publishes nothing for Day 3 appears by itself once
  the target becomes Day 2. Re-deriving the list is arithmetic, so hours that
  share an issuance still cost no request.

- Added the convective outlook to the **locator inset on the hodograph**, so an
  open sounding shows the risk at its own location and valid time without going
  back to the picker. The inset spans well under two degrees, so it is usually
  filled entirely by one category and a colour wash alone could not say which;
  a small chip names the category covering the sounding's exact point, resolved
  by an odd-even containment test that reads holes correctly and so reports the
  most severe area that actually applies.

  The inset is painted from inside a vendored widget's render pass, which
  receives nothing but the widget and must never touch the network — an
  unreachable service would otherwise stall a hodograph repaint, and that pass
  re-runs continuously while a window is resized. Overlays therefore travel with
  the sounding as profile-collection metadata: the viewer fetches on a worker
  thread when a sounding opens, attaches the result, and asks the hodographs to
  repaint. The paint path only ever reads what is already attached, and a
  regression test asserts it makes no network calls.

  Layers are matched to the sounding's own valid time, since one window can hold
  several profiles at different times and switch focus between them; a layer that
  does not cover the focused profile draws nothing rather than something wrong.
  Soundings outside the forecast area skip the request entirely.

  The inset follows the hazard selected in the picker, so opening a sounding
  while looking at the tornado probability keeps showing that hazard rather than
  reverting to the categorical outlook. Two tabs own an overlay, and the one in
  front decides: fetching from the Forecast Model tab uses that tab's hazard even
  when the Station Map tab also has an overlay switched on. Tabs that host no
  overlay of their own fall back to whichever is configured, preferring an
  enabled one.

  The seam is generic rather than outlook-specific: layers are keyed by product
  and replace only their own key, so a forecast-model product can be attached
  alongside the outlook later without touching the paint path.
- Labelled the hazard on the probability overlays. SPC publishes a probability
  as a bare decimal, so a legend that read `0.05` said neither that it was five
  percent nor whether it measured tornado, wind, or hail. Bands now read `5%`,
  the first legend swatch carries the hazard (`HAIL 5%`), and the locator badge
  — the only overlay text the inset has room for — names it in full, as in
  `TOR 15% CIG2`. A hatched area is reported as a qualifier on the band rather
  than in place of it: it deliberately outranks every band so that it paints on
  top, so answering with it alone would have dropped the probability the point
  actually sits in.
- Added SPC's **Conditional Intensity Groups**, which replaced the binary
  significant-severe area from the 1630Z Day 1 outlook on 3 March 2026. Where
  the old area only said "significant severe possible", these grade how strong
  the hazard could become if it occurs: tornado and wind publish three levels,
  hail and the Day 3 total severe two. Tornado CIG1 is a reasonable maximum of
  EF2, CIG2 of EF3, CIG3 of EF4+; wind runs 65, 73, and 82 knots; hail 2 and
  3.5 inches.

  SPC distinguishes the levels by pattern alone — every level publishes the same
  grey fill — so the pattern carries the data and is drawn as one family of
  increasing density: CIG1 a broken diagonal, CIG2 the same diagonal unbroken,
  CIG3 a diagonal cross. The map legend and the hodograph's locator inset use
  the same textures as the map, defined once so the two cannot drift, and the
  locator badge names the level rather than only the presence of a qualifier.

  The label is the only usable key for these areas. Colour cannot separate them,
  and `DN` collides with the probability scale, since CIG1 and the tornado 2%
  band are both `DN=2`. Levels rank above every probability band so a qualifier
  paints over the band it annotates, and above each other so a point inside CIG2
  reports CIG2 rather than the CIG1 area surrounding it.

  Outlooks issued before the change carry the old ungraded `SIGN` area instead.
  The archive read here reaches back to 2020, so both forms are decoded: `SIGN`
  keeps the single diagonal it was always drawn with, and is reported without a
  level, since claiming one would assert an intensity SPC never published for it.
- Added the **Day 3 total-severe probability** as its own product. Day 3 does not
  publish the individual tornado, wind, and hail probabilities; it publishes one
  combined probability with its own probability-to-category conversion, and it
  is where Day 3's intensity groups live. Without it a Day 3 selection could only
  show the categorical risk. It is offered as a separate entry rather than
  substituted for a hazard the day does not carry, because it measures a
  different quantity. Wind's 75% and 90% bands, added by SPC in the same change,
  are also recognised.
- Added **DWD ICON Global at 11 km through Open-Meteo** as a new forecast-model
  route, selectable as `icon`. ICON has never been reachable here: the installed
  Herbie has no loader for it, because DWD publishes split variables on native
  model levels and an icosahedral grid. This route needs no GRIB runtime at all,
  so `icon` now resolves instead of explaining why it cannot. Nothing resolved
  that name before, so no existing command or saved session changes meaning, and
  `ecmwf`, `ifs`, and `aifs` still point at the built-in routes they always did.

  Hours follow DWD's own cadence rather than the provider's: hourly to F078 and
  three-hourly after it, reaching F180 from 00Z and 12Z and F120 from 06Z and
  18Z. Open-Meteo will interpolate the later gaps back to hourly, but those
  values are not model output, so they are not offered.

  One sounding is one request. All 65 hourly variables — five fields across the
  twelve pressure levels from 1000 to 100 hPa that this model actually
  publishes, plus the five surface fields — travel together, because a request
  per level would multiply a user's metered calls twelvefold for no benefit. The
  level list is measured, not taken from the schema: Open-Meteo advertises the
  same twenty-six levels for every model and no model fills more than twelve, so
  asking for the rest would buy nothing and still be billed. Availability is
  answered from a static manifest rather than by probing, so changing a
  selection costs nothing; the run is confirmed when the sounding is fetched.

  The ground row is derived from the model's own geopotential-height profile at
  the reported surface pressure, not from the provider's terrain elevation. The
  two come from different datasets and can disagree: a reported 142 m sat above
  the 1000 hPa geopotential height of 110.9 m, which made height non-monotonic
  and failed quality control. Interpolating the model's own profile is
  self-consistent, and it interpolates between bracketing levels so high terrain
  stays accurate — a 700 hPa surface resolves to within a metre of the
  standard-atmosphere height.

  Vertical velocity is deliberately not carried. Open-Meteo publishes it as a
  geometric velocity in m/s while this format's `omeg` field is a pressure
  velocity, so passing one through as the other would be wrong; the field is left
  missing and one variable per level is saved.

  Requests are made from the user's own machine against their own allowance.
  There is no relay, no shared credential, and no key anywhere in the repository
  or package. Free access needs no key at all. A paid subscription is used by
  setting `SHARPMOD_OPENMETEO_API_KEY` locally, and that key is attached only
  after the exact official customer host is re-checked at the point the request
  leaves; it is scrubbed from error messages, which matters because the HTTP
  library embeds full request URLs in its own exceptions, and it never reaches a
  sidecar, cache entry, or log. Data is attributed to Open-Meteo under CC BY 4.0
  and to Deutscher Wetterdienst as the originating centre.

  Only ICON Global is enabled, and every excluded identifier is listed with its
  reason rather than being quietly absent. Combined "best match" and "seamless"
  products cannot name the model that produced a value, and ensemble means are
  not physically consistent profiles.

  **ECMWF IFS is not among the available models, despite being the obvious thing
  to reach for.** A live audit of twenty-seven identifiers found that Open-Meteo
  serves ECMWF IFS without any pressure-level fields. The failure looks like
  success: the run resolves, every surface variable arrives, and the elevation is
  reported, yet all five pressure families return zero levels, so no sounding can
  be built. The same audit found the advertised level ladders to be far more
  optimistic than reality across the board, which is why each model now carries a
  measured ladder. Twelve identifiers were confirmed usable; the eleven that
  publish no pressure data are mostly convection-allowing models. Three answered
  with a body that was not JSON and are withheld until that is understood.
- Added a UTC clock to the top-right corner of the picker's menu bar, showing
  the date and time to the second and the four-digit Zulu group beside it, as in
  `UTC 2026-08-30 02:57:34 · 0257Z`. Every run, cycle, and valid time in this
  application is UTC while the operating system clock is not, so the conversion
  was being made in the user's head on every selection. The Zulu group is
  spelled out because that is the form the cycle and forecast-hour fields take.
  Monospaced, so its width does not twitch as the digits change, and its timer
  stops with the window.

### Changed

- **Version is now 1.0.0-beta1**, spelled that way deliberately. Semver rejects
  `1.0.0b1`, PEP 440 needs a pre-release marker, and the Rust extension exposes
  `CARGO_PKG_VERSION` verbatim while a test asserts it equals the Python
  package's version. `1.0.0-beta1` is legal in both grammars, so one literal
  string satisfies the crate, the package, and that equality check; the built
  wheel normalizes to `1.0.0b1`, which sorts before `1.0.0` as a pre-release
  must. Bumping the crate also required refreshing `Cargo.lock`, since the
  rebuild runs `--locked` and the lock still recorded the old version.

- Raised the eccodes floor to **2.48.0** and the maturin floor to **1.15.0**, and
  pinned ruff to **0.16.4**. The eccodes bump is the only one that touches
  decoding, so it was verified rather than assumed: a full RRFS extract under
  2.48.0 produced byte-identical output to 2.47.0 -- same 46 levels, same
  surface pressure, temperature and dewpoint, same surface relative vorticity.
  Both of CI's ruff invocations are clean on 0.16.4.

- **The picker's control rails are one design again, and the forecast panel no
  longer scrolls on a maximized window.** Its rail needed 1267 px of a 973 px
  viewport, so the point and fetch controls sat below the fold on every screen.
  It now measures 867 px and fits with room to spare, while still scrolling when
  the window is genuinely too short -- at 1600x900 and below -- which is what the
  scroll area is for.

  | Rail | Before | After |
  | --- | --- | --- |
  | Forecast Model | 1267 px (scrolled) | 867 px |
  | Station Map | 941 px | 659 px |
  | Reanalysis (ERA5) | 690 px | 566 px |

  Nothing was removed to get there. The height came from four things that were
  wrong on their own terms:

  - **Every card was padded twice.** The style sheet already pads a card, and
    each card's inner layout added its own default margin inside that. Only the
    two availability cards had ever zeroed it, which is why they alone looked
    tight. Fixing it in the shared builders recovered about 18 px per card.
  - **The overlay cards each held a single switch.** The product and opacity
    controls of an overlay that is switched off cannot affect anything, so they
    now appear with the overlay instead of holding the card open. Both
    controllers then fit in one "Map overlays" card rather than two mostly empty
    ones.
  - **The ensemble member field was always shown, disabled.** A member is
    meaningless for HRRR or RRFS, so the card is hidden for deterministic
    models rather than present and dead.
  - **A hardcoded 210 px floor** on the run/valid-time card padded it well past
    its own contents, and the list of withheld models held three word-wrapped
    lines open at the bottom of the rail to say something that belongs on the
    model chooser's tooltip.

- **Combo boxes look like combo boxes again.** The style sheet restyled the
  drop-down sub-control but supplied no arrow image for it, and giving that
  sub-control any property makes Qt paint it from the style sheet instead of
  from the style. The arrow therefore vanished everywhere: the model, region,
  cycle, forecast, and overlay-product menus were indistinguishable from
  read-only text fields, while the date edit beside them kept its arrow. The
  rule is gone, so Fusion draws a palette-aware arrow again.

- **The three source panels now agree with each other.** They had each
  hand-rolled their own cards and grids, so the same control differed by tab.
  Two shared builders and a shared row helper replace that, and with them:
  one label-column width so every field starts at the same x down the whole
  rail rather than stepping in and out; "Region" and "Reset" everywhere
  (the station map said "Map area" and "Reset view"); "Cycle:" and "Town:"
  everywhere; the same inline placement and label for "Most recent" (ERA5 had a
  full-width "Latest likely available", with the ERA5 publication lag moved to
  its tooltip); tooltips on all three zoom buttons; and the same zeroed rail
  margins on the ERA5 panel, which was the only one still inset. The station
  map's selection line, the one ungrouped control in any rail, now shares a
  "Selected station" card with the availability it describes.

  The Census/OpenStreetMap credit for town lookups stays visible rather than
  moving to a tooltip, since OpenStreetMap's licence asks for attribution where
  the data is shown; it is just worded to fit one line instead of three.

### Fixed

- **The hodograph's `RM` and `LM` labels no longer sit on an opaque plate.**
  Upstream positions those two labels with rectangles its own comment calls "the
  invisible rectangles", and tries to hide them by setting an alpha-zero *pen*.
  It never clears the *brush*, so the rectangles were filled with whatever brush
  the previous draw call happened to leave active -- painting a solid block over
  the hodograph rings and traces behind each label. The two `drawRect` calls are
  now suppressed, which is what upstream intended; the text is drawn from the
  same rectangles and is unaffected.

  The same block also does `color = self.bg_color` followed by
  `color.setAlpha(0)`. That is not a copy: it mutates the widget's own
  background colour in place and left its alpha at zero for everything drawn
  afterwards. The alpha is now restored when the call returns.

- **The Skew-T's `SFC` label is back.** The effective-inflow label refit in this
  release had dropped `TextDontClip` and left clipping enabled around the bottom
  label. That label sits *below* the inflow layer's lower line, which for a
  surface-based layer is at or under the plot's bottom edge, so it was being
  clipped away entirely -- upstream lifts clipping for exactly that draw, and
  now so does the refit.

- **The UTC clock no longer renders as `JTC`.** The label was created empty and
  filled by a timer, but a menu bar sizes its corner widget from the size hint
  the widget had when it was attached -- so it stayed too narrow, and because the
  text is right-aligned the overflow was clipped off its *left* edge. It is now
  built from a full-width sample and pinned to that width.

  The same label was also losing its monospaced face: a style-sheet
  `font-family` beats `setFont`, so the base chrome rule kept putting the
  proportional UI font back, and the "does not twitch as the digits change"
  promise was not being kept. It now carries the numeric object name the style
  sheet keys the tabular family on.

- **RRFS no longer advertises 20 cycles that cannot produce a sounding.** It was
  configured for all 24 hourly cycles, with the off-hour ones advertising
  F000-F018 and the synoptic ones F000-F084. Live inventories show the off-hour
  cycles publish a sub-hourly two-dimensional product and *no pressure levels at
  all*, so they carry no sounding at any forecast hour rather than a shorter one.
  The cycle list is now `(0, 6, 12, 18)` and the RRFS forecast-hour trimming rule
  is gone, since every remaining cycle publishes the full F000-F084 hourly range.
  This also corrects `--probe --lookback-cycles`, which was stepping back one
  hour at a time through cycles that do not exist; it now walks the real ones.

- Fixed NSTP reporting missing for every rendered sounding. The Non-Supercell
  Tornado Parameter needs surface relative vorticity, which is a horizontal
  derivative of the wind field and so cannot be recovered from a single sounding
  column — it is read from neighbouring grid points at extraction time and
  carried along with the profile. The extractors were supplying it and the
  formula was computing correctly, but the value was being dropped in transit.

  The vendored profile copy rebuilds a profile from a fixed whitelist of arrays
  and re-attaches only the storm-motion vectors, discarding every other
  attribute. A profile collection re-copies its profiles whenever the target
  type changes, and selecting the accelerated parcel path is exactly such a
  change — so the vorticity was stripped the moment the renderer chose it. The
  same sounding computed NSTP correctly outside the renderer, which made it look
  like a broken formula rather than a lost input. The accelerated profile now
  carries these source-supplied surface scalars across a copy.

  Also stopped an optional enrichment from being able to discard a good decode.
  The wind-stencil estimate, used only when a model publishes no vorticity field
  at all, raises when it cannot produce a value, and it shared a `try` block with
  the primary GRIB decode. Its failure therefore threw away a complete profile
  and silently re-derived everything through the slower cfgrib/xarray path,
  recording a different backend in the sidecar. The enrichment is now attempted
  on its own, after the decode has succeeded, so a failed estimate costs only
  NSTP instead of the whole fast path.

  This is a narrow trade rather than a pure win: the discarded-decode behaviour
  did incidentally reach the xarray path's own stencil, which is a separate
  implementation and could have succeeded where the direct one failed. It is an
  acceptable trade because every GRIB-backed model here requests either `ABSV` or
  `vo`, so vorticity is resolved from a published field and the stencil is a
  safety net that should not normally be reached.

- Fixed the Skew-T's effective-inflow-layer and maximum-lapse-rate labels
  punching opaque rectangles out of the chart behind them. Each was drawn onto a
  plate filled with the plot's own background colour, so the plate added no
  legibility the background had not already provided while breaking every
  isotherm, dry adiabat, and mixing-ratio line that passed behind the text. The
  labels now sit directly on the chart and the linework runs through unbroken.

  The lapse-rate label comes from vendored code, so rather than restate the
  method — and risk drifting from its colour tiers and geometry — the original
  runs against a painter that forwards everything except the rectangle fill.

- Fixed the cycle lists running oldest to newest, which put the freshest run
  furthest from the cursor. An hourly model publishes 24 cycles, so the newest
  sat off the bottom of a scrolling list while 00Z — by then most of a day stale
  — was the first entry. Every cycle selector now lists the newest first, the
  hourly forecast-model one and the three-hourly observed ones alike.

  The cycle still selected by default is the same one as before: the most recent
  that has come round today. It is now looked up by hour rather than by position
  in the list, because position no longer tracks the clock. Every cycle each
  model publishes is still offered, since a past date needs all of them, and the
  availability check continues to report when a specific cycle is not out yet.

- Fixed every entry in the overlay's product selector claiming the same outlook
  days regardless of the selection. The day range was written once when the
  selector was built, from the days each product publishes, so a Day 2 or Day 3
  selection still read `Tornado probability (Day 1–2)` and looked like a
  statement about the day on screen. Each entry now names the day the product
  would actually resolve to, and states which days publish it only when the
  selection reaches none of them.

  The day is asked of the resolver that performs the fetch rather than derived
  from the date, so the two cannot disagree. Availability does not follow from
  the date alone: a hazard with no Day 3 product becomes reachable the moment
  that convective day's Day 2 outlook is published, which happens partway
  through the span the arithmetic still calls Day 3. Entries also refresh as the
  clock advances, since a selection moves from Day 3 to Day 2 to Day 1 while the
  window sits open. Resolving a day is arithmetic over candidate URLs, so
  restating the entries costs no requests.
- Fixed the sounding window's HD and UHD image exports being visibly softer than
  `sharpmod-render` output at identical pixel dimensions. Every scientific panel
  paints into a persistent bitmap cache and blits it, and the command-line
  renderer composes its whole window with those caches allocated at the export
  density, so text is rasterized once at final size. The interactive window is
  composed at screen density, so exporting it enlarged caches that had already
  been rasterized — smoothly, which is precisely what made it look soft.

  The export now re-rasterizes those caches at the target density first.
  Measured on the same sounding at 3260x2198, as the share of inked pixels
  sitting at mid-tone (a crisp edge ramps over about one pixel, a stretched one
  over several, so lower is sharper): the command line scores 0.5074, the export
  scored 0.7417 before this change and scores 0.5078 after, with the number of
  inked pixels landing within 0.1% of the command line's. A 46% gap closes to
  0.1%.

  Rebuilding runs each panel's background pass, snapshots it where the panel
  keeps a background cache, then runs its data pass. It deliberately does not
  call ``clearData``: that is a reset for when the profile changes, and most of
  these panels keep no background snapshot for it to restore from, so it simply
  allocates a blank cache. Calling it between the two passes discarded
  everything the background pass had drawn, leaving HD and UHD exports without
  axes, tick labels, titles, or legends, and in some cases without a whole panel
  — the effective-layer STP box plots among them.

  Only the caches are rebuilt, and the originals are restored afterwards, so
  exporting leaves the window on screen byte-identical and does not disturb the
  hodograph centring or skew-T zoom the user has set — the widgets' own
  initialisation, which would recompute both, is deliberately not re-run. Output
  dimensions are unchanged in all three modes, and lossless export is untouched
  because at 1x there is nothing to enlarge.
- Fixed the SPC outlook overlay keeping an earlier issuance after the selected
  forecast hour moved past a later one. Stepping a forecast hour from 18Z to 00Z
  stays inside one convective day and can reach exactly the same set of
  published products, so the overlay saw nothing new available and held the
  1630Z outlook when the 2000Z update was the one in force. The staleness check
  now compares the ordered resolution rather than the set of available products,
  since the ordering is what selects between issuances that are all equally
  available.
- Renamed the issuance in the overlay caption from, for example, `Day 1 1630Z`
  to `Day 1 · 1630Z issuance`, and the map legend now states whether the
  selected time falls inside the outlook instead of only warning when it does
  not. An SPC convective day runs 12Z to 12Z, so a sounding valid 00Z is
  correctly matched to the previous calendar day's outlook; seeing the two dates
  differ with nothing on screen to explain it read as a fault.
- Fixed the date-picker calendar popup, which showed an ellipsis in place of
  most day numbers and offered days from the neighbouring months. A
  `QCalendarWidget` is a `QTableView` internally, so the chrome style sheet's
  generic item padding also applied to its day cells and left too little room
  for two digits, at which point the item delegate elided them. The day cells
  are now painted directly, which removes the elision and lets days outside the
  month on show be left blank; the week-number column is dropped, returning its
  width to the day columns, and the weekday header uses single letters so it
  cannot elide either. Selection, the weekend tint, and any configured date
  range are unchanged.

## [0.9.0] - 2026-08-28

A redesign of the desktop application's interface. The scientific canvas — the
Skew-T, hodograph, and index panels — is deliberately untouched: its geometry
and colours are unchanged, and `sharpmod-render` produces byte-comparable
output. Everything described here is the surrounding application.

### Added

- Introduced a design token layer as the single source of truth for the
  interface: spacing and radius scales, control heights, a type ramp, three
  chrome themes (neutral dark, warm light, protanopia-safe dark), paired map
  palettes, and a generated style sheet. Colours, spacing, and control sizes are
  now named roles rather than literals repeated at each call site.
- Bundled Space Grotesk and JetBrains Mono for the interface and registered them
  at startup, so the typography is identical in the frozen executable instead of
  falling back to whatever the platform substitutes.
- Added user-controlled zoom to the sounding viewer: `Ctrl`+mouse wheel zooms
  about the cursor, middle-button drag pans, and a toolbar carries fit / actual
  size / step controls plus a continuous 20–400% slider and a percentage
  readout. `Ctrl+0` fits, `Ctrl+1` is actual size, `Ctrl+plus` and `Ctrl+minus`
  step. Plain wheel still reaches the canvas, which uses it for its own zoom.
- Added a "Sounding Panel" sidebar to the viewer (`Ctrl+B`) that lists every
  loaded sounding and marks which one is focused, so switching between them is
  one click instead of a walk through `Profiles` → a per-sounding submenu →
  `Focus`. It also exposes ensemble member selection, which previously had no
  on-screen control at all, and a shortcut to the source and quality report.
  The panel occupies horizontal space the sounding cannot use, so it does not
  shrink the plot.
- Replaced the picker's five-tab strip with a left navigation rail, which no
  longer truncates the longer source names.
- Added a live palette preview to Preferences → Colors, which previously showed
  an empty area in released builds.
- Added full screen on **`F11`** to both the picker and the sounding window, with
  `Escape` to leave and a **View → Full Screen** entry. It earns its place in the
  sounding window: the fit is limited by height, so the title bar and taskbar it
  reclaims make the sounding about 8% larger on a 1080p display. Leaving full
  screen returns a maximized window to maximized rather than dropping it to its
  small floating size.
- Gave the sounding window a **Help** menu, with the full interaction guide on
  `F1` and a switch to bring back the tips strip along the top. The window
  previously had no Help menu: the guide could only be opened from a button on
  that strip, and the strip's dismiss button is remembered between sessions, so
  closing it removed the only route to the guide permanently.

### Changed

- Applied one theme across the whole application, so the picker and every
  sounding window share a single visual language. Opening a sounding no longer
  jumps from dark interface chrome to light.
- Rebuilt the colour ramps as neutral graphite and warm paper. Every surface,
  border, and text role was previously tinted blue — the default dark ramp
  shipped by most interface frameworks — which both looked generic and competed
  with the canvas, where saturated colour carries meaning. Chrome now holds
  almost no colour of its own, and the accent is a muted steel blue rather than
  a bright primary.
- Restyled the station and point-selection maps to neutral terrain. The
  landmass, borders, coastline, model-domain outline, and station markers were
  all shades of navy, so nothing separated the map from the data drawn on it.
  Terrain is now neutral and the overlays keep their colour: red for an
  available station, amber for the current selection, cyan for a saved location,
  blue for the model domain. Marker meanings are unchanged.
- Made the availability indicator follow the theme instead of painting fixed
  colours, and moved the "checking" state from amber to blue — it reports
  progress, not a problem.
- Stopped busy states overwriting button labels, so a button that has been
  renamed keeps its name while it works.
- Replaced the interface's inline style sheets with semantic roles. Inline
  styles blocked the application-wide theme from reaching those widgets, which
  is why parts of the interface stayed unthemed.
- The sounding parameter guide is now part of the repository. It documents every
  displayed index — formula, the clamps applied in code, colour thresholds, and
  literature citation — along with which module owns each calculation, since the
  classic SPC composites come from vendored upstream while this fork adds ECAPE,
  the hazard classifier, and the kinematics. It had been excluded as a stale
  local copy.
- Moved `hrrr_extract.py` from the repository root to `scripts/`. It is a
  hardcoded one-off development script, not an entry point, and sitting beside
  `pyproject.toml` implied otherwise; its docstring now says so and points at
  `model-extract`, which is the supported route and merges the verified surface
  row this script never fetched. Behaviour is unchanged.

### Fixed

- Reclaimed the empty bands either side of the sounding when the viewer is
  maximized. On a 1920x1080 screen the fit is limited by height, which left
  about 459 pixels of unused width; the sidebar now occupies that space at no
  cost to the plot's scale.
- Rewrote the interaction guide's account of zooming, and made the guide window
  scrollable and screen-sized. Zooming had a single line — "zoom the Skew-T or
  hodograph" — which gave no direction, did not say that zooming out stops at the
  normal view, and did not distinguish zooming one panel from zooming the whole
  image on the same gesture. The guide also grew taller than a 1080p screen with
  no way to scroll, because it was laid out as a message box.
- Restored mouse-wheel zoom on the Skew-T and hodograph for laptop trackpads.
  The panels take their zoom from the wheel's angular delta and expect the
  discrete notches a mouse wheel sends. A precision trackpad sends neither:
  every event after the first in a gesture is marked as a continuation, and
  those were discarded before reaching the panel, while events reporting only a
  pixel distance carried nothing the zoom could read. Scroll is now translated
  for the panels, so a trackpad zooms smoothly and a wheel behaves as before.
  Zoom also now centres exactly on the pointer.
- Stopped fit-to-window cutting off the bottom of the sounding. The sounding
  was placed into the scaling view still carrying the vertical offset it had as
  the window's central widget, which pushed its lowest rows — the lapse rates,
  Corfidi vectors, and significant-tornado plot — below the region the fit
  covered. Because fitting hides the scrollbars, there was no way to reach them
  and no sign they existed.
- Stopped the sidebar cropping the sounding at actual size. The panel was wide
  enough to push the viewport below the sounding's own width, so the
  pressure-axis labels were cut off the left edge at 100% — the one view that is
  pixel-exact, since the sounding is drawn at that size and any other scale is
  resampled. The panel is now sized so 100% shows the full width, leaving only a
  short vertical scroll to the lower panels.
- Set the source and quality report in a fixed-width face and stopped it
  wrapping. It was rendered in the proportional interface font, which left every
  value column ragged, and wrapped at the panel width, which broke long data
  URLs and file paths mid-path. The window is also larger, so the whole report
  is visible without scrolling.
- Corrected interface contrast against WCAG AA. Control outlines sat at 1.65:1
  against the darkest surface, effectively invisible, and are now 3.58:1;
  tertiary text moved from 4.06:1 to 4.86:1. Every text and control-boundary
  pair is checked against its threshold in all three themes.
- Fixed the map falling back to a platform font, which silently substituted a
  different face for every label on the map.
- Fixed the picker's control rail clipping the widest panel's contents.
- Fixed an intermittent crash when quitting the application. Menu and toolbar
  handlers in the sounding window held a strong reference back to the window
  they belonged to, forming a cycle that Qt keeps outside Python's reach. The
  window then survived until the interpreter shut down, by which point the
  underlying object was already gone, and releasing it was an invalid memory
  access. It struck in roughly 4 of 10 runs, left nothing in the log, and
  affected every window with a tips strip, every forecast sounding through the
  playback toolbar, and the preferences dialog. All such handlers now hold their
  window weakly, and a test rejects any new handler that does not.
- Restored Ctrl+scroll zoom of the whole sounding on laptop trackpads. It read
  only the wheel's angular delta, which a precision trackpad leaves empty, so
  the gesture the guide documents did nothing at all — and because the event was
  still marked as handled, nothing else could act on it either. Zooming one
  panel had already been fixed for these devices; zooming the whole image had
  not.
- Fixed the sounding window sizing itself as though it had no toolbars. Only the
  menu bar was counted, so on some screens the window opened taller than the
  work area, and — because the same measurement decides whether the sounding
  fits at 1:1 — it could open in the non-zoomable view with every zoom control
  greyed out and a message claiming the sounding already fitted, while the lower
  index rows needed scrolling to reach.
- Fixed dismissing the tips strip with its own close button leaving the Help
  menu's "Show Interaction Tips" entry ticked, so bringing the strip back took
  two clicks.
- Fixed the forecast playback dialog's summary line keeping dark-theme colours
  on the light theme, where its validation message was pale grey on white.
- Fixed the sounding viewer retaining a window after it was closed when the new
  sidebar was present.
- Fixed the interaction guide and the source and quality report accumulating a
  window for every time they were opened. Both were kept alive by the sounding
  window rather than released on close, so repeatedly consulting the guide —
  which is expected while learning the zoom gestures — steadily grew the
  application's memory use.
- Fixed the sounding window opening narrower than it should when the sidebar is
  present, which briefly squeezed the plot before it settled. The width
  reserved for the panel was measured with a test that is never true before the
  window is first shown, so nothing was reserved.
- Fixed the Help menu's "Show Interaction Tips" entry opening unticked while the
  tips strip was visible, which made the first click do nothing and dismissing
  the strip take two.
- Fixed the surround immediately around the sounding keeping the previous
  theme's colour when the colour style was changed with a sounding open.
- Brightened the map's degree labels and state borders, which were close enough
  to the landmass to be hard to read.

### Removed

- Removed Windows code signing and its policy. The certificate requirements were
  not going to be met, so the release workflow no longer carries a signing
  provider, `signtool` step, SignPath submission, or signing-state output, and
  the policy document and SignPath artifact configuration are gone rather than
  left describing a process that never runs. Downloads are still verifiable: the
  release publishes `SHA256SUMS.txt` and GitHub build provenance, and the release
  notes and bundled README now say plainly that the executables are unsigned so
  SmartScreen may warn on first launch.
- Removed roughly 120 lines of dead hardcoded style sheets left behind when the
  design-token layer replaced them; both constants were defined and never read.


## [0.8.2] - 2026-08-23

### Fixed

- Restored observed-sounding display from the availability cache by preserving
  the legacy SHARPpy profile metadata required when a raw profile is promoted
  for the interactive viewer. This fixes the `Fetched, but could not display:
  'location'` failure reported for WBGB / station 96441.
- Disabled CDS logging and tqdm output only when the process has no writable
  standard-error stream, preventing ERA5 downloads in the windowed Windows app
  from failing with `NoneType` / `write` after retrieval succeeds.
- Restored every supported local sounding type to the native Open File dialog
  and normalized archive/BUFKIT metadata before adding a second sounding, which
  prevents the viewer from crashing on profiles without explicit model-run
  metadata.
- Made image and text export names follow the sounding currently focused in a
  multi-sounding viewer instead of remaining tied to the first opened profile.
- Preserved forecast classification when an SPC text sounding is paired with a
  generated metadata sidecar, so forecast profiles are not presented as
  observations.

### Changed

- Updated the locked release toolchain to setuptools 84.0.0, wheel 0.48.0,
  PySide6 6.11.2, PyInstaller 6.22.2, and Ruff 0.16.3, incorporating the six
  open dependency/action bump pull requests into one tested patch candidate.
- Updated the pinned SignPath submission action from 2.2 to 2.3.
- Removed the retired experimental forecast subsystem, including its UI
  readout, optional live fetch, offline research tooling, archived datasets,
  and cloud-batch scaffolding.

## [0.8.1] - 2026-08-10

### Fixed

- Reduced Windows first-window time by lazily constructing inactive picker
  tabs, deferring model-cache imports and pruning, importing the analysis stack
  only when a sounding opens, and sharing immutable basemap geometry.
- Reused a successful observed-sounding availability download when Generate is
  pressed instead of fetching and decoding the same profile twice.
- Deleted ordinary sounding viewers when closed, released picker references,
  and collected their Python widget cycles on the next event-loop turn so
  repeated open/close sessions no longer retain one viewer heap per cycle.
- Hardened Windows packaging so source, bundled Python metadata, Rust metadata,
  frozen runtime, and PE file/product versions must agree; official builds now
  reject stale editable metadata and optionally Authenticode-sign when both
  certificate secrets are configured.
- Made the faster one-folder ZIP the prominently labeled recommended Windows
  download while retaining the one-file build as an explicitly slower portable
  option, with signing state recorded in the release manifest.
- Made model downloads and GUI availability checks cancellation-aware and
  bounded, kept stale availability probes single-flight, corrected download
  progress, and disconnected closed sounding windows from live preference
  updates.
- Updated the locked release toolchain to pip 26.2.1, Ruff 0.16.1, and PyO3
  0.29.2, while keeping workflow bootstrap pins sourced from the release
  constraints so patch updates cannot make the release build self-conflicting.

## [0.8.0] - 2026-08-07

### Added

- Added checked JUnit performance budgets and JSON timing artifacts, bounded
  pytest-xdist lanes with one Qt/render worker group, a 3.11/3.12 compatibility
  smoke, one Python 3.13 coverage lane, one full 100-200-example property lane,
  and an exact non-parallel release gate.

### Fixed

- Bounded the 1 hPa layer-mean sampling by the layer it is asked for. Both
  `mean_wind` and `mean_wind_npw` built their sample pressures with
  `arange(pbot, ptop + dp, dp)`, which takes one step **past** the layer top
  whenever the layer depth is not a whole number of hectopascals. When that
  overshoot left the reported profile the sample interpolated to `MISSING` and
  was silently dropped, so the layer mean depended on where the fixed increment
  happened to fall rather than on the requested layer. The samples now end
  exactly at `ptop`, so the integration is bounded by `[pbot, ptop]` and never
  relies on extrapolating outside the profile.

  This is a real error, not a rounding difference: when the layer top *is* the
  profile's top level, the dropped sample removed the top wind from the mean
  entirely. MEASURED on the profile that exposed it, a 33-level sounding whose
  SFC-6 km layer is calm except at its 6 km top, the SFC-6 km non-pressure-weighted
  mean wind moved from `(-0.606, -2.719)` to `(-0.724, -2.850)` kt, which shifted
  the Bunkers right-mover motion by `0.18` kt and SFC-500 m SRH by
  `1.01 m^2/s^2` -- enough to disagree with upstream SHARPpy beyond the
  documented 1% tolerance. Agreement improved from `1.0142` to
  `0.000175 m^2/s^2`, roughly 5800x. Real soundings extend well past 6 km, so
  there the only change is the position of the final sample by at most 1 hPa.

  Fixed identically in the Python path and in the Rust `pressure_samples`
  backend so the two stay in parity, with the invariant pinned by tests on both
  sides. A whole-hectopascal layer keeps its historical sample set exactly.

  The property test that caught this now also compares the SRH **integration**
  on the oracle's own storm motion, which isolates the integration from any
  storm-motion difference. Its end-to-end comparison is skipped in the one state
  where upstream is not a reference: when the oracle's own SFC-6 km sampling
  steps outside its profile, its storm motion is built from a sample set that is
  not the requested layer. Upstream is not self-consistent there -- the
  out-of-domain sample is dropped for some profiles and resolved to the edge
  value for others -- so no single behaviour can match it, and the corrected
  reading is the one that includes the layer top. Requirement 1.5 remains
  covered by `test_winds_storm_motion.py`.

- Sharpened exported PNG text in the scaled image modes. Fonts are now created
  with `PreferAntialias | PreferQuality`, which `index_board` had applied locally
  after finding its bold face rendered pixelated, and with vertical-only hinting
  **while painting a density-scaled export surface**. Horizontal hinting snaps
  stems and advance widths in unscaled design space, and those snapped positions
  then land between physical pixels once the painter is scaled.

  MEASURED on real renders as the share of inked pixels fully on (higher is
  crisper): `hd` 0.357 to 0.372, `uhd` 0.527 to 0.568, with `uhd` now above the
  0.552 scored by rasterising the same face natively at the matching 25px size.

  The setting is deliberately scale-dependent rather than global: at 1x it
  measured **worse** (0.226 to 0.202), because full hinting is exactly what snaps
  stems onto whole pixels when there is no transform. `lossless` therefore keeps
  Qt's default hinting and is unchanged.

  Investigation also established what is *not* wrong, which bounds any further
  work here: HD and UHD are true high-resolution renders rather than upscales of
  a 1x raster, and their text already matched native rasterisation at the
  corresponding physical size before this change. The remaining softness at small
  sizes is a property of the bundled display face - at 9px it leaves only about
  5% of inked pixels fully on - which is a typography choice rather than a
  rendering defect, so it is left alone.
- **Every forecast hour after F000 was broken for five products** because
  terrain height is time-invariant and their providers publish it only at the
  run's analysis step. The verified-surface check read a single forecast-hour
  inventory, saw `surface_height` missing, and refused the sounding, so these
  models worked at F000 and failed at every other lead time. An audit of all
  eighteen configured products at F000 plus three later hours each found:

  | Product | Height field | Published at |
  | --- | --- | --- |
  | ECMWF IFS, ECMWF-AIFS | `z:sfc` (surface geopotential) | F000 only |
  | GEFS | `HGT:surface` | F000 only |
  | Canadian GDPS, RDPS | `*_GeopotentialHeight` WMS layer | analysis instant only |

  When surface height is the *only* missing element, GRIB extraction now
  downloads that one invariant message from the same run's F000 file and
  concatenates it into the subset, mirroring the existing CFS surface-companion
  route; the ECCC point provider requests its height layer at the run time
  instead of the forecast valid time. Verified end to end: terrain height is now
  identical between F000 and the completed forecast hour for all five products
  (IFS 355 m, AIFS 368 m, GEFS 342 m, GDPS 361 m, RDPS 340 m at Norman, OK). The
  refusal remains for genuinely incomplete products and its message now names the
  missing fields. `probe` reports these hours as complete with a
  `surface_contract_invariant_companion` flag, and it confirms the F000 file
  really carries the field rather than assuming it, so availability checks and
  the fetch path agree.
- Fixed a one-byte over-read in ECMWF byte-range downloads. Herbie's wgrib2
  inventories set `end_byte` to a message's last byte, but its eccodes
  inventories (ECMWF open data) set it to `_offset + _length`, which is the
  *first byte of the next message*. An inclusive HTTP `Range` therefore fetched
  one byte too many and the assembled stream did not end at the GRIB `7777`
  trailer. This stayed hidden only because a pressure-level selection happened to
  include the file's last message, where the server clamps the range at EOF;
  requesting any interior message on its own failed range validation and fell
  back to a full-file Herbie download. Eccodes inventories are now normalized to
  inclusive bounds before range planning.
- HiResW WRF-ARW and FV3 no longer advertise 06Z and 18Z cycles. NCEP runs the
  CONUS nests twice a day, so half the offered cycles could only ever fail with
  "no GRIB for run". Both are now configured for 00Z/12Z.
- ECMWF-AIFS forecast hours are 6-hourly, not 3-hourly. AIFS publishes steps
  0-360 by 6 at every cycle, but the picker offered the IFS 3-hourly ladder, so
  24 of 85 selectable hours (F003, F009, F015, ...) could only fail with "no
  ECMWF-AIFS GRIB for run".
- ECMWF IFS 06Z and 18Z now stop at F144. Those cycles are a short cut-off
  forecast; since IFS Cycle 50r1 (13 May 2026) they publish under `stream=oper`
  rather than the retired `scda` stream, but they still carry no step past F144.
  All 85 hours were previously offered at every cycle, so 36 of them always
  failed at 06Z and 18Z. Verified against live data: 00/12Z serve F360, while
  06/18Z serve F144 and nothing beyond.
- A failed ground-field companion download no longer fails the whole sounding.
  The companion path falls back to Herbie's own subset download on
  `OptimizedTransportUnavailable`, as the pressure-level path already did.
- ECMWF field planning was silently disabled, and every model downloaded
  redundant fields. `choose_*_fields` read an inventory column named
  `variable`, which is what Herbie's wgrib2 `.idx` inventories use, but ECMWF
  open data ships eccodes `.index` inventories whose column is `param`. Every
  ECMWF fetch therefore raised `model inventory has no variable column`, fell
  back to the broad configured search, and returned empty field provenance -
  which in turn made `cached_source_fields_compatible` always false, so the
  model disk cache was never reused for IFS or AIFS. Field selection now reads
  either column.

  Inventory narrowing was a second, separate defect: it matched records by
  variable *name*, which cannot express levels. That over-selected for NOAA
  products and would have mis-selected for ECMWF, where `z` is both the
  invariant surface field and a pressure-level field on every isobar. Narrowing
  now matches the chosen search expression, so the planned byte ranges are
  exactly what the download asks for. Measured against real inventories:

  | Product | Planned transfer before | After | Dropped |
  | --- | --- | --- | --- |
  | HRRR `wrfprs` F024 | 295.5 MB | 226.3 MB | 40 redundant pressure-level `SPFH` messages (`RH` is preferred) |
  | ECMWF IFS F036 | 99.9 MB | 91.2 MB | 14 redundant pressure-level `q` messages (`r` is preferred) |

  All 40 HRRR pressure levels are retained for all seven needed fields, and no
  pressure-level `z` is pulled for ECMWF. Verified end to end across all 13
  selectable products: identical level counts and identical surface rows to
  before the change, with field provenance now populated (13 fields for IFS, 12
  for AIFS, 10 for NOAA products) so cached subsets are reusable.

### Changed

- **Products that cannot produce a sounding are no longer offered.** RRFS-A
  (CONUS, Alaska, Hawaii, Puerto Rico) and AIGFS were selectable in the picker
  and the CLI but could only ever end in a verified-surface refusal, because no
  file they publish carries a complete ground row:

  | Withheld | Measured cause |
  | --- | --- |
  | `rrfs-a` and its three domains | The `prslev` index has 675 records, all pressure levels - no `:surface:`, `:2 m above ground:`, or `:10 m above ground:` entries - and `natlev`, `testbed`, and `ififip` return no GRIB. |
  | `aigfs` | The `pres` product has no ground fields, and the companion `sfc` product publishes only four messages: 2-m temperature, 10-m U/V, and mean-sea-level pressure. Surface pressure, terrain height, and 2-m moisture are absent from both. |

  `ModelConfig` now carries an `unavailable_reason`, and `available_models()`
  returns only products that can produce a sounding, which is the single list
  the picker and `--list` both read. The configs stay registered so keys,
  aliases, domains, and archived provenance still resolve, and `--list` reports
  them under "Known but not enabled" with the reason. Asking for one explicitly
  now fails immediately with that reason instead of a generic contract refusal.
  Restoring either product is a one-field change if NOAA publishes the missing
  fields. The remaining thirteen products were each confirmed end to end to
  return a sounding with a merged verified surface row.
- Centralized each test worker's `QApplication`, made explicitly elevated
  Hypothesis example counts respect the ten-example compatibility profile, and
  moved coverage/property compatibility duplication out of CI while preserving
  the complete scientific and release checks.
- Defaulted new hodograph displays to LCL-to-EL mean-wind centering with a
  20%-tighter 160-kt viewport, while preserving user-selected Normal and Storm
  Relative modes across profile and geometry updates.
- Re-rendered the README example sounding and added Inverted (light) and
  Protanopia (colorblind) palette screenshots. Both palette screenshots use the
  bundled OAX 2014-06-16 19Z observed sounding, deliberately a different profile
  from the dark HRRR example, so the palette change is visible independently of
  the data. All three are produced through the shipped `render()` pipeline with
  the persisted `color_style` preference, not by overriding palette internals.
  MEASURED mean luminance: dark `0.050`, Inverted `0.946`, Protanopia `0.051`,
  and Protanopia differs from a Standard render of the same sounding.

## [0.7.0] - 2026-07-30

### Added

- Added a cached Python/Rust SB/MU/100-hPa-ML parcel workspace with CAPE/CIN,
  LCL/LFC/EL, and 3/6-km CAPE summaries.
- Added the API 6 traced convective workspace for forecast, surface, MU, ML,
  and effective parcels, explicit Skew-T user-parcel ascents, and DCAPE with
  Python-oracle fallback.
- Added a coarse-grained Python/Rust profile-kinematics backend that computes
  and caches standard layer shear, mean wind, storm-relative wind, SRH, and
  Bunkers motion while retaining Python fallback for nonstandard layers.
- Added strict, lightweight validation for cached portable-sounding NPZ/JSON
  pairs, including array-shape, metadata, archive-size, and member-count
  checks.
- Added separate fast, full-property, and opt-in live-provider CI lanes with
  bounded test runtimes, branch coverage, focused Ruff checks, and dependency
  vulnerability auditing.
- Added a weekly live HRRR CONUS terrain matrix spanning the Pacific Northwest,
  Intermountain West, Rockies, central Plains, Southeast coast, and Northeast.
  It verifies that the current provider schema yields each point's local model
  surface and removes below-ground isobars rather than special-casing Denver.
- Added an explainable recent-cycle surface-contract probe for provider
  monitoring, including a failing CLI mode that lists missing ground fields.
- Added exact release constraints and release-workflow contract tests for
  source-SHA, action pinning, permissions, and artifact-only publication.

### Changed

- Reuse the standard parcel workspace across eligible SharpTab composite
  indices and cache failed full-convective-oracle construction, avoiding
  repeated Python parcel ascents for one immutable `Profile`.
- Upgrade decoded GUI/render collections to an accelerated convective profile
  that preserves SHARPpy's public objects while removing repeated Python parcel
  and DCAPE integrations.
- Revalidate cached provider mirrors before reuse and key provider decisions
  by the exact source set instead of retaining stale process-wide choices.
- Gate releases on the exact source commit that passed the reusable test
  workflow, build from constrained dependencies, and limit write permission to
  the artifact-only publish job.
- Fetch ECCC surface fields first and skip every pressure-level layer at or
  below the selected point's ground pressure, reducing unnecessary GeoMet
  point requests.
- Namespace persistent forecast caches by contract version; older payloads
  remain inspectable but cannot be reused as current soundings.

### Fixed

- Build ERA5 and every forecast profile from a verified model surface: fetch
  surface pressure/height, 2-m temperature/moisture, and 10-m wind; remove
  every below-ground isobar; and fail closed when a provider does not publish
  the complete ground contract.
- Decode one-point NOMADS subsets reliably when ecCodes cannot select an exact
  nearest grid point from the cropped payload.
- Decode CFS pressure and surface fields on their independently published
  regular and Gaussian grids, including surface dewpoint derived from specific
  humidity.
- Reject physically inconsistent extracted and cached profiles, including
  non-monotonic pressure/height and dewpoint warmer than temperature, before
  they can be written or reused.
- Promote unrecognized test warnings to errors, guard project-owned non-finite
  kinematic arithmetic, opt cfgrib into xarray's new merge defaults, and
  narrowly contain documented warnings from pinned scientific dependencies.
- Keep sparse soundings on the reference 0–6 km CAPE integral instead of
  applying the dense model-profile native shortcut across kilometre-scale gaps.
- Recover expired or orphaned model-cache leases so abandoned entries can be
  pruned.
- Report cache entries as removed only after deletion actually succeeds, and
  ignore malformed or incomplete portable-sounding pairs during cache reuse.
- Stop every picker-owned Qt worker before application teardown, including
  advisory station and model-availability probes that may be blocked in a
  network library.
- Keep the map, forecast-model, ERA5, and raw-WRF control rails usable at the
  picker's minimum size by scrolling them vertically instead of compressing
  their controls.
- Refit native-size sounding windows after realization so menu-bar height does
  not create unnecessary scroll bars or clip the bottom analysis row.
- Treat Windows' `SystemError` wrapper for an invalid `os.kill(pid, 0)` probe
  as a stale model-cache lease instead of failing GUI startup.

## [0.6.0] - 2026-07-28

### Added

- Added compact colored 0.5, 1, 3, 6, 9, and 12 km AGL dots with
  solid black numbers inside the marker and extra edge clearance for the
  `0.5` label, plus optional location/town
  entry and a cached background town lookup for the locator-map title,
  including coordinate-only files rendered outside the picker. A reproducible
  52,818-entry Census place/town index provides offline CONUS title coverage
  without drawing town names inside the map. Separately tiled and compressed
  Census county outlines keep the local locator useful without network I/O.
- Added visible, synchronized multi-sounding controls to the ERA5 and raw WRF
  tabs so repeated point/time selections can be overlaid in one analysis
  window.
- Added an annual Census-index freshness workflow that rebuilds the newest
  official CONUS place, state-boundary, and county-outline data and publishes
  changed generated resources for review.
- Added a hash-pinned SHARPpy compatibility installer that verifies the
  official wheel and its `RECORD`, corrects only its obsolete NumPy dependency
  metadata, records provenance, and requires `pip check` to pass.

### Changed

- Bounded supported source runtimes to Python 3.11–3.13, added Python 3.13 and
  Windows WRF CI coverage, kept comprehensive testing on pull requests instead
  of duplicating it after merge on `main`, and increased the full-suite timeout
  to match measured runtime.
- Reduced expected third-party warning noise while retaining unrecognized
  deprecations and updated the local ECAPE path to MetPy's current
  two-argument specific-humidity API.

### Fixed

- Prevented Herbie Unicode status glyphs from raising `UnicodeEncodeError` on
  redirected CP1252 Windows consoles.
- Replaced RDPS's misleading nearly-global WMS envelope with its operational
  rotated-grid acceptance test and curved picker-map outline.
- Added a file-based Windows clipboard helper for external automation, avoiding
  fragile inline `pwsh -Command` parsing of .NET object construction.
- Replaced Windows-incompatible `grep` instructions with `rg`/ripgrep usage and
  installation guidance.
- Restored raw WRF support in Windows releases by installing and bundling
  `netCDF4`/`cftime`, explicitly selecting a NetCDF4-capable xarray engine, and
  rejecting misleading SciPy-only NetCDF3 setups.
- Disabled pickle loading for user and cache NPZ files, added bounded archive
  and profile validation, and preserved adjacent JSON coordinates/model
  metadata for SPC and other non-NPZ files.
- Added timeouts, response limits, explicit resource closure, and one-download
  decoder dispatch for remote sounding URLs.
- Made town resolution strictly CONUS-only with bundled Census state polygons,
  offline-first lookup, settlement-only online parsing, and expiring positive,
  offline, failure, and negative cache entries.
- Kept coordinate-only recent points eligible for automatic town resolution
  when they are reused from the Forecast, ERA5, or raw-WRF source controls.
- Kept town names only in the locator title, replaced synchronous county
  requests during hodograph painting with bounded local Census tiles, and
  prevented height dots from colliding with one another or being covered by
  the locator inset.
- Painted hodograph height dots on the live, scale-aware widget layer so their
  small black numerals stay crisp in HD/UHD exports instead of being enlarged
  from the one-pixel-density hodograph bitmap cache.
- Rebuilt all cached sounding panels at the requested HD/UHD pixel density so
  titles, axes, parameter tables, insets, and scientific annotations render
  with crisp antialiased text instead of being smoothly enlarged from 1x
  backing pixmaps.
- Centered the hodograph LCL-EL mean-wind square and gave its measured value
  an edge-aware gap, kept height dots clear of vector annotations, and jointly
  packed the near-surface labels from every displayed sounding so their masks
  and numbers cannot overlap.
- Replaced silent locator/height-overlay failures with diagnostic logging and
  made GUI and headless file opens share the same town-title resolution path.

## [0.5.0] - 2026-07-22

### Added

- Added a non-blocking **Reanalysis (ERA5)** GUI workflow with a global point
  map, hourly UTC selection, snapped-grid preview, focused CDS setup errors,
  point/hour caching, cooperative cancellation, and viewer-scoped outputs.
- Added guided raw `wrfout*` extraction under **Open File**, including
  background domain/time inspection, curvilinear grid-edge validation, map
  selection, progress/cancellation, and viewer-scoped output cleanup.
- Added RRFS-A Alaska, Hawaii, and Puerto Rico point-sounding adapters plus a
  provider capability contract for domains, cycles, forecast hours, members,
  fields, levels, archive status, and supported transports.
- Added real Canadian GDPS and RDPS point adapters through ECCC MSC GeoMet,
  with exact run/valid-time checks, bounded layer fan-out, normalized 33-level
  soundings, spatial cache identity, provider capabilities, and CLI/GUI use.
- Added forecast-hour timeline queues with streaming partial results and
  slider, step, play, and loop controls in the sounding viewer.
- Added a downloaded-data library for validating, reopening/re-extracting,
  pinning, deleting, and copying provenance from persistent model-cache entries.
- Added searchable saved and recent points with versioned JSON import/export
  and forecast/ERA5 map markers.
- Added a focused-profile source, quality, and provenance inspector with
  provider/transport, decoder/backend, cache, level/missing-field, vorticity,
  and non-mutating QC details.
- Added resumable multi-point/multi-hour batch extraction with shared model-hour
  downloads, bounded concurrency, atomic outputs, and checksummed manifests.
- Added provider-neutral observed-sounding retrieval with explicit UWyo to IEM
  RAOB fallback in both the GUI and CLI.

### Performance

- Generalized bounded parallel HTTP-range retrieval from RRFS to every indexed
  Herbie model. Large coalesced spans are balanced across up to four workers by
  default, with per-worker sessions, ETag/Last-Modified identity pinning,
  ordered atomic assembly, resumable fragments, cancellation, progress
  aggregation, bounded transient retries, and an automatic sequential-range
  fallback. This does not change decoder parallelism.
- Added official NOAA geographic-subset routes for NAM 3 km CONUS, HRW
  WRF-ARW, HRW FV3, and CFS alongside the existing HRRR, RAP, NAM, GFS, and
  GEFS routes.
- Vectorized multi-point GRIB element reads, added direct four-neighbor wind
  stencils for products without vorticity fields, and removed HRRR Zarr's
  intermediate xarray point construction. Unsupported layouts retain the
  existing compatibility fallbacks.

### Fixed

- Preserved the cancellation signal during raw WRF extraction after the input
  dataset has opened, so cancelled GUI requests do not surface as failures.

## [0.4.2] - 2026-07-18

### Fixed

- Fixed forced-Rust startup and backend-test failures caused by a stale or
  mismatched locally installed `sharpmod_rs` extension. The new synchronization
  command detects version drift, rebuilds the locked native extension when
  needed, and verifies Rust selection in a fresh Python process.
- Fixed the Windows Qt `CO_E_CANTCALLOUT_ININPUTSYNCCALL` COM-apartment warning
  seen during GUI event-dispatch tests by selecting Qt's headless platform
  before any GUI test module is imported.

### Added

- Added `sharpmod-rust-sync` to check, rebuild, and verify the native Rust
  extension for editable source installations.

### Changed

- Moved frozen Windows executable validation out of pull-request CI. Pull
  requests retain Python, Rust, NumPy, and wheel coverage; executable builds
  and runtime checks now run only in the release workflow after merge.
- Defaulted the shared pytest Qt platform to `offscreen` before GUI modules are
  collected, preventing native test windows and collection-order failures.
- Ignored local sounding render and issue-working directories.

## [0.4.1] - 2026-07-16

### Changed

- Updated the Rust backend's `libloading` dependency from 0.8.9 to 0.9.0 and
  raised the source-build minimum supported Rust version from 1.86 to 1.88.
  Official binaries still bundle the native extension, and Python-fallback
  installations still do not require a Rust toolchain.

## [0.4.0] - 2026-07-16

### Added

- Added independently optimized Rust and Python backends for profile-array
  operations and direct pressure-level GRIB point decoding. Rust is the
  supported primary backend in official v0.4 binaries and whenever `auto`
  validates a compatible extension. Python remains the fully functional,
  portable fallback, while explicit modes expose compatibility failures instead
  of silently changing implementations.
- Added an all-model decoder matrix covering all 13 enabled forecast products:
  HRRR, RAP, NAM, NAM 3 km, HRW WRF-ARW, HRW FV3, RRFS-A, GFS, AIGFS, CFS,
  ECMWF IFS, ECMWF-AIFS, and GEFS.
- Added reproducible old-Python-versus-optimized-Python,
  old-hybrid-versus-optimized-Rust, and optimized-Python-versus-optimized-Rust
  benchmark drivers. Dated Markdown and JSON results under
  `benchmarks/results` retain fixture hashes, raw samples, selected grid
  coordinates, build fingerprints, unavailable stages, and equivalence output.
- Added cross-backend tests for selected point, pressure order, deduplication,
  missing masks, metadata, and scientific values within appropriate
  floating-point tolerances, including generated multi-field GRIB fixtures.

### Performance

- Replaced repeated Python cfgrib scans and full-grid xarray construction on
  compatible products with a direct ecCodes point decoder. It scans headers
  once per file identity, reuses the inventory and nearest-point selection,
  reads only required field/level scalars, and keeps bounded inventory,
  nearest-point, and exact decoded-sounding caches.
- Kept cfgrib as a functional compatibility path while reusing persistent
  indexes. Direct-decoder failures reduce split groups to a small point
  neighborhood before merge; products that require the established
  neighbor-wind vorticity calculation retain their full xarray merge.
- Added a Rust decoder that memory-maps the local GRIB subset, borrows message
  storage through ecCodes, releases the GIL during the immutable decode, and
  returns all sounding columns as one contiguous NumPy-compatible matrix in a
  single Python/Rust boundary crossing.
- Avoided speculative decoder parallelism. Profiling did not show a benefit for
  ordinary point soundings, so calls made by the Rust decoder remain serialized
  and the implementation focuses on fewer allocations, copies, and calls.
- Reused the existing model inventory for field planning and transport, passed
  cache-owned local GRIB subsets directly into the selected decoder, and kept
  source, run, valid-time, selected-point, field, unit, and lifecycle metadata
  unchanged through profile construction.

#### All-model decoder benchmark

Network transfer is excluded. Times are application-cold medians;
application caches and cfgrib indexes are cleared, while the operating-system
file cache is not flushed.

| Model | Production decode path | Levels old / optimized | Old/new omega | Old Python ms | Optimized Python ms | Python speedup | Old Rust hybrid ms | Optimized Rust ms | Rust speedup | Py/Rust optimized |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HRRR | direct GRIB (F000 may use point Zarr in auto mode) | 40 / 40 | matched | 13,656.191 | 6,666.527 | 2.05x | 13,623.801 | 6,325.879 | 2.15x | 1.054x |
| RAP | direct GRIB | 37 / 37 | matched | 4,468.287 | 2,213.136 | 2.02x | 4,442.193 | 2,095.314 | 2.12x | 1.056x |
| NAM | direct GRIB | 39 / 39 | matched | 8,174.641 | 1,233.397 | 6.63x | 4,003.166 | 980.808 | 4.08x | 1.258x |
| NAM 3km CONUS | direct GRIB | 42 / 42 | matched | 19,334.120 | 9,848.533 | 1.96x | 19,112.263 | 9,541.045 | 2.00x | 1.032x |
| HRW WRF-ARW | direct GRIB | 27 / 27 | matched | 7,927.530 | 3,963.836 | 2.00x | 7,966.092 | 3,858.457 | 2.06x | 1.027x |
| HRW FV3 | direct GRIB | 27 / 27 | matched | 7,553.566 | 3,518.836 | 2.15x | 7,719.979 | 3,432.609 | 2.25x | 1.025x |
| RRFS A | direct GRIB | 45 / 45 | matched | 14,380.822 | 6,821.914 | 2.11x | 14,533.194 | 6,379.690 | 2.28x | 1.069x |
| GFS | direct GRIB | 33 / 41 | matched | 14,502.366 | 3,609.185 | 4.02x | 14,454.516 | 3,333.053 | 4.34x | 1.083x |
| AIGFS | xarray vorticity fallback (direct point decoder benchmarked) | 13 / 13 | matched | 3,021.279 | 1,144.266 | 2.64x | 2,884.502 | 1,090.785 | 2.64x | 1.049x |
| CFS | direct GRIB | 37 / 37 | matched | 4,944.596 | 2,517.830 | 1.96x | 4,885.038 | 2,365.896 | 2.06x | 1.064x |
| ECMWF IFS Open Data | direct GRIB | 14 / 14 | matched | 3,487.357 | 1,330.818 | 2.62x | 3,438.142 | 1,239.230 | 2.77x | 1.074x |
| ECMWF-AIFS | xarray vorticity fallback (direct point decoder benchmarked) | 14 / 14 | matched | 2,926.893 | 1,123.699 | 2.60x | 2,930.174 | 1,049.424 | 2.79x | 1.071x |
| GEFS | xarray vorticity fallback (direct point decoder benchmarked) | 12 / 12 | different (12 -> 1 valid) | 927.236 | 246.907 | 3.76x | 900.190 | 201.013 | 4.48x | 1.228x |

Across all 13 fixtures, the geometric-mean speedups are **2.61x for Python**
and **2.65x for Rust**. Optimized Python divided by optimized Rust is 1.082x,
so optimized Rust has about 7.6% lower latency overall. The matrix's NAM row
contains an isolated system-wide timing stall; the separate five-repeat
[NAM confirmation](benchmarks/results/2026-07-16-nam-decoding-v0.4.0-windows-amd64.json)
measured 3.36x for Python and 4.04x for Rust.

`Old Rust hybrid` is the frozen historical cfgrib/xarray algorithm followed by
native wind post-processing; the old extension did not decode GRIB. See the
[complete benchmark report](benchmarks/results/2026-07-16-all-model-decoding-windows-amd64.md)
and [raw JSON record](benchmarks/results/2026-07-16-all-model-decoding-windows-amd64.json)
for fixture hashes, raw samples, equivalence results, and environment details.

### Fixed

- Decoded every logical field in packed GRIB multi-field messages, preserving
  both U and V winds when they share one physical byte offset. This fixes the
  layouts used by products including RAP, NAM, NAM 3 km, HRW WRF-ARW, HRW FV3,
  and CFS in both Python and Rust.
- Preserved every published pressure level while applying stable descending
  pressure sorting and aligned deduplication consistently across all columns.
- Aligned scalar-pressure variables to their actual pressure before xarray
  merging. In particular, a GEFS omega value published only at 850 hPa remains
  missing at all other levels instead of being broadcast or indexed as a full
  vertical column.
- Applied preference changes to the complete mounted sounding widget tree.
  Switching to **Inverted** now updates the Skew-T, hodograph, locator, storm
  slinky, insets, IndexBoard, and Streamwiseness panels immediately instead of
  leaving custom surfaces in the dark palette.
- Made inverted/light colors readable without changing scientific values or
  established dark palettes. Theme-dependent text and semantic annotations use
  a complete contrast-checked role palette, and headless rendering now honors
  the same selected inverted colors as the GUI.

#### Sounding palette previews

Both previews use the same checked-in HRRR profile, selected parcel, and viewer
state so only the palette changes.

##### Inverted / light mode

![SHARPpy Reimagined sounding in inverted light mode](docs/images/v0.4.0/sounding-light-mode.png)

##### Protanopia colorblind mode

![SHARPpy Reimagined sounding in the Protanopia colorblind palette](docs/images/v0.4.0/sounding-protanopia.png)

### Compatibility and limitations

- Products without a usable published relative- or absolute-vorticity field
  retain the xarray wind-gradient fallback in production so derived
  vorticity behavior is preserved, even though their direct point decoder is
  measured by the benchmark matrix.
- Optimized Python and Rust return matching pressure-aligned omega values and
  missing masks within the cross-backend tolerances. The GEFS cross-generation
  report's `12 -> 1 valid` difference is the intentional correction of the
  frozen legacy xarray scalar-pressure broadcast, not missing Rust
  functionality or a new scientific-value discrepancy.
- Rust is the supported primary backend in official v0.4 binaries and in
  `auto` mode whenever its versioned contract validates. Optimized Python
  remains the fully functional portable fallback for source installs and
  platforms without a compatible native extension; GUI and CLI behavior do not
  change when fallback is required.

## [0.3.1] - 2026-07-15

### Fixed

- ERA5 point extraction now accepts scalar latitude/longitude coordinates from
  zero-area CDS responses and does not mistake a snapped singleton coordinate
  for the source dataset's geographic coverage.
- ECAPE's NCAPE calculation now evaluates only through the equilibrium level,
  avoiding invalid saturation calculations in unused upper-stratospheric
  levels.
- Windows builds now analyze from the actual repository root so the local
  `sharpmod` package is embedded, and frozen runtime verification checks
  `logging.handlers` and the GUI entrypoint in both build formats.

## [0.3.0] - 2026-07-14

### Added features

- Portable `.sharpmod-session` analysis sessions that save and restore every
  sounding in a viewer, the active profile/time/member, profile and storm-motion
  edits, interpolation state, parcel selection, and supported viewer state.
  Sessions use validated, versioned JSON and never embed source GRIB downloads.
- Fifty-step undo/redo history for mouse and numeric profile edits,
  interpolation and reset actions, and storm-motion changes. Use `Ctrl+Z` and
  `Ctrl+Y`, or the new Edit menu actions.
- Availability-aware forecast-model selection. The picker checks the selected
  model, run, forecast hour, and member in the background and offers an explicit
  **Use available cycle** action when a newer selection has not been published.
- Multi-sounding analysis windows with profile focus/removal controls and a
  remembered option to add newly opened soundings to the active viewer.
- A validated **Edit Nearest Level** dialog for pressure, height, temperature,
  dewpoint, wind direction, and wind speed, with immediate recalculation of
  derived displays.
- Maximum Parcel Level (MPL) values in the parcel table and Skew-T level labels.
- Persistent GUI preferences for units, colors, readouts, default parcel,
  multi-sounding behavior, dismissed tips, recent files, and picker selections.
- Worldwide coast, country, and state/province outlines in the hodograph locator
  while retaining the detailed U.S. county overlay.
- Three-hourly observed-sounding selection from 00Z through 21Z on both the
  station map and station list, including special/asynoptic launch times.
- Accelerated forecast retrieval with a direct HRRR analysis-Zarr point path,
  NOAA NOMADS geographic subsetting where supported, adaptive coalesced HTTP
  ranges for other indexed providers, and automatic fallback to Herbie's
  standard downloader.
- A persistent, bounded forecast-model cache plus optional **Prefetch Next
  Forecast Hour** and **Clear Downloaded Model Cache** File-menu actions.

### UX improvements

- Removed calendar dates from sounding titles while retaining compact run and
  valid UTC hours, forecast hour, and coordinates.
- Model availability checks are debounced and ignore stale worker results.
  Unknown or transient probe failures leave manual Fetch available, and the
  selected run is never changed without user confirmation.
- Added forecast-download stage and byte progress reporting, actionable
  GRIB-runtime errors, and a Help action that opens the rotating GUI diagnostic
  log folder.
- Reuses a decoded full-GRIB forecast hour when another point is requested from
  the same model, run, forecast hour, and member. Point/subregion downloads are
  cached by coordinate so data from one location cannot be reused for another.
- Added a dedicated Cancel button for forecast retrieval. Compatible range
  downloads retain verified partial fragments so an interrupted request can
  resume instead of restarting every byte.
- The fast path automatically races equivalent Herbie mirrors, remembers the
  quickest healthy provider for six hours, and reports the chosen transport
  and fields in the point sounding's metadata.
- Small indexed subsets use direct ranges instead of paying the NOMADS CGI
  preparation cost; geographic NOMADS cropping is reserved for transfers above
  32 MiB or inventories whose size cannot be determined safely.
- Optional next-hour prefetch is disabled by default, runs independently of the
  active fetch, and never opens a viewer or replaces the user's selection.
- Kept custom-panel numeric values readable by measuring and drawing compact
  unit suffixes before applying overflow elision.

### Bug fixes

- Replaced the removed Herbie `era5` model path with official Copernicus CDS
  pressure-level retrieval, including all 37 levels, point-sized requests,
  temporary-GRIB cleanup, and actionable CDS credential guidance.
- Restored forecast-model availability checks and downloads on Windows Python
  3.14 by loading the ecCodes DLL bundled in its pure-Python wheel when a
  version-specific `_eccodes` helper wheel is unavailable.
- Moved automatic model-availability GRIB validation onto the main Qt thread
  before starting a probe worker.
- Prevented native Windows GUI startup crashes under Python 3.14 by handing
  source-checkout launches to the project's Python 3.11-3.13 environment before
  `QApplication` starts. Packaged releases already bundle Python 3.11.
- Prevented duplicate concurrent requests for the same model hour with
  single-flight cache loading, while allowing different hours to proceed
  independently.
- Included the point location in persistent cache identity so two locations can
  never reuse the wrong extracted sounding, and protected in-use entries from
  age/size pruning.
- Validated HTTP partial-content responses, entity tags, file size, and GRIB
  boundaries before publishing resumed or coalesced downloads.
- Handled RAP inventories that expose packed U/V wind messages at a shared byte
  offset, preventing the optimized range path from unnecessarily falling back
  to a full Herbie download.

### Code improvements

- Replaced the monolithic `sharpmod.gui` implementation with a small compatible
  facade and focused picker, settings, fetch-worker, map, session, viewer, and
  shared-runtime modules.
- Moved renderer monkeypatch installation into one ordered registry that checks
  for the supported SHARPpy version before applying any patches.
- Analysis-session files contain decoded sounding state only, preserving the
  existing cleanup lifecycle for temporary forecast-model data.
- Added a bounded, lease-aware model-hour cache that owns shared GRIB data
  independently from viewer-scoped point files and safely closes decoded
  datasets on eviction or application shutdown.
- User-facing package, window, and renderer version labels now read from
  `sharpmod._version.__version__` as the single source of truth.
- Added focused field planning that selects one available humidity field and
  one available vertical-motion field while retaining every pressure level
  published by the selected model.
- Split accelerated retrieval into field-planning, range-transport, provider,
  disk-cache, and HRRR-Zarr modules with cancellation and fallback boundaries
  that can be tested independently.

### Packaging and project maintenance

- Added `cdsapi` to the ERA5 optional dependency set and bundled the CDS/ECMWF
  runtime modules in standalone GUI builds.
- Expanded the frozen-launcher dependency check to verify that CDS retrieval is
  available before the GUI starts.
- Bundled and runtime-checked `numcodecs` and `pyproj`, which are required by
  the HRRR Zarr point backend.
- Stopped tracking generated `sharpmod.egg-info` directories, standardized text
  line endings with `.gitattributes` and `.editorconfig`, and archived the stale
  outer wrapper and artifacts so the repository has one obvious project root.
- Excluded AI-agent plans and metadata, the internal engineering backlog,
  legacy `attic` prototypes, local analysis sessions, credentials, logs, and
  scratch files from consumer-facing source releases.

### Tests and documentation

- Added regression coverage for analysis sessions, edit history, model
  availability, GUI module boundaries, settings persistence, multi-sounding
  behavior, level editing, MPL display, patch registration, and versioning.
- Added ERA5 CDS request, missing-credential, forecast-model registry, and frozen
  packaging regressions so removed or unbundled data-provider integrations fail
  during testing instead of at runtime.
- Added retrieval regressions for field pruning, adaptive range coalescing,
  resume validation, provider selection, persistent cache pruning, single-flight
  loading, HRRR Zarr decoding, cancellation, and background prefetch.
- Updated the README, usage guide, and installation notes with CDS account,
  dataset-licence, `.cdsapirc`, and optional-dependency setup instructions.
