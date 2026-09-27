# Advanced analysis and sharing

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

Every map exposes explicit **Select** and **Inspect** tools; point maps also have
**Draw box**. Select changes the intended sounding point, Inspect reads the
underlying numeric field or overlapping markers without moving it, and loading
remains a separate action. Lock can freeze the point while inspection,
navigation, and box work continue; Recent point reverses the last point move.
Hover is transient, while a pinned inspection card retains sampled and requested
coordinates, distance, units, source, run/valid time, and native grid context.
Imagery without a numeric field says so rather than deriving a value from colour.

Scroll/trackpad zoom anchors at the pointer. Middle- or right-drag pans; a true
right-click opens navigation, and a released pan never selects. Fit, Centre,
Previous/Next view, a searchable region, and the View menu provide the same
reversible actions. `V`, `I`, `B`, and `U` switch Select, Inspect, Draw box, and
Recent point only while the map has focus, so typing in a field is unaffected.

On the Forecast Model map, **Box…** and **Draw box** arm the same rectangle
state. Shift-drag draws from any tool. A committed rectangle has handles,
whole-box movement, numeric bounds, Reset, domain/grid coverage, and an explicit
extraction review; changing a run/hour does not itself download. Saved areas are
source-neutral and restore bounds without changing the selected model.

The layer list states paint order, visibility, opacity, actual time, and typed
loading/offline/no-data/failed/cancelled state. Numeric fields can lock one
legend range across panels or times. The persistent map header distinguishes
requested and displayed times, run/lead, observation time, and secondary
retrieval time. Historical mode holds the correctly labelled prior frame and
suppresses latest-only radar until Live is explicitly restored. Failed layers
retain independent working layers and offer a scoped retry.

## Opening the analysis workspace

Open a sounding, then choose **View → Analysis Workspace** or press
`Ctrl+Shift+A`. The workspace uses the soundings already loaded in that viewer.
Its four top-level tabs are **Trends**, **Compare**, **Ensemble**, and
**Animation**.

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

The upper comparison view supports two or four identity-backed slots. Each can
show a full Skew-T, hodograph, or the simplified difference view using the
established scientific renderer; unavailable profiles keep their assigned slot.
The reference and slot colours remain stable, axes/cursor heights are linked by
default, and unlinking is stated explicitly. Missing vertical layers remain
gaps. The lower difference
view uses conservative common-height alignment for temperature, dewpoint, and
wind components; drawing interpolation does not alter the scientific table.
Temperature and wind differences have separate labelled axes. **Fixed ranges**
holds one scale per compatible family across slots/times until Refit. Reference,
slot assignment, chart type, layout, scale lock, metric columns, splitter, and
current tab restore with an analysis session. **Export CSV…** includes the
selected values, compatibility issues, and provenance; image export renders the
selected slots at chosen pixels without resizing the live view.

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
Cancelling retains exact members/times already completed. Retry uses the frozen
definition and requests only failed or unfinished diagnostics; replacing the
source ensemble invalidates the old result rather than relabelling it.

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

## Animations

Use **Animation** and choose **Forecast timeline** or **Successive runs at focused
valid time**, an inclusive first/last range, and individual frame checkboxes.
Choose an exact pixel/aspect preset or custom dimensions independently of the
window, and set milliseconds per frame. The output-frame count and selected
frame preview show the configured composition, source/time labels, frame theme,
and gaps. **Show missing/time-gap cards** is the default; **Stop before the first
gap** truncates the sequence; **Fail if any gap exists** refuses it. A missing
exact profile is never interpolated, and a jump beyond the smallest source
interval receives a visible discontinuity card rather than silently joining
times. The same ordered frames are captured at export time; encoding runs in a
cancellable worker and atomically replaces the destination only after a complete
GIF is valid. **Open file**, **Open folder**, **Copy path**, and **Recent exports…**
appear on the Animation page after completion.

## File formats and export directory

| Workflow | Format | Important contents |
| --- | --- | --- |
| Comparison / trend / ensemble threshold | CSV | Displayed values, member outcomes, denominators, compatibility/provenance |
| Animation | GIF | Chosen-size timestamped frames, gap policy, and embedded frame manifest |
| Map / field / locator figure | PNG | Captured extent, selection, layer order/opacity, scales, requested/actual time, source, attribution |
| Analysis session | `.sharpmod-session` JSON | Decoded sounding/workspace state; no source GRIB or context rasters |

Every new save/export/open dialog starts in the application's central
`rendered_soundings` directory, following the same source-checkout and packaged
executable rules as existing exports. Choosing another destination affects only
that operation; the next dialog returns to `rendered_soundings`, including after
restart. **Open Export Folder** opens that exact directory. An unwritable directory
produces a path-specific error and never silently redirects to a home, Desktop,
Documents, or Downloads folder.

## Scientific boundaries

- Ensemble threshold agreement is not calibrated probability.
- Comparison uses exact horizontal/time rules and conservative
  vertical alignment; an unavailable value remains unavailable.
- Surface station observations are point measurements at station elevation and may
  be asynchronous within the displayed tolerance.
- GOES map imagery is resampled for display after fixed-grid geolocation; its native
  resolution and selected scan time remain in the provenance.
- Cached map imagery may remain useful offline, but its actual time and stale or
  offline state stay visible; an offline refresh is never labelled current.
