# Design system

The React shell follows the local SoundScopepy UI: a compact sidebar,
a forecast configuration strip, a contained map, an hour rail, and a fixed
point-details pane. The source reference was SoundScopepy's
`apps/web/src/components/app-shell.tsx`, forecast configuration and timeline,
product selector, point details, and the final overrides in `globals.css`.
The interface keeps SHARPpy Reimagined branding and its current data services.

## Layout and navigation

The five workspaces are Overview, Model Viewer, Soundings, Data Status, and
Settings. Analysis, Compare, and Ensemble were removed at the user's request.
The shared model catalogue offers HRRR, RAP, NAM, NAM 3km CONUS, RDPS,
HRW WRF-ARW, HRW WRF-NSSL, HRW FV3, RRFS, REFSm1 through REFSm5, GFS, ECMWF,
and ECMWF 9km.
REFS choices fetch fixed individual members without an ensemble workspace.
Original NAM offers its 34 supported map products on the native 12 km grid,
with three-hour forecast steps through F084 and its own coverage outline.
ECMWF uses the Open-Meteo Single Runs IFS 0.25-degree adapter. The backend's
per-cycle limits constrain both forecast selectors and the hour rail.
ECMWF 9km uses the direct IFS 0.1-degree open-data feed, combining pressure,
surface and same-run analysis terrain files. Its suggested run allows for the
additional two-hour publication delay.
Choices outside this list resolve through the selectors' normal fallback.
Both model selectors group CONUS and Global models, with RDPS under Regional.
Search keeps these groups and hides sections with no matching models.

Soundings has a separate ERA5 source with a UTC date, all 24 analysis hours,
and a station or typed point. It calls the existing CDS pressure-level and
surface extractor, preserving terrain QC and reporting the snapped grid point.
ERA5 entries are labelled reanalysis without a forecast hour. The date defaults
to six days ago because ERA5T arrives about five days late; the backend's CDS
profile and dataset terms must be configured before downloading.

The header is 56 px high. The sidebar is 232 px wide, or 58 px collapsed.
The viewer uses 8 px gutters, a 54 px configuration strip, a contained map,
a 58 px timeline, and a 304 px details pane. Point details and Layers share
that pane; selecting a station or forecast point brings Point details forward.
Panel contents scroll independently without covering the map or timeline.

At 1120 px the sidebar becomes a navigation dialog. Below 760 px the map and
details stack. The map stays mounted and inert while another workspace is
active; returning preserves its camera and selection.

## Forecast interaction

Model, run, and hour use one native modal dialog with three steps. Parameter
selection has searchable categories and product descriptions. Escape closes
the dialog and returns focus to its trigger. Run labels distinguish suggested
cycles and cycles that may still be publishing.

The configuration strip, timeline, map panels, and point-sounding action all
use `useForecast`. Hours come from the provider catalogue, including sparse
steps and cycle-specific limits. Changing a run clamps the current hour to
the supported range. The hour rail supports arrow keys, Home, and End; manual
selection pauses playback. Playback speed and looping are shared with Settings.

A model without map fields says so and still permits its supported point
soundings. Loading, unavailable, out-of-domain, missing, and below-palette
values remain distinct. Current radar and observed data retain their actual
provider timestamps rather than being presented as future forecast fields.

## Appearance

UI surfaces are opaque. Dark mode uses #141516 for the page, #1d1f21 for
panels, #24272a for raised controls, and #f2f2f2 for primary text. Blue actions
and the orange brand mark follow the reference. Borders and small radii
separate controls without blurred content behind them.

Inter is bundled locally in `frontend/public/fonts/` with its SIL license.
Body text is 14 px, controls 12–13 px, and scientific readouts use tabular
digits. CSS transforms do not scale UI text. Map label rendering and palettes
remain owned by the existing scientific renderer.

Settings supports System, Light, and Dark appearance. Theme, sidebar collapse,
playback speed, and looping persist in `uiStore`; navigation dialogs do not.
Graphics profiles still control map rendering, not UI transparency.

| File | Responsibility |
|---|---|
| `src/styles/tokens.css` | Colours, typography, spacing, dimensions, theme overrides |
| `src/styles/base.css` | Font face, reset, opaque panel material, focus and typography |
| `src/ui/Controls.tsx` | Existing action, toggle, range, status, and panel controls |
| `src/app/Shell.tsx` | Header, sidebar, contained viewer, point/layer tabs, mobile navigation |
| `src/features/toolbar/` | Forecast setup, parameter selection, search and backend health |
| `src/features/timeline/` | Hour rail, animation controls, valid time |
| `src/stores/uiStore.ts` | Appearance and playback preferences |
| `src/map/canvasTokens.ts` | Scientific map colour and label tokens |

The older glass components and their isolated compatibility CSS remain for
legacy callers; the active shell uses the opaque panel material.

## Verification

From `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, and
`npm run build:browser`. Focused checks cover forecast limits and sparse hours,
keyboard selection, ensemble exclusion, safe model fallback, sidebar
navigation, and preference persistence. Browser checks cover the actual
provider-backed viewer and responsive layout. Packaged Tauri and hosted CI
remain separate validation lanes.
