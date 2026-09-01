# Open-Meteo Forecast-Model Soundings Implementation Plan

**Status:** Partially implemented. The Qt-free provider core is delivered and
wired into `model_extract`; see [Implementation status](#12-implementation-status)
for exactly what landed, what the implementation corrected in this plan, and what
is still outstanding. No dependency change, published key, commit, push, or
release has been made.

**Scope reduction:** one model is enabled, DWD ICON Global at 11 km
(`icon_global`), not the five-candidate pilot in section 5. The pilot list is
retained below as the expansion backlog.

**Correction:** this plan was written around ECMWF IFS. The live capability audit
proved that model cannot work here — Open-Meteo serves ECMWF IFS with no
pressure-level fields at all — so it is withheld and ICON Global took its place.
See [the audit](#the-live-capability-audit) for the evidence and the full
per-model matrix. Wherever sections 1 through 11 name ECMWF as the pilot model,
read ICON Global instead.

**Goal:** Add new deterministic forecast models through Open-Meteo's
pressure-level API, produce the same verified SHARPpy Reimagined sounding
contract as the existing Herbie and ECCC paths, minimize billable requests, and
guarantee that distributed copies make direct requests under each user's own
Open-Meteo access rather than a project-owned shared credential.

**Architecture summary:** Keep `sharpmod.tools.model_extract` as the public
facade. Add one Qt-independent Open-Meteo point-provider adapter, route exact
model runs through the Single Runs API, normalize one response into the
existing surface-merged NPZ contract, and reuse the existing model-hour and disk
cache ownership. Open-Meteo access is always local and direct: free mode uses
the user's IP-based public allowance; customer mode requires that user to
provide their own API key at runtime. There is no SHARPpy proxy and no key in
the repository, package, GitHub Actions, logs, cache metadata, or generated
soundings.

**Primary references, checked 2026-08-30:**

- [Open-Meteo Forecast API documentation](https://open-meteo.com/en/docs)
- [Open-Meteo Single Runs API](https://open-meteo.com/en/docs/single-runs-api)
- [Open-Meteo pricing and usage limits](https://open-meteo.com/en/pricing)
- [Official Forecast API OpenAPI specification](https://github.com/open-meteo/open-meteo/blob/main/openapi/forecast.yml)

---

## 1. Non-negotiable constraints

1. **Do not promise an unlimited free API.** The current free tier is limited
   to 600 weighted calls/minute, 5,000/hour, 10,000/day, and 300,000/month.
   Customer plans remove minute/hour/day limits but still have plan-dependent
   monthly budgets. The implementation target is zero avoidable calls, cache
   hits for repeats, and graceful handling before or after a `429` — not
   bypassing Open-Meteo's controls.
2. **Every installation owns its requests.** The application calls Open-Meteo
   from the user's machine. No SHARPpy-hosted relay, Cloudflare Worker, GitHub
   proxy, shared API key, or baked-in token is permitted.
3. **Free access has no API key.** In `free-direct` mode, "their own API" means
   the request originates from the user's network and consumes that IP's public
   allowance. In `customer-direct` mode, the user supplies their own
   subscription key.
4. **Use exact runs, not a stitched forecast.** The existing UI selects a model
   run and forecast hour. Use
   `https://single-runs-api.open-meteo.com/v1/forecast` with `run=...` rather
   than the operational "best match" time series.
5. **Do not dynamically expose every advertised model.** Enable only explicit,
   deterministic model identifiers that pass a pressure-level, surface-field,
   domain, timing, and physical-QC capability gate.
6. **Preserve the existing verified ground row.** A pressure ladder without
   surface pressure, model-grid height, 2-m temperature/moisture, and 10-m
   winds must be rejected, not rendered as if its lowest isobar were ground.
7. **Keep provider output honest.** Open-Meteo pressure levels and some time
   steps are normalized/interpolated products, not every native model level.
   Provider, model identifier, selected grid point, available levels, and
   interpolation/cadence facts must remain visible in metadata and the UI.

## 2. Current repository boundaries to preserve

- `sharpmod/tools/model_extract.py` is the CLI/library facade and model catalog.
- `sharpmod/eccc_geomet.py` is the closest point-provider precedent: it
  normalizes provider data without requiring GRIB/ecCodes and writes the same
  portable sounding format.
- `sharpmod/model_surface.py` owns the verified surface merge and removal of
  below-ground isobars.
- `sharpmod/model_hour_cache.py` owns in-process single-flight and lease-aware
  reuse.
- `sharpmod/model_disk_cache.py` owns the bounded persistent cache and must
  become transport-neutral rather than assuming every reusable payload is GRIB.
- `sharpmod/gui_workers.py` performs background retrieval; `sharpmod/gui_picker.py`
  owns provider/model/run/F-hour selection; `sharpmod/gui_timeline.py` and
  `sharpmod/batch_extract.py` own multi-hour and multi-request workflows.
- Headless render output remains transient after the PNG is produced. GUI data
  remains alive until its viewer closes. Adding Open-Meteo must not regress
  those two different lifecycle rules.

## 3. Architecture decisions

### ADR-1: Direct user-to-Open-Meteo requests

**Decision:** Ship only a local client. Do not build or document a shared
backend.

**Why:** It makes ownership unambiguous, prevents one public repository key
from becoming a quota and billing bottleneck, and matches desktop distribution.

**Trade-off:** A user's public-IP quota can still be shared with their other
Open-Meteo usage, and a client cannot hide a credential from the user who owns
the machine. The application can prevent accidental project-wide sharing, but
cannot turn a client-side key into an unknowable secret.

### ADR-2: Single Runs API instead of Forecast API

**Decision:** Use the Single Runs host and send exactly one explicit Open-Meteo
`models` identifier plus the selected `run`.

**Why:** It preserves the existing run + F-hour semantics and produces
reproducible, immutable cache entries. The normal Forecast API stitches the
newest runs and can no longer prove which initialization produced a value.

**Trade-off:** A newly initialized run is unavailable until Open-Meteo has
ingested it, and most archived models begin on 2026-04-02. The GUI must encode
conservative publication lag and show an actionable unavailable-run error
without silently substituting another cycle.

### ADR-3: Vetted static capability manifest

**Decision:** Version a small `OpenMeteoCapability` manifest in the codebase.
Each entry contains the SHARPpy key, provider model identifier, label, domain,
cycle hours, native output cadence, forecast horizon, pressure levels, archive
start, and enabled/withheld reason.

**Why:** The generic API advertises models and variables independently, but
that does not prove every model supplies a complete sounding at every level.
A dynamic dropdown would expose partial or physically invalid products.

**Trade-off:** Model additions and upstream changes require a capability audit
and release. This is preferable to silently changing scientific data contracts.

### ADR-4: Runtime-only user credentials

**Decision:** Phase 1 reads customer credentials only from
`SHARPMOD_OPENMETEO_API_KEY`. It stores non-secret mode/preferences in
`QSettings`, but never the key. A later GUI credential editor may use the OS
keychain; it must not fall back to plaintext INI storage.

**Why:** Environment-only secret injection works in CLI, packaged GUI, local
development, and automation without adding a keyring dependency or putting a
secret under the project directory.

**Trade-off:** Customer users must configure an environment variable before
launch. Free-direct remains zero-configuration.

### ADR-5: Core sounding fields first; omega later

**Decision:** Phase 1 requests temperature, dew point, wind speed, wind
direction, and geopotential height at vetted pressure levels. Fill `omeg` with
the normal missing value.

**Why:** Open-Meteo vertical velocity is geometric velocity for applicable
models, while SHARPpy's `omeg` is Pa/s. Omitting this optional family avoids a
scientifically wrong pass-through and removes one variable per pressure level
from weighted usage.

**Revisit trigger:** Add vertical velocity only after a documented,
thermodynamically tested conversion to pressure velocity and a measured quota
impact.

## 4. Access modes and credential isolation

Define a frozen, Qt-independent `OpenMeteoAccess` value with three modes:

| Mode | Endpoint | Key behavior | Intended use |
|---|---|---|---|
| `free-direct` | `single-runs-api.open-meteo.com` | No key accepted or sent | Personal/non-commercial public use; user's IP quota |
| `customer-direct` | `customer-single-runs-api.open-meteo.com` | Requires `SHARPMOD_OPENMETEO_API_KEY` | User's own paid subscription |
| `self-hosted` | Explicit local/admin URL | Phase 1 never forwards the customer key | User-operated Open-Meteo server |

Security rules:

- Hard-code and compare exact official hosts before attaching `apikey`.
- Reject customer mode when the key is absent; reject a key in free or
  self-hosted mode so it cannot leak to the wrong host.
- Construct the authenticated query only inside the transport boundary.
  Request/cache objects contain a redacted parameter set.
- Sanitize `requests` exceptions and response URLs before logging or displaying
  them. Replace the value of `apikey` with `<redacted>` everywhere.
- Never put the key or authenticated URL in `.cache.json`, NPZ sidecars,
  crash logs, telemetry, exception messages, snapshots, tests, screenshots, or
  support bundles.
- Use only a one-way truncated SHA-256 credential scope internally when usage
  counters must distinguish two customer keys. Do not expose that identifier
  in normal logs or UI.
- Keep CI contract tests fully mocked. Live tests are local/manual opt-in and
  use the executing user's access; no repository-level Open-Meteo secret is
  created.

## 5. Model catalog rollout

Use namespaced SHARPpy keys such as `openmeteo-icon-global` so they cannot be
mistaken for or overwrite an existing native/Herbie route.

### Pilot candidates

Enable the adapter first with a small, globally useful set that is not already
a normal selectable native route:

| Candidate API identifier | Proposed label | Gate before enabling |
|---|---|---|
| `icon_global` | Open-Meteo ICON Global | Complete core fields, surface row, native cadence, global-point matrix |
| `jma_gsm` | Open-Meteo JMA GSM | Same, including tropical and dateline points |
| `bom_access_global` | Open-Meteo ACCESS-G | Same, including Southern Hemisphere point |
| `cma_grapes_global` | Open-Meteo CMA GRAPES Global | Same, including moisture/height completeness |
| `ukmo_global_deterministic_10km` | Open-Meteo UKMO Global 10 km | Same plus licence/access verification |

The identifiers are candidates, not promises. Withhold any one that fails the
live contract and record a reader-facing reason in `unsupported_models()`.

### Expansion candidates

After the pilot passes, audit explicit regional models such as ICON-EU/D2,
JMA MSM, KMA GDPS/LDPS, ARPEGE/AROME, MET Norway Nordic, KNMI and DMI
HARMONIE-AROME, UKMO UK 2 km, MeteoSwiss ICON, GeoSphere AROME, and
ItaliaMeteo ICON-2I.

Do not initially expose:

- `best_match` or any `*_seamless` identifier, because it can combine models;
- ensemble members or ensemble means, which need separate member semantics;
- duplicate Open-Meteo GFS/HRRR/NAM/IFS/GDPS routes until a deliberate
  provider-fallback UX is designed;
- a model whose pressure rows have missing temperature, moisture, wind, or
  geopotential height;
- interpolated hourly choices when the manifest says the source model's native
  output cadence is 3 or 6 hours. Default the picker to native times only.

## 6. Request contract

For one model/run/point/F-hour:

1. Compute `valid_time = run_time + fxx` in UTC.
2. Send one HTTP request, never one request per level or field.
3. Send:
   - `models=<one explicit deterministic model>`
   - `run=YYYY-MM-DDTHH:MM`
   - `latitude` and `longitude`
   - `start_date=<valid UTC date>` and `end_date=<same date>`
   - `timezone=GMT` and `timeformat=unixtime`
   - `cell_selection=nearest` and `elevation=nan` to avoid terrain
     downscaling and retain the selected model grid
   - `temperature_unit=celsius` and `wind_speed_unit=ms`
   - surface fields: `surface_pressure`, `temperature_2m`, `dew_point_2m`,
     `wind_speed_10m`, and `wind_direction_10m`
   - the five core pressure-level families only at that model's vetted levels.
4. Select the exact `valid_time` from the returned hourly timestamps. Reject a
   missing or duplicate timestamp instead of taking an array position on faith.
5. Record top-level returned latitude, longitude, and elevation as the selected
   grid point/height. Live acceptance must first prove that `elevation=nan`
   returns a usable native grid-cell height for the model; otherwise withhold
   the model until an authoritative surface-height source is available.
6. Convert pressure winds from m/s + meteorological direction into `u/v` in
   m/s and `wspd` in knots. Keep temperature/dew point in Celsius and height in
   metres.
7. Remove any level where a core field is null/non-finite, sort/deduplicate
   pressure bottom-to-top, then call `merge_surface_level(...)`. Reject fewer
   than the agreed minimum usable levels, a profile that does not reach at
   least 300 hPa, or any existing `basic_sounding_qc` failure.
8. Set optional omega and surface-vorticity fields missing in Phase 1; never
   invent them.

Normalized metadata must include:

- SHARPpy model key/label and exact Open-Meteo model identifier;
- provider/endpoint class, but not the authenticated URL;
- requested and selected coordinates plus selected grid elevation;
- run, valid time, F-hour, pressure levels requested/retained, and native
  cadence;
- `surface_contract_version`, surface pressure, removed below-ground count, and
  QC result;
- cache hit/single-flight reuse, request variable count, estimated weighted
  usage units, and response generation time when supplied;
- attribution to Open-Meteo and the originating national model provider.

## 7. Request minimization and rate-limit safety

Apply optimizations in this order:

1. **No background Open-Meteo probe.** The current availability worker must not
   spend a second request for Open-Meteo. Use manifest timing plus cache state;
   perform network I/O only after Fetch/Timeline/Batch is explicitly invoked.
2. **No automatic cycle spray.** If a run is not ingested, show the reason and
   offer an earlier configured cycle. Do not silently try four prior cycles.
3. **One request per uncached slice.** Request all needed variables together
   for the smallest UTC date window containing selected valid hours.
4. **Single-flight identical work.** Concurrent identical
   model/run/F-hour/point/schema requests share one future and one response.
5. **Persistent immutable cache.** Single-run data does not change after a
   successful ingest. Store the normalized provider payload atomically in the
   bounded model cache and reuse it across restarts until evicted by size/age or
   cache-contract version.
6. **Negative cache.** Cache "run not available yet" briefly (for example
   2–5 minutes) so repeated clicks do not hammer the same missing run. Do not
   negative-cache authentication or malformed-contract failures as availability.
7. **Timeline coalescing.** Fetch several selected F-hours from one run in one
   request per minimal date window, then materialize individual point datasets
   and cache entries. Do not call the endpoint once per timeline frame.
8. **Batch coordinate coalescing after measurement.** Open-Meteo accepts
   comma-separated coordinates. Add bounded same-model/run/time batches only
   after live tests prove response ordering and current weighted accounting.
   Treat each location conservatively in the local usage estimate; fewer HTTP
   requests do not automatically mean fewer billable units.
9. **No Open-Meteo prefetch by default.** The existing next-hour prefetch
   preference must not apply unless a future Open-Meteo-specific setting is
   explicitly enabled and budgeted.
10. **Connection reuse and bounded retry.** Reuse one `requests.Session`,
    accept gzip, set connect/read/deadline timeouts, retry only transient
    connection/`5xx` failures with jitter, and honor `Retry-After` for `429`.
    Never retry `400`/`401`/`403` blindly.

Track estimated weighted usage using the provider's documented rule: more than
10 variables and more than 14 days count fractionally/multiplicatively. Treat
multiple coordinates as separate location cost until Open-Meteo documents and
tests a safer rule. Persist atomic rolling counters outside the repository,
scoped to free access or the hashed customer credential.

The local governor should reserve headroom rather than run at the published
edge (for example 80% of configured minute/hour/day/month limits). When the
next request would exceed the local safety budget, queue it until the shortest
reset when reasonable or return a clear message with the reset time. A `429`
from Open-Meteo remains authoritative because the user may consume the same
IP/key outside this application.

## 8. Cache contract changes

Make the current cache source-neutral:

- Add `provider` and `payload_kind` to `ModelHourKey`/entry metadata or include
  the provider in the namespaced model key and payload metadata.
- Add a validated `openmeteo-point-v1` payload containing normalized arrays,
  selected point, exact run/valid time, model identifier, and non-secret
  provenance.
- Extend `ModelDiskCache` with provider payload validation/loading; do not route
  an Open-Meteo cache entry through `valid_grib_paths()`.
- Include adapter schema, surface-contract version, model manifest version,
  units, and requested field set in the cache fingerprint. Bump the contract
  when any normalization rule changes.
- Keep API keys, key hashes, authenticated URLs, and usage-account details out
  of cache payloads. The same meteorological cache may be reused after a user
  switches from free to their customer endpoint if the provider/model/run/grid
  contract is identical.
- Preserve current lease markers, atomic writes, cache manager visibility,
  pin/delete behavior, shutdown cleanup, and viewer-lifetime ownership.

## 9. Implementation tasks

### Task 1: Freeze the provider contract and capability audit

**Files:**

- Create: `sharpmod/openmeteo.py`
- Create: `sharpmod/tests/test_openmeteo_catalog.py`
- Modify: `sharpmod/tools/model_extract.py`

- [ ] Define `OpenMeteoCapability` and namespaced candidate aliases.
- [ ] Build the exact core/surface variable list from vetted pressure levels.
- [ ] Add withheld reasons and make `available_models()` expose only passing
      candidates.
- [ ] Add provider-aware `requires_grib_runtime()`,
      `point_only_provider()`, `provider_capability()`, domain checks, cycles,
      native F-hours, and archive start.
- [ ] Unit-test that no `best_match`/seamless/ensemble or duplicate native route
      is accidentally selectable.
- [ ] Run a one-time local capability matrix against recent completed runs and
      representative in-domain points; save results as review evidence, not a
      runtime-generated catalog.

### Task 2: Implement access resolution and secret-safe transport

**Files:**

- Create: `sharpmod/openmeteo_access.py`
- Create: `sharpmod/tests/test_openmeteo_access.py`
- Modify: `.gitignore` only if a new local access file is introduced

- [ ] Resolve free/customer/self-hosted mode without importing Qt.
- [ ] Require runtime user credentials for customer mode and exact-host
      allowlisting before query injection.
- [ ] Implement query/exception/log redaction and test every error path.
- [ ] Prove no key appears in `repr`, sidecar metadata, cache metadata, progress
      callbacks, or captured logs.
- [ ] Add a deterministic HTTP session factory so unit tests use fake responses
      and make zero network calls.

### Task 3: Fetch and normalize one point sounding

**Files:**

- Modify: `sharpmod/openmeteo.py`
- Modify: `sharpmod/tools/model_extract.py`
- Create: `sharpmod/tests/test_openmeteo.py`
- Modify: `sharpmod/tests/test_model_extract.py`

- [ ] Build the exact Single Runs request contract above.
- [ ] Validate status/content type/JSON schema, API errors, timestamp alignment,
      units, model/grid identity, and all core fields.
- [ ] Normalize to `OpenMeteoPointDataset`, merge the verified surface, remove
      below-ground/invalid levels, and run physical QC.
- [ ] Route `extract()` and `probe()` without importing Herbie/ecCodes.
      `probe()` must be cache/manifest-only unless explicitly invoked as a live
      diagnostic.
- [ ] Write atomic NPZ + JSON using the existing portable contract and include
      complete non-secret provenance/attribution.
- [ ] Cover null levels, high terrain, dateline longitude, missing height,
      wrong valid time, wrong model response, response-list shape, cancellation,
      and write failure.

### Task 4: Add persistent single-flight cache and usage governor

**Files:**

- Modify: `sharpmod/model_hour_cache.py`
- Modify: `sharpmod/model_disk_cache.py`
- Modify: `sharpmod/openmeteo.py`
- Create: `sharpmod/openmeteo_budget.py`
- Create: `sharpmod/tests/test_openmeteo_budget.py`
- Modify: `sharpmod/tests/test_model_hour_cache.py`
- Modify: `sharpmod/tests/test_model_disk_cache.py`

- [ ] Serialize/validate/load `openmeteo-point-v1` atomically.
- [ ] Make concurrent identical requests produce exactly one HTTP call.
- [ ] Make a second process launch reuse the immutable payload with zero calls.
- [ ] Add bounded negative availability caching.
- [ ] Estimate/persist rolling weighted units and reserve configurable headroom.
- [ ] Test minute/hour/day/month boundaries with a fake clock.
- [ ] Test `Retry-After`, bounded `5xx` retry, cancellation while waiting, and
      cleanup of partial/corrupt cache entries.

### Task 5: Integrate CLI and GUI without shared credentials

**Files:**

- Modify: `sharpmod/gui_settings.py`
- Modify: `sharpmod/gui_picker.py`
- Modify: `sharpmod/gui_workers.py`
- Create: `sharpmod/tests/test_gui_openmeteo.py`

- [ ] Group or label models by provider so "Open-Meteo ICON Global" cannot be
      mistaken for a native route.
- [ ] Show access mode and configured/not-configured state; never display the
      key.
- [ ] Disable unavailable dates/F-hours using the capability manifest and
      conservative publication lag.
- [ ] Skip native GRIB runtime preflight and background availability fan-out for
      Open-Meteo models.
- [ ] Keep Fetch enabled in free-direct mode and show an actionable setup error
      only when customer mode lacks the user's key.
- [ ] Preserve cancellation, progress, failure cleanup, persistent cache
      manager, and viewer-close lifecycle behavior.
- [ ] Add CLI documentation/status output that identifies free direct,
      customer direct, or self-hosted access without printing secrets.

### Task 6: Coalesce timeline and batch requests

**Files:**

- Modify: `sharpmod/gui_timeline.py`
- Modify: `sharpmod/batch_extract.py`
- Modify: `sharpmod/openmeteo.py`
- Modify: `sharpmod/tests/test_batch_extract.py`
- Create or modify focused timeline tests

- [ ] Add a provider-native `fetch_times(...)` that returns several requested
      valid hours from the smallest date window.
- [ ] Materialize/cache one dataset per selected F-hour after a single response.
- [ ] Group identical concurrent requests across GUI timeline and batch paths.
- [ ] Measure bounded multi-coordinate requests separately; enable only if
      ordering, partial failures, URL size, response size, cancellation, and
      weighted usage are all deterministic.
- [ ] Assert N selected timeline hours do not produce N HTTP calls.

### Task 7: Documentation, attribution, and verification

**Files:**

- Modify: `README.md`
- Modify: `docs/USAGE.md`
- Modify: `CHANGELOG.md` only when implementation is actually delivered
- Modify: `sharpmod/tests/test_live_provider_contracts.py`
- Modify: `.github/workflows/tests.yml` only to keep live access opt-in

- [ ] Document free non-commercial limits, customer/self-hosted choices,
      per-user request ownership, and Open-Meteo/origin-provider attribution.
- [ ] Document that no project key exists and that customer users set their own
      `SHARPMOD_OPENMETEO_API_KEY` locally.
- [ ] Add mocked CI contracts and a manually enabled live matrix. Do not add a
      shared GitHub Open-Meteo secret.
- [ ] Run focused unit/integration tests, `git diff --check`,
      `sharpmod-rust-sync --check`, the complete test suite, and package build.
- [ ] For every enabled candidate, perform one fresh live sounding and one
      cache repeat; verify exact run/valid time, selected grid point, surface
      merge, pressure-level completeness, physical QC, rendered output, first
      request count, and zero-repeat request count.
- [ ] Verify customer-mode logs with a disposable test key contain no secret,
      then rotate that disposable key.

## 10. Acceptance criteria

- [ ] A distributed clone/release contains no Open-Meteo credential and has no
      route to a SHARPpy-owned proxy.
- [ ] Free-direct requests originate on the user's machine; customer-direct
      refuses to run until that user provides their own key.
- [ ] Authenticated parameters are sent only to the exact official customer
      Single Runs host and are absent from every persisted/logged artifact.
- [ ] Each enabled model produces a verified surface row plus a physically
      valid profile reaching at least 300 hPa at representative low/high terrain
      and domain-edge points.
- [ ] One cold point/F-hour fetch makes one API request; an identical repeat and
      concurrent duplicate make zero additional requests.
- [ ] A multi-hour timeline is coalesced by minimal UTC date window rather than
      one request per frame.
- [ ] Open-Meteo selection triggers no background availability or implicit
      prefetch calls.
- [ ] Local weighted-usage accounting, `Retry-After`, `429`, cancellation, and
      negative-cache behavior are deterministic under tests.
- [ ] Existing Herbie, HRRR Zarr, ECCC GeoMet, batch, cache, GUI, and transient
      render lifecycles remain green.
- [ ] README/USAGE and output metadata include required Open-Meteo and
      originating-provider attribution plus the pressure-level/interpolation
      limitations.

## 11. Explicit non-goals for the first implementation

- Hosting an API, proxy, shared cache service, or shared credential.
- Evading or attempting to defeat Open-Meteo rate limits.
- Claiming all 30+ advertised models support scientific soundings.
- Adding seamless/best-match or ensemble profiles.
- Replacing existing native model routes with Open-Meteo duplicates.
- Filling missing omega/vorticity with guessed values.
- Storing a paid key in source control, QSettings INI, NPZ/JSON, or logs.
- Starting a release before the implementation, local verification, hosted CI,
  and live provider matrix are separately requested and complete.

---

## 12. Implementation status

Recorded 2026-08-30. Everything below was verified locally with a mocked
transport; no test in the repository contacts Open-Meteo.

### Delivered

**`sharpmod/openmeteo_access.py`** — access resolution and the single transport
boundary. Modes `free-direct`, `customer-direct`, `self-hosted`. The credential
lives in a `_Secret` wrapper that redacts itself through `repr`, `str`,
f-strings, dataclass reprs, and `dataclasses.asdict`, so containment does not
depend on every call site remembering to redact. The exact-host allowlist is
re-checked inside `authenticated_params` immediately before the query leaves,
rather than trusted from construction. A key supplied for free or self-hosted
mode is refused rather than dropped or forwarded. `redact()` scrubs `apikey=`
from arbitrary text, which matters because `requests` embeds the full request
URL in its own exception messages. `credential_scope()` returns a truncated
one-way digest for usage accounting.

**`sharpmod/openmeteo.py`** — the vetted catalog plus fetch, normalize, write,
extract, and probe. One request per sounding carries all 65 hourly variables.
Units follow the existing contract exactly, including the asymmetry that `wspd`
is knots while `u`/`v` are metres per second. `omeg` is left missing per ADR-5.
Levels are the twelve the model measurably publishes between 1000 and 100 hPa,
recorded in `MEASURED_LADDERS`; `pressure_levels` is a required field with no
default, so a capability cannot inherit a ladder nobody verified. The 50 and 10
hPa levels are excluded from the candidate set entirely because nothing this
application computes reaches them.

**`sharpmod/tools/model_extract.py`** — wired through a single
`POINT_PROVIDER_KEYS` frozenset instead of adding a second literal to seven
places, plus one `ModelConfig` and the aliases `icon`, `icon-global`,
`openmeteo-icon`, `openmeteo-icon-global`, and `om-icon`. The bare `icon` key was
previously in `UNSUPPORTED_MODELS`, explaining that Herbie has no ICON loader;
that entry is removed, because a key cannot both resolve and be reported absent.
Claiming `icon` is safe in a way that re-pointing `ecmwf` would not be, since
nothing resolved it before.

Also fixed while here: `get_config` consulted this module's own
`UNSUPPORTED_MODELS` rather than the merged `unsupported_models()`, so the
Open-Meteo adapter's withheld keys were printed by `--list` under "known but not
enabled" and then answered with a bare `KeyError` when typed back. It now reads
the merged map, and a genuine typo still raises `KeyError`.

**Tests** — `test_openmeteo_access.py` (32) and `test_openmeteo.py` (73).

### What the implementation corrected in this plan

**Section 6 step 5 was wrong about the surface height, and the failure was
silent.** The plan proposed recording the provider's `elevation` as the grid
height, withholding a model if `elevation=nan` returned nothing usable. In
testing, `elevation` disagreed with the model's own mass field: a reported
terrain height of 142 m sat *above* the 1000 hPa geopotential height of 110.9 m
at a surface pressure of 1005 hPa. Prepending that ground row made height
non-monotonic and failed `basic_sounding_qc`, so the sounding was refused. The
two quantities come from different datasets and need not agree.

The ground row's height is therefore always derived by interpolating the model's
own geopotential-height profile in log pressure to the reported surface
pressure. That is self-consistent by construction. The derivation runs before
below-ground levels are filtered out, so high terrain interpolates between
bracketing levels rather than extrapolating from the bottom of the profile: a
700 hPa surface resolves to within a metre of the standard-atmosphere height.
`provider_elevation_m` is still recorded in the sidecar as a diagnostic for the
capability audit.

**Section 5's "audit then enable" ordering was softened, deliberately.** A
static gate cannot be satisfied without live calls, and shipping a manifest that
enables nothing is not useful. Instead completeness is enforced per request: a
level missing any core field is dropped, and a profile that loses its ground row
or fails physical QC is refused. An enabled model can therefore produce a clear
failure but never a quietly wrong sounding. The live capability matrix is still
required before the model is *promised* in user documentation.

**Section 7 item 1 is stronger than described.** `probe()` is manifest-only by
default and takes an explicit `live=True` to touch the network, so no background
availability check can spend a metered request.

**The level set is no longer provisional; it is measured.** The audit replaced
the assumed ladder with the twelve levels ICON Global actually publishes. Request
validation against the run's actual series is retained anyway, so a ladder that
changes upstream yields an explicit error rather than a short profile presented
as complete.

**Forecast hours follow DWD's cadence, not the provider's.** Open-Meteo will
answer hourly past F078 by interpolating its own three-hourly source, so the
manifest offers hourly only to F078 and three-hourly after it. Presenting
interpolated steps as model output would misrepresent what the sounding is.

**One thing the plan did not anticipate:** `test_every_selectable_forecast_model
_exists_in_herbie_registry` asserted every selectable model exists in Herbie's
registry. The ECCC point providers passed only by coincidence, because Herbie
also ships `gdps` and `rdps` models. It is now scoped to Herbie-backed routes,
with a companion test asserting every exempted key is a real selectable
point provider so the exemption cannot silently widen.

### The live capability audit

Twenty-seven identifiers were probed against the real service at run
2026-08-28 00Z, F012, each at a point inside its own domain. One request per
model. This is the only part of the work that touched the network.

**ECMWF IFS publishes no pressure-level data.** This is the finding that
redirected the whole feature, and it is worth stating precisely because the
failure is indistinguishable from success at the transport layer. The request
returned HTTP 200, thirteen hourly steps, a grid elevation of 201 m, and every
one of the five surface variables complete. All five pressure families —
temperature, dew point, wind speed, wind direction, geopotential height —
returned zero complete levels. Nothing in the response resembles an error, so
this could not have been caught by anything short of opening the profile. The
same is true of `ecmwf_ifs025`.

**The advertised ladders are far more optimistic than reality.** The OpenAPI
schema lists the same twenty-six pressure levels for every model. No model fills
more than twelve. These eleven levels came back empty for every single model
audited: 950, 900, 750, 650, 550, 450, 350, 275, 225, 175, and 125 hPa. The
shipped catalog had assumed twenty-four of them, which would have meant paying
for 125 variables to receive 65. Each capability now carries its measured ladder.

Twelve identifiers were confirmed usable. All twelve produced a valid ground row
and passed `basic_sounding_qc`:

| Identifier | Levels | Top | Provider elevation minus derived height |
|---|---|---|---|
| `icon_global` | 12 | 100 hPa | −0.6 m |
| `ukmo_global_deterministic_10km` | 12 | 100 hPa | −2.3 m |
| `cma_grapes_global` | 12 | 100 hPa | −1.1 m |
| `meteofrance_arome_france` | 12 | 100 hPa | +3.2 m |
| `icon_eu` | 11 | 150 hPa | +2.4 m |
| `cmc_gem_hrdps` | 11 | 100 hPa | −1.6 m |
| `icon_d2` | 10 | 200 hPa | +4.2 m |
| `meteofrance_arpege_world` | 10 | 200 hPa | +4.5 m |
| `meteofrance_arpege_europe` | 10 | 200 hPa | +4.2 m |
| `ukmo_uk_deterministic_2km` | 9 | 250 hPa | −0.6 m |
| `jma_gsm` | 8 | 300 hPa | −1.1 m |
| `jma_msm` | 8 | 300 hPa | +4.7 m |

Eleven publish no pressure levels at all, predominantly convection-allowing
models: `ecmwf_ifs`, `cmc_gem_hrdps_west`, `meteofrance_arome_france_hd`,
`meteoswiss_icon_ch1`, `meteoswiss_icon_ch2`,
`knmi_harmonie_arome_netherlands`, `dmi_harmonie_arome_europe`,
`geosphere_arome_austria`, `metno_nordic`, and `ncep_nbm_conus`. Two are too
shallow to use: `knmi_harmonie_arome_europe` at 5 levels and
`italia_meteo_arpae_icon_2i` at 6.

Three answered with a body that is not JSON, raising
`RetrievalError: Open-Meteo response was not valid JSON`: `kma_gdps`,
`bom_access_global`, and `kma_ldps`. The cause is not yet understood, so they are
withheld rather than guessed at.

**The audit vindicated the derived-ground-height decision and bounded its
disagreement.** Every usable model agreed with its own geopotential profile to
within −2.3 to +4.7 m. That is small, but it is not zero, and the sign varies by
model, so the two quantities are confirmed to be independent rather than
redundant. Deriving remains correct; the earlier 142 m against 110.9 m case was
not an outlier in magnitude but in which side of the surface it fell on.

**Two guardrails now sit at their limit.** `jma_gsm` and `jma_msm` return exactly
`MIN_USABLE_LEVELS` levels, topping out at exactly `REQUIRED_TOP_PRESSURE_HPA`.
They pass only because the top check is `min(levels) > 300.0`. One level withdrawn
upstream disqualifies them mid-season, which is why both are withheld with that
stated as the reason rather than being enabled on a technicality. `icon_global`
clears both bounds comfortably.

**The `forecast_hours` parameter is the only way to bound the series.** The
endpoint rejects `start_date`/`end_date` with `Parameter 'start_date' must not be
set`, and rejects `start_hour`/`end_hour` the same way. `forecast_hours = fxx + 1`
returns hours 0 through N−1 from the run, which is what the fetch path uses.

Evidence: `.tmp/openmeteo_capability_matrix.json`, produced by
`.tmp/audit_openmeteo_models.py`. Neither is part of the package.

### Outstanding

- **Task 4's budget governor** (`sharpmod/openmeteo_budget.py`) is not written.
  The structural protections are in place — one request per sounding, no
  background probe, no cycle spray, manifest-only availability, and
  `weighted_units()` reporting 6.5 units per sounding rather than 1 — but
  persistent rolling counters, configurable headroom, and a local queue ahead of
  a `429` are not. `OpenMeteoRateLimited` carries `Retry-After` for a caller to
  honour; nothing yet honours it automatically.
- **Task 4's cache work.** The dataset exposes `_sharpmod_source_url`,
  `_sharpmod_fields`, and `_sharpmod_transport`, so it can already participate in
  the model-hour cache's single-flight machinery. The `openmeteo-point-v1` disk
  payload and its validation are not implemented; the existing
  `valid_sounding_paths` precedent is the intended route rather than
  generalizing `valid_grib_paths`.
- **Task 5, GUI and CLI integration.** Partly live, and not by design. Because
  the model is an entry in `_CONFIGS`, it appears in the Forecast Model tab's
  model list and is fetched by the existing `_ModelFetchWorker` with no
  Open-Meteo-specific worker involved. That works, but it was not a decision --
  and it broke on first use, which is worth recording because the cause was a
  gap this plan's own refactor was meant to close.

  `POINT_PROVIDER_KEYS` replaced seven hardcoded `{"gdps", "rdps"}` literals. It
  missed an eighth, inside `_retrieve_dataset`, which is not one of the public
  seams but the Herbie retrieval helper. `extract` returns early for point
  providers, so the command line was fine; the GUI's model-hour cache calls
  `_retrieve_dataset` directly to populate a shared entry, so selecting ICON in
  the picker fell through to Herbie and failed with `module 'herbie.models' has
  no attribute 'openmeteo-icon-global'` -- Herbie blamed for a name it was never
  asked about. Both point providers now share one branch keyed on
  `POINT_PROVIDER_KEYS`, and
  `test_retrieve_dataset_routes_every_point_provider_away_from_herbie` is
  parametrized over the whole set so a provider added later cannot reintroduce
  it by being handled in `extract` alone.

  Still outstanding is the polish: picker grouping, an access-mode display, and
  date/F-hour gating driven from the manifest rather than from `fxx_values`.
  `_ERA5FetchWorker` remains the better precedent for a dedicated worker if one
  is ever wanted, since it is the non-GRIB cached-point case.
- **Task 6, timeline and batch coalescing.** `fetch_times(...)` does not exist,
  so a multi-hour timeline would still issue one request per frame.
- **Task 7, documentation.** README and USAGE are untouched. The live matrix is
  done — see [the audit](#the-live-capability-audit) — so the level set and
  pressure-level completeness are now measured rather than assumed. What remains
  unverified is the far end of the forecast horizon: the audit probed F012 only,
  so the F078 cadence change and the F120/F180 cut-offs come from DWD's published
  schedule, not from observation. Request-time validation catches a mismatch as an
  explicit error rather than a wrong sounding.
- **The three non-JSON responses.** `kma_gdps`, `bom_access_global`, and
  `kma_ldps` need their raw bodies inspected before they can be trusted or ruled
  out; two of them are global models and would otherwise be worth having.
- **Expansion is now cheap.** `MEASURED_LADDERS` holds the audited ladder for all
  twelve usable models, so enabling one is a manifest entry plus its withheld
  reason removed, with no further live calls.
