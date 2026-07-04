# Contributing

Thanks for helping improve SHARPpy Reimagined. This project is a Python 3.11+
modernization of SHARPpy with a focus on reproducible sounding rendering,
decoder correctness, and weather-analysis tooling.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,era5,wrf,render]"
python -m pip install --no-deps "SHARPpy==1.4.0a5"
```

On Linux or macOS, activate the environment with:

```bash
source .venv/bin/activate
```

## Test Before Opening a PR

```bash
pytest
```

Renderer tests run headlessly with Qt's `offscreen` platform. If you are working
on extraction tools, add focused tests that avoid live network dependencies when
possible.

## Project Conventions

- Keep the `sharpmod` import/package name stable.
- Prefer package-relative resource access through `importlib.resources`.
- Keep optional data-source dependencies behind extras and lazy imports.
- Add regression tests for decoder, derived-parameter, and renderer behavior.
- Keep example data small enough for GitHub.
