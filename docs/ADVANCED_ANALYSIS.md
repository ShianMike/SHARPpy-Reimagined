# Advanced analysis, verification, replay, and sharing

This guide covers the cross-sounding workflows in the sounding window and the
optional environmental layers in the picker. The guiding rule is exactness:
missing members, levels, and times remain missing. The application does not turn
them into zeroes or silently replace them with nearby data.

## Picker layout and map gestures

Choose the sounding source from **Load From** in the picker menu bar, beside
**File**, **Locations**, **View**, and **Help**. The central map or form therefore
uses the full window width. Controls inside a source are grouped into collapsible
sections; use each section's chevron to hide or reveal it without resetting its
values.

On the Forecast Model map, **Box…** arms one rectangle gesture. It releases after
the rectangle is accepted, rejected, cancelled, or blocked, and **Escape** clears
an in-progress or completed rectangle and releases the mode. A storm-report
marker receives click priority over point-sounding selection: click the marker to
read its description without moving the sounding point.

## Opening the analysis workspace

Open a sounding, then choose **View → Analysis Workspace** or press
`Ctrl+Shift+A`. The workspace uses the soundings already loaded in that viewer.
Its nine top-level tabs are **Trends**, **Compare**, **Ensemble**, **Notes**,
**Scenarios**, **Verify**, **Observed Winds**, **Share**, and **Replay**.

The forecast picker's **Workspace…** command remains the quickest way to acquire
several models, successive runs, or ensemble members at one point and exact valid
time. A failed member does not discard the members which loaded successfully.

## Trends and comparison

### Trends

Choose one diagnostic to plot across every loaded forecast timeline. A missing
profile remains a visible gap. Clicking a plotted point activates that sounding
and exact valid time. **Export CSV…** writes the values currently represented by
the chart.

### Compare

Choose the reference sounding. The comparison table and the synchronized visual
panels use its exact valid time; a collection without that time remains
unavailable. Each row records the basis for its deltas:

- requested and selected coordinates;
- grid spacing and terrain elevation when supplied by the source;
- run time and forecast lead;
- parcel and storm-motion conventions; and
- whether the profile has been edited.

Location and terrain differences are warnings, not bans, so an intentional
spatial comparison remains inspectable. A conflicting parcel or storm-motion
convention marks affected diagnostics incompatible instead of presenting a
misleading delta. Unknown metadata remains `Unknown`.

The upper comparison view supports two or four panels. It places the reference
first, shares temperature and height ranges, and draws one cursor height across
all visible panels. Missing vertical layers remain gaps. The lower difference
view uses conservative common-height alignment for temperature, dewpoint, and
wind components; drawing interpolation does not alter the scientific table.
Reference choice, two/four-panel layout, splitter position, and current tab are
restored with an analysis session. **Export CSV…** includes the values,
compatibility issues, and provenance.

## Ensemble accounting and thresholds

The acquisition ledger distinguishes four questions:

- how many members were originally requested;
- how many loaded successfully;
- which failed or were cancelled; and
- how many contain every value needed by a particular diagnostic.

For example, a 31-member request which loads 24 remains `24/31 loaded`; a
diagnostic usable for 22 reports `22` as its own denominator. Unknown diagnostic
values are neither zero nor a nonqualifying result. One usable member is retained
and displayed, while the workspace states that two are required for a meaningful
distribution. **Retry unavailable** submits only the recorded unsuccessful
members and merges successes into the original collection. The ledger survives a
session save/reopen.

In **Ensemble → Spread**, the table shows p10/median/p90 and each diagnostic's
usable count. The thermodynamic envelope shows the available temperature and
dewpoint range by pressure.

In **Ensemble → Thresholds**:

1. Name a definition and choose a diagnostic, operator, and value.
2. Enable additional rows for a joint condition. Joint conditions are evaluated
   member by member on the common usable subset.
3. Choose **Evaluate current** or **Evaluate timeline**.
4. Select a row in the member table to open that member's sounding, or save the
   definition/results as JSON and export member classifications as CSV.

Every result reports qualifying, usable, loaded, and requested counts plus both
`qualifying / usable` agreement and `usable / requested` coverage. It is labelled
**ensemble ingredient agreement; not a calibrated severe-weather probability**.
Cancelling a timeline retains the exact times already completed.

## Saved sensitivity scenarios

Open **Scenarios**, focus the intended profile, and select **Use focused as
baseline**. The baseline is copied once and remains immutable; later focus or
viewer edits do not silently replace it.

Named scenarios support:

- surface temperature and surface dewpoint changes in degrees Celsius;
- a moisture-layer dewpoint change with a configurable AGL top and uniform or
  linear-taper application;
- a localized cap-temperature change with AGL bottom/top and uniform or
  triangular application; and
- a storm-motion `u`/`v` vector delta in knots.

The editor shows the amount, units, vertical extent, and interpolation rule for
every applied change. Thermodynamic changes are bounded to ±30 °C and storm
motion to ±100 kt; malformed layers, dewpoint above temperature, and other
invalid profiles are rejected with a reason. **Apply changes** invalidates cached
diagnostics. The table then compares baseline and scenario using the normal
metrics backend. **Open hypothetical in viewer** creates a separately labelled
collection and opens Compare; it never mutates the source sounding.

Use **New**, **Rename** through the name field and **Apply changes**,
**Duplicate**, **Reset**, and **Delete** to manage scenarios. **Save / export…**
writes a versioned JSON document containing the immutable baseline, named
perturbations, and provenance; **Open…** restores it.

The **T / Td grid** evaluates bounded surface-temperature and moisture-layer
dewpoint deltas. A sweep contains at most 121 cells, runs outside the GUI thread,
and caches completed results. **Cancel** leaves those cells usable. Click a
completed cell to create and open the corresponding hypothetical scenario.

## Forecast verification

The **Verify** selectors intentionally separate loaded data into forecast,
observation, analysis, and reanalysis classes. Only a forecast can occupy the
forecast side and only a true observed sounding can occupy the observation side;
analyses and reanalyses are counted and explained but excluded.

Before **Verify pair**, set:

- an optional observation station identifier;
- the permitted valid-time mismatch (0–180 minutes);
- maximum forecast-point to station distance;
- maximum bracketing gap for vertical interpolation; and
- an optional UTC **forecast as-of cutoff**.

The matcher requires forecast initialization metadata and rejects a run initialized
after its valid time or after the declared as-of cutoff. It also rejects forecast
data whose recorded availability is later than that cutoff. No later model run is
silently treated as an earlier forecast.

For a matched pair, the vertical table is anchored to observed heights within
observed coverage and above the higher terrain. Forecast temperature, dewpoint,
`u`, and `v` are linearly interpolated only between bracketing levels no farther
apart than the chosen maximum. There is no vertical extrapolation. The diagnostics
table uses the same metrics backend on both profiles; unsupported parcel or shear
values remain unavailable with a reason.

Each attempted pair—matched or rejected—can be retained. Select the cases to use
in the aggregate table. Aggregates group by model and forecast lead and report
bias, MAE, RMSE, sample count, matched count, rejected count, and the matching
rules. Invalid pairs do not contribute numeric errors. **Save…** and **Open…**
round-trip versioned JSON; **Export CSV…** writes provenance, rejection reasons,
vertical errors, and diagnostic errors.

To acquire another observation, **Load observed sounding…** returns to the
existing Station Map workflow. It does not introduce a second sounding provider.

## Observed VAD/VWP winds

The **Observed Winds** tab is wind-only. It never constructs thermodynamic
profiles from radar data.

- **Retrieve latest public VWP** requests the NWS RPCCDS NEXRAD product 48
  `sn.last` file for the entered radar. This is a recent-product path, not a
  historical archive selector. **Cancel** cooperatively stops the bounded
  background response read; no partial product is imported.
- **Import…** reads NEXRAD Level III product 48 through MetPy, or this project's
  portable wind-profile JSON/CSV. Historical product files must be obtained from
  a documented archive and imported; automatic NCEI archive retrieval is not
  claimed.
- **Save series…** writes versioned JSON; **Export current CSV…** writes the
  selected profile's levels, units, quality, location, times, and provenance.

The tab steps chronologically through observation times and overlays the selected
observed wind vectors against a loaded model hodograph. Its table reports MSL and
AGL heights when radar elevation permits, observed/model `u` and `v`, residual
quality, coverage, location, UTC observation time, retrieval age, source URL, and
attribution.

Layer shear is calculated only when both requested AGL bounds lie inside accepted
coverage. Storm-relative quantities additionally require **Use explicit storm
motion**, both vector components, and a non-empty source such as “Bunkers RM from
HRRR.” Sparse, missing, or poor-quality levels cannot masquerade as a complete
layer.

## Optional GOES and surface context

The Station Map and Forecast Model map each include an optional environmental
context section. These controls are off until selected, so provider failure cannot
block ordinary sounding work.

**GOES satellite** reads NOAA's public GOES ABI Level-2 Cloud and Moisture Imagery
files from the GOES-18 or GOES-19 Open Data bucket. Choose visible channel 2
(0.64 µm, 0.5 km native resolution) or clean infrared channel 13 (10.3 µm, 2 km
native resolution). The adapter selects the nearest actual scan within the
declared tolerance, geolocates the fixed-grid source before drawing it on the map,
and reports requested time, selected scan time, native resolution, map coverage,
age, source URL, and NOAA attribution. A time or coverage mismatch is shown as
unavailable rather than replaced by “latest.”

**Surface dewpoint + wind** follows the NWS API point-to-station links, requests a
narrow observation window, and retains each station's actual time. Select sparse,
normal, or dense decluttering. The map draws dewpoint in °C and meteorological wind
barbs in knots, marks stale matches, and reports discovered, matched, stale,
unmatched, and decluttered counts with NWS attribution. Missing wind or dewpoint
remains missing.

Primary provider references:

- [NWS API documentation](https://www.weather.gov/documentation/services-web-api)
- [NOAA GOES on AWS / NODD](https://registry.opendata.aws/noaa-goes/)
- [NWS WSR-88D product specification, including VWP display behavior](https://www.weather.gov/media/roc/Documentation/2620003N.pdf)
- [NWS WSR-88D tabular product format and VWP field units](https://www.weather.gov/media/roc/Documentation/2620003T.pdf)
- [NCEI NEXRAD Level II and Level III archive](https://www.ncei.noaa.gov/products/radar/next-generation-weather-radar)

## Briefings and animations

Use **Share** to select loaded soundings. **Export HTML…** creates a self-contained
briefing with embedded PNG pixels from the actual sounding viewer, the current map
context when active, the values currently displayed in Compare and Trends, notes,
source/run/valid timestamps, requested and selected points, scenario labels,
ensemble coverage, attribution, and limitations. **Export PDF…** writes the same
briefing as a real standalone PDF. Capturing several soundings temporarily changes
the displayed collection and restores the original collection and times afterward.

For GIF output, choose **Forecast timeline** or **Successive runs at focused valid
time**, include/exclude individual frames, set frame duration, and choose **Export
GIF…**. All available frames use the same captured viewer dimensions and a visible
run/valid/lead timestamp. A missing exact profile becomes a labelled missing frame;
it is not interpolated. Encoding runs in a cancellable worker and atomically
replaces the destination only after a complete GIF is valid.

## Historical replay and portable cases

Use **Replay → Build package** to group the current sounding session, analysis
notes, scenarios, verification cases, observed winds, optional satellite/surface
context, and current radar/outlook/report map layers. You may also add a local
archived file or a documented HTTPS archive URL with separate event/valid and
forecaster-availability times.

The current implementation records provider capabilities without implying that a
live route is an archive. In particular, current live radar imagery is never used
as a historical substitute. Remote downloads are bounded to four workers, have
size and timeout limits, and support cooperative cancellation. A cancelled build
still creates a valid partial package containing completed assets and explicit
missing/cancelled entries.

`.sharpmod-case` is a versioned ZIP data container with a JSON manifest. Every
packaged member has a SHA-256 digest and size; opening a case validates safe member
paths, limits, manifest membership, and every digest before exposing bytes. Members
are read as data and are never imported or executed.

After **Open case…**, use previous/next, exact-time selection, playback, and speed.
The replay clock contains exact manifest times. If no packaged sounding exists at
a selected time, the viewer says so and does not substitute a nearby one. **Open
packaged soundings in this viewer** restores the ordinary portable sounding
session when it is visible.

Enable **Training mode** to reveal an asset only at or after its recorded
availability time. An asset with unknown availability is hidden and listed as a
training limitation; the app does not call that reconstruction faithful.
Ordinary `.sharpmod-session` files store only Replay control preferences—not case
payload bytes or local archive paths—so they remain lightweight.

## File formats and export directory

| Workflow | Format | Important contents |
| --- | --- | --- |
| Comparison / trend / ensemble threshold | CSV | Displayed values, member outcomes, denominators, compatibility/provenance |
| Scenarios | Versioned JSON | Immutable baseline and named perturbations |
| Verification | Versioned JSON and CSV | Pair identities/rules, rejections, vertical and diagnostic errors |
| Observed winds | Versioned JSON and CSV | Wind-only levels, quality, coverage, source/times |
| Briefing | Self-contained HTML or PDF | Embedded images, displayed results, notes, attribution |
| Animation | GIF | Fixed-size timestamped frames and embedded frame manifest |
| Analysis session | `.sharpmod-session` JSON | Decoded sounding/workspace state; no source GRIB or context rasters |
| Historical case | `.sharpmod-case` ZIP | Versioned manifest and integrity-checked data assets |

Every new save/export/open dialog starts in the application's central
`rendered_soundings` directory, following the same source-checkout and packaged
executable rules as existing exports. Choosing another destination affects only
that operation; the next dialog returns to `rendered_soundings`, including after
restart. **Open Export Folder** opens that exact directory. An unwritable directory
produces a path-specific error and never silently redirects to a home, Desktop,
Documents, or Downloads folder.

## Scientific boundaries

- Scenario output is a hypothetical perturbation, not a forecast or observation.
- Ensemble threshold agreement is not calibrated probability.
- Comparison and verification use exact horizontal/time rules and conservative
  vertical alignment; an unavailable value remains unavailable.
- VWP products contain winds only. Their algorithms, vertical sampling, and
  residual quality are not equivalent to a radiosonde.
- Surface station observations are point measurements at station elevation and may
  be asynchronous within the displayed tolerance.
- GOES map imagery is resampled for display after fixed-grid geolocation; its native
  resolution and selected scan time remain in the provenance.
- Training replay is faithful only for assets with credible availability times.
