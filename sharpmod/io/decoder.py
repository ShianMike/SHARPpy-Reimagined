"""SHARPpy Reimagined decoder registry -- a modernized port of ``sharppy.io.decoder``.

The upstream module used the standard-library ``imp`` module (removed in
Python 3.12) to load user-supplied custom decoders. This port replaces ``imp``
with :mod:`importlib` (``importlib.machinery`` + ``importlib.util``), so the
fork runs on modern Python with **no ``imp`` reference anywhere**
(Requirement 11.2).

Two behaviours are preserved verbatim from the legacy renderer:

* ``getDecoders()`` returns the lazily-built registry of format-name -> decoder
  class, discovering the built-in decoders plus any custom decoders dropped in
  ``~/.sharppy/decoders`` (Requirement 12).
* ``load_npz()`` builds a profile collection straight from a NumPy ``.npz``
  point-sounding sidecar, keeping the OMEGA (vertical-velocity) column so the
  renderer can draw the OMEGA profile (Requirement 12.5).

The built-in decoders currently live in the vendored ``sharppy.io`` tree. Those
modules import their ``Decoder`` base with ``from .decoder import Decoder``,
which would otherwise pull in the legacy, ``imp``-importing
``sharppy.io.decoder``. Before importing them we bridge that name to *this*
module so the vendored decoders bind to the modernized ``Decoder`` base and the
legacy module is never imported.
"""

import glob
import importlib.machinery
import importlib.util
import json
import logging
import math
import os
import ssl
import sys
import zipfile
from datetime import datetime
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

import certifi
import numpy as np

import sharppy.sharptab.profile as profile
import sharppy.sharptab.prof_collection as prof_collection

logger = logging.getLogger(__name__)

# Directory scanned for user-supplied custom decoders (one module per file).
HOME_DIR = os.path.join(os.path.expanduser("~"), ".sharppy", "decoders")

# Format-name -> decoder-class registry, built lazily by ``findDecoders``.
_decoders = {}

DEFAULT_REMOTE_TIMEOUT_S = 15.0
DEFAULT_MAX_REMOTE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_NPZ_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PROFILE_LEVELS = 20_000
DEFAULT_MAX_SIDECAR_BYTES = 1 * 1024 * 1024


def _positive_env_number(name, default, converter):
    """Return a positive numeric environment override or ``default``."""
    try:
        value = converter(os.environ.get(name, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if value > 0 else default


def _remote_timeout() -> float:
    return _positive_env_number(
        "SHARPMOD_REMOTE_TIMEOUT", DEFAULT_REMOTE_TIMEOUT_S, float)


def _max_remote_bytes() -> int:
    return _positive_env_number(
        "SHARPMOD_MAX_REMOTE_BYTES", DEFAULT_MAX_REMOTE_BYTES, int)


def _is_http_url(value) -> bool:
    try:
        return urlparse(os.fspath(value)).scheme.casefold() in {"http", "https"}
    except (TypeError, ValueError):
        return False


def _local_source_path(value) -> str:
    """Return a filesystem path for a plain path or local ``file://`` URL."""
    source = os.fspath(value)
    parsed = urlparse(source)
    if parsed.scheme.casefold() != "file":
        return source
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc.casefold() not in {"", "localhost"}:
        path = f"//{parsed.netloc}{path}"
    # ``urlparse('file:///C:/...').path`` starts with a slash on Windows.
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" \
            and path[2] == ":":
        path = path[1:]
    return path


def _read_bounded(stream, limit: int) -> bytes:
    """Read at most ``limit`` bytes and reject a larger response."""
    payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise IOError(
            f"input exceeds the configured {limit:,}-byte safety limit")
    return payload


class abstract(object):
    """Decorator marking an unimplemented abstract method.

    Calling a method wrapped in ``@abstract`` raises ``NotImplementedError``;
    subclasses are expected to override it.
    """

    def __init__(self, func):
        self._func = func

    def __call__(self, *args, **kwargs):
        raise NotImplementedError(
            "Function or method '%s' is abstract. Override it in a subclass!"
            % self._func.__name__)


class Decoder(object):
    """Base class for all decoders.

    A decoder is constructed from a file name or URL and parses it into a
    profile collection on construction. Remote data is fetched over HTTPS with
    server-certificate verification enabled via an :mod:`ssl` default context
    (no legacy ``urlopen(cafile=...)`` shim).
    """

    def __init__(self, file_name):
        self._file_name = file_name
        self._prof_collection = self._parse()

    @abstract
    def _parse(self):
        pass

    def _downloadFile(self):
        """Return the decoded text of the decoder's source (URL or local file).

        HTTP(S) inputs use certificate verification, a bounded timeout, a
        bounded response size, and an explicitly closed response. Filesystem
        inputs are opened directly instead of first being mistaken for URLs.
        """
        if _is_http_url(self._file_name):
            context = ssl.create_default_context(cafile=certifi.where())
            try:
                with urlopen(
                    self._file_name,
                    timeout=_remote_timeout(),
                    context=context,
                ) as response:
                    file_data = _read_bounded(
                        response, _max_remote_bytes())
            except (ValueError, URLError, OSError) as exc:
                raise IOError(
                    "Remote file '%s' could not be downloaded: %s"
                    % (self._file_name, exc)
                ) from exc
        else:
            fname = _local_source_path(self._file_name)
            try:
                with open(fname, "rb") as handle:
                    file_data = _read_bounded(
                        handle, _max_remote_bytes())
            except OSError as exc:
                raise IOError(
                    "File '%s' cannot be found" % self._file_name
                ) from exc
        return file_data.decode("utf-8-sig")

    def getProfiles(self, indexes=None):
        """Return the parsed profile collection (optionally a subset)."""
        prof_col = self._prof_collection
        if indexes is not None:
            prof_col = prof_col.subset(indexes)
        return prof_col

    def getStnId(self):
        """Return the station identifier / location metadata."""
        return self._prof_collection.getMeta('loc')


def _load_source(module_name, path):
    """Load a module from a source file via importlib.

    Drop-in replacement for the removed ``imp.load_source``: builds a
    ``SourceFileLoader`` spec, executes it, and registers the module in
    ``sys.modules`` under ``module_name``.
    """
    loader = importlib.machinery.SourceFileLoader(module_name, path)
    spec = importlib.util.spec_from_file_location(module_name, path,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _register(dec_module):
    """Register a decoder module's class in the registry by its format name."""
    dec_name = dec_module.__classname__
    fmt_name = dec_module.__fmtname__
    _decoders[fmt_name] = getattr(dec_module, dec_name)


def _bridge_legacy_decoder_base():
    """Bind the vendored ``sharppy.io.decoder`` name to this module.

    The vendored built-in decoders import their ``Decoder`` base with
    ``from .decoder import Decoder`` / ``from sharppy.io.decoder import
    Decoder``. Registering this module under that name makes them bind to the
    modernized ``Decoder`` base and prevents the legacy, ``imp``-importing
    module from ever being imported.
    """
    if 'sharppy.io.decoder' not in sys.modules:
        sys.modules['sharppy.io.decoder'] = sys.modules[__name__]


def _sanitize_profile_rows(prof):
    """Normalize SPC rows that decoded as ``nan`` instead of missing values.

    Some high-resolution SPC-style exports include a column-name row immediately
    after ``%RAW%``. The vendored SPC decoder feeds that line to ``genfromtxt``,
    producing an all-``nan`` level. Raw profiles tolerate it, but the later
    convective-profile upgrade can fail on NumPy mask length mismatches. Keep
    rows with finite pressure/height and convert any remaining non-finite cell
    to SHARPpy's missing-value sentinel.
    """
    if not hasattr(prof, "pres") or not hasattr(prof, "hght"):
        return

    missing = float(getattr(prof, "missing", -9999.0))
    pres = np.ma.asarray(prof.pres, dtype=float)
    hght = np.ma.asarray(prof.hght, dtype=float)
    if pres.ndim != 1 or hght.ndim != 1 or len(pres) != len(hght):
        return

    pres_values = np.asarray(pres.filled(np.nan), dtype=float)
    hght_values = np.asarray(hght.filled(np.nan), dtype=float)
    keep = np.isfinite(pres_values) & np.isfinite(hght_values)
    if keep.size == 0 or not np.any(keep):
        return

    for name in (
        "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v",
        "omeg", "tmp_stdev", "dew_stdev",
    ):
        arr = getattr(prof, name, None)
        if arr is None:
            continue
        marr = np.ma.asarray(arr, dtype=float)
        if marr.ndim != 1 or len(marr) != len(keep):
            continue
        values = np.asarray(marr.filled(missing), dtype=float)[keep]
        values[~np.isfinite(values)] = missing
        setattr(prof, name, np.ma.masked_values(values, missing))


def _max_spc_profile_levels():
    """Return the plotting-safe cap for very dense SPC profiles."""
    try:
        value = int(os.environ.get("SHARPMOD_MAX_SPC_LEVELS", "700"))
    except ValueError:
        value = 700
    return max(50, value)


def _thin_profile_rows(prof):
    """Downsample extremely dense SPC profiles before SHARPpy widget plotting."""
    max_levels = _max_spc_profile_levels()
    pres = getattr(prof, "pres", None)
    if pres is None:
        return
    try:
        count = len(pres)
    except TypeError:
        return
    if count <= max_levels:
        return

    indexes = np.unique(np.rint(
        np.linspace(0, count - 1, max_levels)
    ).astype(int))

    for name in (
        "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v",
        "omeg", "tmp_stdev", "dew_stdev",
    ):
        arr = getattr(prof, name, None)
        if arr is None:
            continue
        marr = np.ma.asarray(arr)
        if marr.ndim != 1 or len(marr) != count:
            continue
        setattr(prof, name, marr[indexes])


def _sanitize_profile_collection(prof_col):
    """Apply row sanitation to every raw profile in a ProfCollection."""
    for profs in getattr(prof_col, "_profs", {}).values():
        for prof in profs:
            _sanitize_profile_rows(prof)
            _thin_profile_rows(prof)
    return prof_col


def _wrap_spc_decoder():
    """Wrap the vendored SPC decoder with SHARPpy Reimagined row sanitation."""
    spc_cls = _decoders.get("spc")
    if spc_cls is None or getattr(spc_cls, "_sharpmod_sanitized", False):
        return

    class SanitizedSPCDecoder(spc_cls):
        _sharpmod_sanitized = True

        def _parse(self):
            return _sanitize_profile_collection(super()._parse())

    SanitizedSPCDecoder.__name__ = spc_cls.__name__
    SanitizedSPCDecoder.__qualname__ = spc_cls.__qualname__
    SanitizedSPCDecoder.__module__ = spc_cls.__module__
    _decoders["spc"] = SanitizedSPCDecoder


def findDecoders():
    """Discover and register the built-in and custom decoders.

    Built-in decoders are imported from the vendored ``sharppy.io`` package;
    custom decoders are loaded from ``HOME_DIR`` with :func:`_load_source`
    (importlib), never ``imp``.
    """
    global _decoders

    _bridge_legacy_decoder_base()

    built_ins = ['buf_decoder', 'spc_decoder', 'pecan_decoder', 'arw_decoder',
                 'uwyo_decoder']
    io = __import__('sharppy.io', globals(), locals(), built_ins, 0)

    for dec in built_ins:
        logger.debug("Loading built-in decoder '%s'.", dec)
        _register(getattr(io, dec))

    custom = glob.glob(os.path.join(HOME_DIR, '*.py'))
    for dec in custom:
        dec_mod_name = os.path.basename(dec)[:-3]
        logger.debug("Found custom decoder '%s'.", dec_mod_name)
        _register(_load_source(dec_mod_name, dec))

    _wrap_spc_decoder()


def getDecoder(dec_name):
    """Return the decoder class registered under ``dec_name``."""
    return getDecoders()[dec_name]


def getDecoders():
    """Return the format-name -> decoder-class registry (built lazily)."""
    if _decoders == {}:
        findDecoders()
    return _decoders


def _validate_npz_container(filename) -> None:
    """Reject oversized or malformed point-sounding archives before NumPy."""
    path = os.path.abspath(os.fspath(filename))
    size_limit = _positive_env_number(
        "SHARPMOD_MAX_NPZ_BYTES", DEFAULT_MAX_NPZ_BYTES, int)
    try:
        if os.path.getsize(path) > size_limit:
            raise ValueError(
                f"portable sounding exceeds {size_limit:,} bytes")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 64:
                raise ValueError("portable sounding contains too many arrays")
            expanded = sum(member.file_size for member in members)
            if expanded > size_limit:
                raise ValueError(
                    "portable sounding expands beyond the configured "
                    f"{size_limit:,}-byte safety limit")
            if any(
                member.file_size > size_limit
                or not member.filename.endswith(".npy")
                for member in members
            ):
                raise ValueError(
                    "portable sounding contains an invalid array member")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"invalid portable sounding archive: {exc}") from exc


def _npz_scalar(data, key, *, default=None):
    """Read a non-object scalar from an already safely-open NPZ archive."""
    if key not in data:
        if default is not None:
            return default
        raise ValueError(f"portable sounding is missing required field {key!r}")
    value = np.asarray(data[key])
    if value.dtype.hasobject:
        raise ValueError(
            f"portable sounding field {key!r} uses unsafe object data")
    if value.size != 1:
        raise ValueError(
            f"portable sounding field {key!r} must contain one value")
    return value.reshape(-1)[0].item()


def _npz_profile_array(data, key, expected_levels=None) -> np.ndarray:
    """Read and validate one numeric, one-dimensional profile array."""
    if key not in data:
        raise ValueError(f"portable sounding is missing required field {key!r}")
    value = np.asarray(data[key])
    if value.dtype.hasobject or value.dtype.kind not in "fiu":
        raise ValueError(
            f"portable sounding field {key!r} must be a numeric array")
    if value.ndim != 1:
        raise ValueError(
            f"portable sounding field {key!r} must be one-dimensional")
    max_levels = _positive_env_number(
        "SHARPMOD_MAX_PROFILE_LEVELS",
        DEFAULT_MAX_PROFILE_LEVELS,
        int,
    )
    if not 2 <= value.size <= max_levels:
        raise ValueError(
            f"portable sounding field {key!r} has an invalid level count")
    if expected_levels is not None and value.size != expected_levels:
        raise ValueError(
            f"portable sounding field {key!r} has {value.size} levels; "
            f"expected {expected_levels}")
    return np.asarray(value, dtype=float)


def _parse_portable_datetime(value, key: str) -> datetime:
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise ValueError(
        f"portable sounding field {key!r} is not a supported date/time")


def _read_json_sidecar(filename) -> tuple[dict[str, Any] | None, str]:
    source_path = os.path.abspath(os.fspath(filename))
    stem_path = os.path.splitext(source_path)[0] + ".json"
    candidates = (
        (stem_path,)
        if source_path.lower().endswith(".npz")
        else (source_path + ".json", stem_path)
    )
    for sidecar_path in dict.fromkeys(candidates):
        try:
            if os.path.getsize(sidecar_path) > _positive_env_number(
                "SHARPMOD_MAX_SIDECAR_BYTES",
                DEFAULT_MAX_SIDECAR_BYTES,
                int,
            ):
                continue
            with open(sidecar_path, encoding="utf-8") as sidecar_file:
                sidecar = json.load(sidecar_file)
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if isinstance(sidecar, dict):
            return sidecar, sidecar_path
    return None, candidates[0]


def attach_json_sidecar(prof_col, filename) -> dict[str, Any] | None:
    """Attach safe adjacent JSON provenance to any decoded collection.

    This is intentionally format-neutral so an SPC file and its extractor
    sidecar retain the same model and coordinate metadata as the equivalent
    portable NPZ file.
    """
    sidecar, sidecar_path = _read_json_sidecar(filename)
    if sidecar is None:
        return None

    for raw_key, raw_value in sidecar.items():
        key = str(raw_key)
        value = raw_value
        if key in {"run", "base_time"} and isinstance(value, str):
            try:
                value = _parse_portable_datetime(value, key)
            except ValueError:
                continue
        elif key in {"lat", "lon", "requested_lat", "requested_lon",
                     "selected_lat", "selected_lon"}:
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
        elif key == "observed" and not isinstance(value, bool):
            continue
        elif key == "loc":
            # A decoder-derived station name wins unless it is absent.
            try:
                if str(prof_col.getMeta("loc") or "").strip():
                    continue
            except Exception:
                pass
            value = str(value)
        try:
            prof_col.setMeta(key, value)
        except Exception:
            logger.debug(
                "Could not attach sidecar metadata %s from %s",
                key,
                sidecar_path,
                exc_info=True,
            )
    prof_col.setMeta("metadata_sidecar", sidecar_path)
    return sidecar


def load_npz(filename):
    """Build a profile collection from a NumPy ``.npz`` point-sounding sidecar.

    This bypasses the SPC text decoder so the vertical-velocity (OMEGA) column
    survives, letting the renderer draw the OMEGA profile (Requirement 12.5).

    Parameters
    ----------
    filename : str
        Path to the ``.npz`` sidecar. Expected arrays: ``pres, hght, tmpc,
        dwpc, wdir, wspd, omeg`` plus metadata ``valid, run, loc, lat`` and
        optional ``model`` / surface-vorticity metadata.

    Returns
    -------
    tuple(prof_collection.ProfCollection, str)
        The built profile collection and the station id / location label.
    """
    _validate_npz_container(filename)
    with np.load(filename, allow_pickle=False) as d:
        pres = _npz_profile_array(d, "pres")
        level_count = pres.size
        hght = _npz_profile_array(d, "hght", level_count)
        tmpc = _npz_profile_array(d, "tmpc", level_count)
        dwpc = _npz_profile_array(d, "dwpc", level_count)
        wdir = _npz_profile_array(d, "wdir", level_count)
        wspd = _npz_profile_array(d, "wspd", level_count)
        omeg = _npz_profile_array(d, "omeg", level_count)
        valid = _parse_portable_datetime(
            _npz_scalar(d, "valid"), "valid")
        run = _parse_portable_datetime(_npz_scalar(d, "run"), "run")
        loc = str(_npz_scalar(d, "loc")).strip()
        lat = float(_npz_scalar(d, "lat"))
        lon = float(_npz_scalar(d, "lon")) if "lon" in d else None
        if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
            raise ValueError("portable sounding latitude is out of range")
        if lon is not None and (
            not math.isfinite(lon) or not -180.0 <= lon <= 180.0
        ):
            raise ValueError("portable sounding longitude is out of range")
        model_name = str(_npz_scalar(d, "model", default="HRRR")).strip()
        if "observed" in d:
            observed_value = _npz_scalar(d, "observed")
            if not isinstance(observed_value, bool):
                raise ValueError(
                    "portable sounding field 'observed' must be a Boolean"
                )
        else:
            observed_value = model_name.casefold().startswith("observed")

        optional_surface_fields = {}
        for key in (
            "surface_relative_vorticity",
            "sfc_relative_vorticity",
            "surface_vorticity",
            "sfc_vorticity",
            "vorticity",
        ):
            if key in d:
                optional_surface_fields[key] = float(_npz_scalar(d, key))

        provenance = {}
        for key in (
            "source", "source_provider", "source_provider_name",
            "source_station", "source_url", "requested_station",
        ):
            if key in d:
                provenance[key] = str(_npz_scalar(d, key))
        if "fallback_from" in d:
            fallback = np.asarray(d["fallback_from"])
            if fallback.dtype.hasobject:
                raise ValueError(
                    "portable sounding field 'fallback_from' uses unsafe "
                    "object data")
            provenance["fallback_from"] = tuple(
                str(value) for value in fallback.reshape(-1))

    prof = profile.create_profile(
        profile="raw", pres=pres, hght=hght, tmpc=tmpc,
        dwpc=dwpc, wdir=wdir, wspd=wspd, omeg=omeg,
        location=loc, date=valid, latitude=lat, missing=-9999.0)
    for key, value in optional_surface_fields.items():
        setattr(prof, key, value)

    pc = prof_collection.ProfCollection({"": [prof]}, [valid])
    pc.setMeta("loc", loc)
    pc.setMeta("observed", observed_value)
    pc.setMeta("base_time", run)
    pc.setMeta("run", run)
    pc.setMeta("model", model_name)
    pc.setMeta("npz_path", os.path.abspath(filename))
    pc.setMeta("decoder", "portable NPZ decoder")
    pc.setMeta("backend", "portable NPZ")
    pc.setMeta("lat", lat)
    if lon is not None:
        pc.setMeta("lon", lon)
    for key, value in provenance.items():
        pc.setMeta(key, value)
    if provenance:
        current_meta = dict(getattr(prof, "meta", {}) or {})
        current_meta.update(provenance)
        current_meta["observed"] = observed_value
        prof.meta = current_meta
    for key, value in optional_surface_fields.items():
        pc.setMeta(key, value)
    sidecar, sidecar_path = _read_json_sidecar(filename)
    if sidecar is not None:
        # Preserve datetime-valued core metadata established above. Everything
        # else is JSON-safe provenance produced by the extractor and can be
        # surfaced by the viewer's data-quality inspector or analysis sessions.
        reserved = {
            "loc", "observed", "base_time", "run", "model", "lat", "lon",
            *optional_surface_fields,
        }
        for key, value in sidecar.items():
            if str(key) not in reserved:
                pc.setMeta(str(key), value)
        pc.setMeta("metadata_sidecar", sidecar_path)
    if optional_surface_fields:
        profiles = []
        try:
            profiles.extend((pc.getCurrentProfs() or {}).values())
        except Exception:
            pass
        profiles.extend(p for plist in getattr(pc, "_profs", {}).values() for p in plist)
        for cur_prof in profiles:
            for key, value in optional_surface_fields.items():
                setattr(cur_prof, key, value)
    return pc, loc


__all__ = [
    "Decoder",
    "attach_json_sidecar",
    "findDecoders",
    "getDecoder",
    "getDecoders",
    "load_npz",
]
