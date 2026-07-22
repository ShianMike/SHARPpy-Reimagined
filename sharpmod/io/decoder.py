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
import os
import ssl
import sys
from datetime import datetime
from urllib.error import URLError
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

        Tries an HTTPS fetch first (certificate verification on), then falls
        back to reading a local path, mirroring the legacy behaviour without
        the removed ``cafile`` keyword argument.
        """
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            f = urlopen(self._file_name, context=context)
        except (ValueError, URLError, IOError):
            fname = self._file_name[7:] \
                if self._file_name.startswith('file://') else self._file_name
            try:
                f = open(fname, 'rb')
            except IOError:
                raise IOError("File '%s' cannot be found" % self._file_name)
        file_data = f.read()
        return file_data.decode('utf-8')

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
    d = np.load(filename, allow_pickle=True)
    valid = datetime.strptime(str(d["valid"]), "%Y-%m-%d %H:%M")
    run = datetime.strptime(str(d["run"]), "%Y-%m-%d %H:%M")
    loc = str(d["loc"])

    prof = profile.create_profile(
        profile="raw", pres=d["pres"], hght=d["hght"], tmpc=d["tmpc"],
        dwpc=d["dwpc"], wdir=d["wdir"], wspd=d["wspd"], omeg=d["omeg"],
        location=loc, date=valid, latitude=float(d["lat"]), missing=-9999.0)

    optional_surface_fields = {}
    for key in (
        "surface_relative_vorticity",
        "sfc_relative_vorticity",
        "surface_vorticity",
        "sfc_vorticity",
        "vorticity",
    ):
        if key in d:
            value = float(np.asarray(d[key]).reshape(-1)[0])
            optional_surface_fields[key] = value
            setattr(prof, key, value)

    pc = prof_collection.ProfCollection({"": [prof]}, [valid])
    pc.setMeta("loc", loc)
    model_name = str(d["model"]) if "model" in d else "HRRR"
    observed = model_name.casefold().startswith("observed")
    if "observed" in d:
        observed = bool(np.asarray(d["observed"]).reshape(-1)[0])
    pc.setMeta("observed", observed)
    pc.setMeta("base_time", run)
    pc.setMeta("run", run)
    pc.setMeta("model", model_name)
    pc.setMeta("npz_path", os.path.abspath(filename))
    pc.setMeta("decoder", "portable NPZ decoder")
    pc.setMeta("backend", "portable NPZ")
    if "lat" in d:
        pc.setMeta("lat", float(d["lat"]))
    if "lon" in d:
        pc.setMeta("lon", float(d["lon"]))
    provenance = {}
    for key in (
        "source", "source_provider", "source_provider_name",
        "source_station", "source_url", "requested_station",
    ):
        if key in d:
            value = str(np.asarray(d[key]).reshape(-1)[0])
            provenance[key] = value
            pc.setMeta(key, value)
    if "fallback_from" in d:
        fallback_from = tuple(str(value) for value in np.asarray(
            d["fallback_from"]
        ).reshape(-1))
        provenance["fallback_from"] = fallback_from
        pc.setMeta("fallback_from", fallback_from)
    if provenance:
        current_meta = dict(getattr(prof, "meta", {}) or {})
        current_meta.update(provenance)
        current_meta["observed"] = observed
        prof.meta = current_meta
    for key, value in optional_surface_fields.items():
        pc.setMeta(key, value)
    sidecar_path = os.path.splitext(os.path.abspath(filename))[0] + ".json"
    try:
        with open(sidecar_path, encoding="utf-8") as sidecar_file:
            sidecar = json.load(sidecar_file)
    except (OSError, ValueError, TypeError):
        sidecar = None
    if isinstance(sidecar, dict):
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
