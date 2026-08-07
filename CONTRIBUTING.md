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
# Fast deterministic feedback (grouped across four bounded workers).
python scripts/run_test_lane.py fast --workers 4

# Full scientific properties (the original 100-200 examples are preserved).
python scripts/run_test_lane.py property --workers 4

# The 3.11/3.12 compatibility smoke includes 10 examples per property.
python scripts/run_test_lane.py compatibility --workers 4

# Exact complete non-parallel gate used by official releases.
python scripts/run_test_lane.py serial-release

# Python 3.13 is the only coverage lane.
python scripts/run_test_lane.py fast --workers 4 --coverage

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
python scripts/run_test_lane.py live-provider
```

Each runner invocation writes JUnit XML and a JSON timing report under
`.test-results/`, reports its 15 slowest tests, and checks the reviewed budgets
in `constraints/test-performance-baseline.json`. Parallel and serial test
durations have separate baselines because CPU contention changes individual
test time. Do not raise a budget merely to make a regression pass: reproduce
the lane, explain the change, and update the checked value in the same review.

Every test has a 180-second safety timeout by default; the deliberately
expensive full-property and serial-release lanes raise that guard to 900 seconds
per test. Pull requests run 3.11/3.12 compatibility smoke, Python 3.13
deterministic coverage, and the full Python 3.13 property lane. Pushes to
`main` also run the complete serial gate. Official releases force that serial
gate against the exact immutable commit that is packaged. A weekly scheduled
lane runs the live-provider checks, including the multi-region CONUS HRRR
surface regression.

## Project Conventions

- Keep the `sharpmod` import/package name stable.
- Prefer package-relative resource access through `importlib.resources`.
- Keep optional data-source dependencies behind extras and lazy imports.
- Add regression tests for decoder, derived-parameter, and renderer behavior.
- Keep example data small enough for GitHub.
