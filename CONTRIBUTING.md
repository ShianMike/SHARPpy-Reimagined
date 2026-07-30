# Contributing

Thanks for helping improve SHARPpy Reimagined. This project is a Python
3.11–3.13 modernization of SHARPpy with a focus on reproducible sounding
rendering, decoder correctness, and weather-analysis tooling.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,quality,era5,wrf,render]"
python scripts/install_sharppy_compat.py --sharppy-only
```

The compatibility installer verifies the pinned upstream SHARPpy wheel,
repairs only its obsolete NumPy dependency declaration, and finishes by
running `pip check`.

On Linux or macOS, activate the environment with:

```bash
source .venv/bin/activate
```

## Test Before Opening a PR

```powershell
# Fast deterministic feedback (the same lane used by CI).
$env:SHARPMOD_HYPOTHESIS_PROFILE = "fast"
python -m pytest -m "not property and not live_provider"

# Full scientific properties (100 Hypothesis examples per property).
$env:SHARPMOD_HYPOTHESIS_PROFILE = "full"
python -m pytest -m "property and not live_provider" --timeout=900

# Whole offline suite.
python -m pytest

# Static correctness, focused maintainability, and dependency checks.
python -m ruff check sharpmod scripts packaging
python -m ruff check sharpmod/portable_sounding.py sharpmod/model_disk_cache.py `
  sharpmod/model_sources.py sharpmod/gui_cache.py --select E,F,I,UP,B,SIM
python -m pip_audit --skip-editable
```

Renderer tests run headlessly with Qt's `offscreen` platform. If you are working
on extraction tools, add focused tests that avoid live network dependencies when
possible. Current public-provider contracts can be run locally on demand:

```powershell
$env:SHARPMOD_RUN_LIVE_PROVIDER_TESTS = "1"
python -m pytest -m live_provider
```

Every test has a 180-second safety timeout by default; the deliberately
expensive full-property lane raises that guard to 900 seconds per test. Pull
requests, pushes to `main`, and official releases run the fast and
full-property lanes separately. A weekly scheduled lane runs the live-provider
checks, including the high-terrain HRRR surface regression. Releases reuse this
workflow at the exact immutable commit that is packaged.

## Project Conventions

- Keep the `sharpmod` import/package name stable.
- Prefer package-relative resource access through `importlib.resources`.
- Keep optional data-source dependencies behind extras and lazy imports.
- Add regression tests for decoder, derived-parameter, and renderer behavior.
- Keep example data small enough for GitHub.
