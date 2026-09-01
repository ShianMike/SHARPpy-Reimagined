# Changelog

All notable changes to SHARPpy Reimagined are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
