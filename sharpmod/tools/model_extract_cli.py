"""Command-line entry point and runtime preflight for public point-model extraction.

Argument parsing, platform dependency setup, and planned-search validation stay in the
CLI layer; provider configuration, probing, retrieval, and decoding live in adjacent
modules."""

from __future__ import annotations

from dataclasses import field
from sharpmod.models.model_fields import IFS_SURFACE_FIELDS
from sharpmod.models.model_fields import NOAA_SURFACE_FIELDS
from sharpmod.models.model_fields import choose_search
from sharpmod.tools.era5_extract import ERA5ExtractionError as ModelExtractionError
from sharpmod.tools.era5_extract import RetrievalError
import importlib.util
import os
import sys
import tempfile
from sharpmod.tools import model_extract as _api


def _prepare_windows_eccodes_runtime():
    """Expose a bundled ecCodes DLL when no CPython helper wheel exists.

    ECMWF's Windows wheel normally includes a version-specific ``_eccodes``
    helper.  On Python versions for which that helper is not published, pip
    falls back to the pure-Python wheel even though the same installation still
    contains ``eccodes.dll`` and its dependencies.  Selecting findlibs mode and
    putting that package directory first on ``PATH`` lets the ABI-level CFFI
    bindings load the bundled DLL directly.
    """
    if sys.platform != "win32":
        return None

    try:
        spec = importlib.util.find_spec("eccodes")
        origin = getattr(spec, "origin", None)
        if not origin:
            return None
        package_dir = os.path.dirname(os.path.abspath(origin))
        package_files = os.listdir(package_dir)
    except (ImportError, OSError, ValueError):
        return None

    has_helper = any(
        name.startswith("_eccodes") and name.endswith(".pyd")
        for name in package_files
    )
    if has_helper or not os.path.isfile(os.path.join(package_dir, "eccodes.dll")):
        return None

    os.environ["ECCODES_PYTHON_USE_FINDLIBS"] = "1"
    current_path = os.environ.get("PATH", "")
    path_entries = [entry for entry in current_path.split(os.pathsep) if entry]
    normalized = {os.path.normcase(os.path.abspath(entry)) for entry in path_entries}
    if os.path.normcase(package_dir) not in normalized:
        os.environ["PATH"] = os.pathsep.join([package_dir, *path_entries])
    return package_dir


def require_runtime_dependencies():
    """Load the native ecCodes boundary before starting a worker.

    ecCodes is a native extension.  Importing a partially installed build for
    the first time from a ``QThread`` can terminate the whole process before
    Python can report the import error.  The GUI calls this function on its
    main thread before it creates model-availability or model-fetch workers.
    The slower pure-Python Herbie, cfgrib, and xarray imports stay on those
    background workers.
    """
    _api._prepare_windows_eccodes_runtime()
    try:
        import eccodes

        # Importing the pure-Python ``eccodes`` wrapper can succeed even when
        # its binary extension is absent.  Calling the API version forces that
        # native boundary to be resolved here, on the main thread.
        eccodes.codes_get_api_version()
    except Exception as exc:  # pragma: no cover - environment-specific path
        hint = "Install the optional model stack with pip install -e \".[era5]\"."
        if sys.platform == "win32" and sys.version_info >= (3, 14):
            hint = (
                "The installed Windows ecCodes package is incomplete. "
                "Reinstall the [era5] extra, or use Python 3.11-3.13 if its "
                "native DLL still cannot be loaded."
            )
        raise RetrievalError(
            "forecast model support could not load its GRIB runtime: %s. %s"
            % (exc, hint)
        ) from exc


def _planned_model_search(herbie, config):
    """Choose one field from each equivalent group without dropping levels."""
    try:
        inventory = herbie.inventory(config.search).copy()
        search, fields = choose_search(config, inventory)
        selected_fields = tuple(fields)
        if _api._is_ecmwf_open_data(config):
            selected_fields += tuple(
                field for field in IFS_SURFACE_FIELDS
                if field not in selected_fields
            )
        else:
            selected_fields += tuple(
                field for field in NOAA_SURFACE_FIELDS
                if field not in selected_fields
            )
        # Narrow with the chosen search rather than by variable name. Name
        # matching alone cannot express levels, so it both over-selects (NOAA
        # ``TMP`` at 80 m above ground) and would mis-select for ECMWF, where
        # ``z`` is the invariant surface field *and* a pressure-level field on
        # every published isobar. Matching the search keeps the planned byte
        # ranges identical to what the download expression asks for.
        planned = inventory
        if "search_this" in inventory:
            planned = inventory[
                inventory["search_this"].astype(str).str.contains(
                    search, regex=True, na=False
                )
            ].copy()
        # Confirm the in-memory narrowed inventory really contains records
        # before using the expression to name a persistent subset file.  This
        # avoids two more regex scans/copies of Herbie's cached DataFrame.
        if len(planned) == 0:
            raise ValueError("planned model search matched no messages")
        return search, selected_fields, planned
    except Exception as exc:
        _api._LOGGER.info(
            "model_fields.fallback model=%s reason=%s", config.key, exc
        )
        try:
            fallback_inventory = herbie.inventory(config.search).copy()
        except Exception:
            fallback_inventory = None
        return config.search, (), fallback_inventory


def main(argv=None):  # pragma: no cover - CLI wrapper
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="model-extract",
        description="Extract public forecast-model point soundings to .npz")
    parser.add_argument("model", nargs="?", help="model key; use --list")
    parser.add_argument("lat", nargs="?", type=float)
    parser.add_argument("lon", nargs="?", type=float)
    parser.add_argument("out", nargs="?", default=None)
    parser.add_argument("--run", default=None,
                        help="model run/cycle time, ISO or 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--fxx", type=int, default=0, help="forecast hour")
    parser.add_argument("--member", default=None,
                        help="ensemble/member override, e.g. GEFS c00 or p01")
    parser.add_argument("--loc", default=None, help="location label")
    parser.add_argument("--render", nargs="?", const="", default=None,
                        metavar="PNG", help="also render the sounding to PNG")
    parser.add_argument("--list", action="store_true",
                        help="list supported and known unsupported models")
    parser.add_argument("--probe", action="store_true",
                        help="check inventory availability for a model")
    parser.add_argument("--open-subset", action="store_true",
                        help="with --probe, open the pressure-level subset too")
    parser.add_argument(
        "--require-surface-contract",
        action="store_true",
        help="with --probe, fail until every verified ground field is present",
    )
    parser.add_argument(
        "--lookback-cycles",
        type=int,
        default=1,
        help="with --probe, inspect this many recent cycles for live inventory",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.require_surface_contract and not args.probe:
        parser.error("--require-surface-contract requires --probe")
    if args.lookback_cycles != 1 and not args.probe:
        parser.error("--lookback-cycles requires --probe")
    if args.lookback_cycles < 1:
        parser.error("--lookback-cycles must be at least 1")

    if args.list:
        print("Supported forecast models:")
        for cfg in _api.available_models():
            fxx = _api.forecast_hours(cfg)
            fxx_label = "F%03d-F%03d" % (min(fxx), max(fxx)) if fxx else "F---"
            print("  %-20s %-24s %-16s %-11s %s/%s" % (
                cfg.key, cfg.label, cfg.domain, fxx_label,
                cfg.herbie_model, cfg.product))
        print("\nKnown but not enabled:")
        for key, reason in sorted(_api.unsupported_models().items()):
            print("  %-15s %s" % (key, reason))
        return 0

    if not args.model:
        parser.error("model is required unless --list is used")

    run = _api._parse_time(args.run) if args.run else None
    if args.probe:
        if args.lookback_cycles > 1:
            info = _api.probe_recent_surface_contract(
                args.model,
                reference_time=run,
                fxx=args.fxx,
                member=args.member,
                lookback_cycles=args.lookback_cycles,
                open_subset=args.open_subset,
            )
        else:
            info = _api.probe(
                args.model,
                run_time=run,
                fxx=args.fxx,
                member=args.member,
                open_subset=args.open_subset,
            )
        for key in sorted(info):
            print("%s: %s" % (key, info[key]))
        usable = bool(info.get("available"))
        if args.require_surface_contract:
            usable = usable and bool(info.get("surface_contract_complete"))
        return 0 if usable else 1

    if args.lat is None or args.lon is None:
        parser.error("lat and lon are required for extraction")
    transient = args.render is not None
    download_dir = tempfile.mkdtemp(prefix="sharpmod-model-") \
        if transient else None
    path = None
    try:
        try:
            path = _api.extract(
                args.model, args.lat, args.lon, run_time=run, fxx=args.fxx,
                out_path=args.out, loc=args.loc, member=args.member,
                download_dir=download_dir,
            )
        except (ModelExtractionError, KeyError, OSError) as exc:
            print("ERROR: %s" % exc)
            return 1
        print("wrote %s" % path)

        if transient:
            from sharpmod.tools import render_npz
            png = render_npz(path, args.render or None)
            print("rendered %s" % png)
        return 0
    finally:
        if transient:
            _api.cleanup_transient_data(path or args.out, download_dir)
