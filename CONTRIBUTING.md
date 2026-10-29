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

For the v3 React/Tauri interface, follow the
[frontend setup and checks](docs/frontend/ARCHITECTURE.md#running-the-interface).
Use `npm run tauri -- dev` in `frontend/` for the current desktop. The retired
Qt interface is available in the [v2.0.0 Legacy release](https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v2.0.0).

Desktop CI caches the pinned `cargo-audit` executable. Both lockfile audits
still refresh advisory data and run on every check. Release verification uses
`cargo test --release --locked --test updater_artifacts -- --ignored --nocapture`
to use the release build profile while checking the staged installer.

```powershell
# Fast deterministic feedback (grouped across four bounded workers).
python scripts/run_test_lane.py fast --workers 4

# Full scientific properties (the original 100-200 examples are preserved).
python scripts/run_test_lane.py property --workers 2

# The 3.11/3.12 compatibility smoke includes 10 examples per property.
python scripts/run_test_lane.py compatibility --workers 2

# Complete offline release gate; Qt tests stay together on one worker.
python scripts/run_test_lane.py serial-release --workers 4

# WRF-ARW extraction on Windows, which CI runs on its own runner.
python scripts/run_test_lane.py windows-wrf --workers 4

# Python 3.13 is the only coverage lane.
python scripts/run_test_lane.py fast --workers 4 --coverage

# Static correctness, focused maintainability, and dependency checks.
python -m ruff check sharpmod scripts packaging
python -m ruff check sharpmod/io/portable_sounding.py sharpmod/models/model_disk_cache.py `
  sharpmod/models/model_sources.py --select E,F,I,UP,B,SIM
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

The GitHub **Tests** workflow retains pytest logs, JUnit reports, coverage and
enforced timing budgets. Routine runs collect coverage in the Python 3.13 fast
job. When the complete offline release gate is requested, it collects coverage
instead and the overlapping fast job is skipped. The same 35% coverage threshold
applies in either case. Compare timing reports only under matching worker,
backend, coverage and host conditions; the checked JSON retains the reviewed
baselines and separate local and hosted limits.

For a final integrated/release-readiness check, first run
`sharpmod-rust-sync --check`, finish focused diagnosis, and checkpoint the exact
source state. Then run `python scripts/run_test_lane.py serial-release` once in a
supported environment. Treat the generated JUnit and timing JSON as part of the
result: a timing-budget failure is a failed gate even when every pytest assertion
passed. Use focused reruns to diagnose; do not repeatedly run the full offline
lane while iterating.

Every test has a 180-second safety timeout by default; the deliberately
expensive full-property and complete-release lanes raise that guard to 900 seconds
per test. Routine pull requests run Python 3.13 deterministic coverage, Windows
WRF tests, static checks, frontend unit/type/build checks and desktop/Rust source
checks. The full browser matrix runs after merge, on manual runs and
on `release/*` PRs. The frontend gate requires the expected browser result for
each event; failed or cancelled browser runs cannot pass that gate.
Reusable calls inherit the caller's event and follow the same rule.
Python 3.11/3.12 compatibility and full properties run after merge, on
manual/reusable runs and on `release/*` PRs. Rust MSRV checks, NumPy compatibility
and wheel matrices run on `release/*` PRs and manual runs; routine Rust PRs retain
stable formatting, Clippy, native tests and Python/Rust equivalence checks.
Official releases run every offline test against the exact immutable commit
that is packaged, across two workers with Qt tests grouped on one worker. This
checks all tests without checking whole-suite interactions in a single process.
The weekly schedule runs live-provider checks, including the multi-region CONUS
HRRR surface regression, and dependency audits.

## Project Conventions

- Use [`docs/CODE_STRUCTURE.md`](docs/CODE_STRUCTURE.md) to find the frontend,
  bridge and scientific modules. The legacy Qt GUI is retired from this tree.
- Keep the `sharpmod` import/package name stable.
- Prefer package-relative resource access through `importlib.resources`.
- Keep optional data-source dependencies behind extras and lazy imports.
- Add regression tests for decoder, derived-parameter, and renderer behavior.
- Keep example data small enough for GitHub.

### A regression test has to fail on the old behavior

A test that passes both before and after a fix documents the fix; it does not
protect it. Before opening a PR that repairs a defect, put the original code
back and confirm the new test actually fails, then say so in the PR. Where the
defect was a wrong quantity rather than an exception, assert the magnitude —
a projection bug that drew a model boundary 118 px from the truth is guarded by
a test that fails if the error exceeds a pixel, and by a second one that
asserts the discarded approach is off by more than fifty. Either alone would let
someone restore the shortcut with a green suite.

### Defects in dependencies

When the bug is upstream, fix it upstream. Open the report and the pull request
against the project that owns the code, and reference both from ours.

Where waiting for a release would leave the symptom in front of users, a local
repair goes in `sharpmod/upstream/upstream_patches.py`, and it has to earn its place:

- **Name the upstream report and fix** as module constants, so the reason a
  patch exists is discoverable from the patch.
- **Stand down automatically.** Detect a marker that only the fixed version
  carries and decline to patch when it is present, so upgrading the dependency
  is what retires the workaround rather than somebody remembering to delete it.
- **Apply at one chokepoint**, never at import of the patch module, so nothing
  is altered for code that does not go through us.
- **Degrade, do not guess.** A patch may make a failure recoverable; it must not
  invent data or change a scientific result.
- **Test it against the real dependency**, including that it declines when the
  marker is present, and state in the PR which parts of the upstream defect the
  local repair does *not* cover.

Do not vendor or fork a dependency to carry a fix, and do not pin a project to
an unreleased commit.

### Documentation that states facts

`installation.txt` mirrors `pyproject.toml`, and `pyproject.toml` is
authoritative. A change to dependencies, extras, or `[project.scripts]` is not
finished until the requirement summary, the extras section, the per-task table,
and the console-command list in `installation.txt` agree with it. The same goes
for anything here that names a lane, a script, or a flag: check it still exists
before shipping the sentence that promises it.
