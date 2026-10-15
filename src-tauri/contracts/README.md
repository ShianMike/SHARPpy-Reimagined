# Contract fixtures

Shared JSON fixtures for the two data contracts of the Tauri app. The same
files are read by `cargo test` (`src-tauri/tests/contracts.rs`), Vitest
(`frontend`, through the `@contracts` alias), and pytest
(`sharpmod/tests/test_bridge_contract.py`), so a change on one side that the
others do not follow fails a test instead of drifting silently.

| Folder | Contract | Casing | Producer → consumer |
|---|---|---|---|
| `bridge/` | Bridge protocol payloads (`result` and `error` values) | snake_case | Python `sharpmod.bridge` → Rust `src/python/` |
| `public/` | Public DTOs returned by the Tauri commands and events | camelCase | Rust `src/dto/` → TypeScript `frontend/src/lib/contracts/` |

Field names change in exactly one place, `src-tauri/src/backend/convert.rs`.

## Naming

- `bridge/<method>.result.<case>.json` is a success payload; `bridge/error.<CODE>.json`
  is an error-frame payload. The `error.*` files are exactly the fourteen codes an
  error frame may carry.
- `public/<dto>.<case>.json` is a DTO; `public/errors/<CODE>.json` holds one
  `BackendError` per public error code (21).
- `bridge/soundings/` and `public/soundings/` hold the sounding and model
  payloads: the model list, an open summary (one time, and the 49-time BUFKIT
  example), the parameters, the Trends, and `render.example.png`, the image the
  browser fixture transport serves (sharpmod's render of the example, at half
  size).
- `bridge/map/` and `public/map/` hold the map payloads: the map catalogue,
  the categorical, tornado, and wind outlooks (wind empty: SPC published
  nothing), place names, an availability report, and the field and radar
  results, whose public forms are the binary frames `field.shrf` (a 256 by 128
  grid) and `radar.shrf`. The browser fixture transport serves them too.
- `invalid/<target>.<case>.*` files must be rejected by the parser that `<target>`
  names: `stations.catalog`, `health`, or `error`.

## Golden files

Four files are generated from the real catalogue and must not be edited by hand:

- `public/stations.catalog.full.json` (all bundled stations, one per line)
- `public/stations.search-cases.json` (queries and the ids `search_stations()` returns)
- `public/stations.catalog.seed-fallback.json`
- `bridge/stations.catalog.result.seed-fallback.json`

Regenerate them from the repository root whenever
`sharpmod/resources/uwyo_stations.json` changes:

```powershell
.gribenv\Scripts\python.exe scripts/export_frontend_fixtures.py
```

pytest compares the exporter's output with these files byte for byte, and the
real-sidecar Rust test compares the live catalogue with the full file, so a stale
golden file fails both.

The sounding fixtures come from the real bridge handlers, run on
`examples/soundings/hrrr_point_36.68N_95.66W_f018.npz` and
`examples/soundings/hrrr_kbvo_20260625_06z.buf` with a pinned clock and ids.
Regenerate them after a change to the bridge's sounding payloads or to
sharpmod's parameters:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"; .gribenv\Scripts\python.exe scripts/export_sounding_fixtures.py
```

`sharpmod/tests/test_bridge_soundings.py` checks the handlers' output against
them.

The map fixtures come from the real bridge handlers with the network replaced
by deterministic stand-ins (one synthetic HRRR record derived, reprojected, and
quantized by the real code; SPC payloads read by the real parser; a PNG from
sharpmod's encoder; a UWyo list) and a pinned clock. Regenerate them after a
change to the map payloads, the HRRR products, or their palettes:

```powershell
.gribenv\Scripts\python.exe scripts/export_map_fixtures.py
```

`sharpmod/tests/test_bridge_map.py` checks that the committed files are the
exporter's output.

## Format

UTF-8 (the golden files are ASCII), LF line endings, a final newline, and no
`NaN` or `Infinity` token outside `bridge/invalid/stations.catalog.nan-token.txt`.
The sample catalogues hold six real records (`03023`, `323306`, `61998`, `66160`,
`72357`, `73111`) covering every source code (`FM35`, `BUFR`, `FM37`), an empty
name, a multi-line name, and southern and eastern hemispheres.
