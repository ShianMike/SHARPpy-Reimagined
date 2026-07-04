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
        dwpc, wdir, wspd, omeg`` plus metadata ``valid, run, loc, lat`` and an
        optional ``model``.

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

    pc = prof_collection.ProfCollection({"": [prof]}, [valid])
    pc.setMeta("loc", loc)
    pc.setMeta("observed", False)
    pc.setMeta("base_time", run)
    pc.setMeta("run", run)
    pc.setMeta("model", str(d["model"]) if "model" in d else "HRRR")
    if "lat" in d:
        pc.setMeta("lat", float(d["lat"]))
    if "lon" in d:
        pc.setMeta("lon", float(d["lon"]))
    return pc, loc
