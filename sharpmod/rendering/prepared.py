"""One pristine local input for the serial Qt worker; never retain its widgets."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pickle
import stat

from sharpmod import backends
from sharpmod.io import decoder
from sharpmod.sharptab.constants import OPTIONAL_SOURCE_SURFACE_FIELDS


def _science_identity():
    return (
        id(backends.get_backend()),
        tuple(sorted(backends.backend_info().items())),
        tuple(sorted((key, value) for key, value in os.environ.items()
                     if key.startswith("SHARPMOD_"))),
    )


def _profile_signature(prof):
    # Include masks, optional columns, source metadata and both motion vectors.
    fields = (
        "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v",
        "omeg", "wetbulb", "tmp_stdev", "dew_stdev", "logp", "vtmp",
        "theta", "thetae", "wvmr", "relh", "meta", "date", "location",
        "latitude", "longitude", "sfc", "top", "missing", "strictQC",
        "srwind", "user_srwind", "bunkers", *OPTIONAL_SOURCE_SURFACE_FIELDS,
    )
    values = tuple(getattr(prof, name, None) for name in fields)
    # Serialization is only for hashing in-memory values; nothing is unpickled.
    return _science_identity(), hashlib.sha256(pickle.dumps(values, protocol=5)).digest()


def _file_identity(path, limit):
    try:
        before = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("input is not a bounded regular file")
    with path.open("rb") as stream:
        digest = hashlib.sha256(decoder._read_bounded(stream, before.st_size)).digest()
        after = os.fstat(stream.fileno())
    stamps = lambda value: (value.st_dev, value.st_ino, value.st_size,  # noqa: E731
                            value.st_mtime_ns, value.st_ctime_ns)
    if stamps(before) != stamps(after) or stamps(after) != stamps(path.stat()):
        raise ValueError("input changed while reading")
    return stamps(after), digest


def _input_identity(infile):
    if decoder._is_http_url(infile):
        return None
    try:
        path = Path(os.path.abspath(decoder._local_source_path(infile)))
        npz = path.suffix.lower() == ".npz"
        limit = decoder._positive_env_number(
            "SHARPMOD_MAX_NPZ_BYTES", decoder.DEFAULT_MAX_NPZ_BYTES, int
        ) if npz else decoder._max_remote_bytes()
        source = _file_identity(path, limit)
        if source is None:
            return None
        stem_sidecar = Path(os.path.splitext(str(path))[0] + ".json")
        sidecars = (stem_sidecar,) if npz else (
            Path(str(path) + ".json"), stem_sidecar)
        sidecar_limit = decoder._positive_env_number(
            "SHARPMOD_MAX_SIDECAR_BYTES", decoder.DEFAULT_MAX_SIDECAR_BYTES, int)
        return (
            str(path), source,
            tuple((str(sidecar), _file_identity(sidecar, sidecar_limit))
                  for sidecar in dict.fromkeys(sidecars)),
            backends.get_backend(), _science_identity(),
        )
    except (OSError, ValueError):
        return None


class PreparedInputCache:
    """Keep at most one unedited collection, cloning it for every request.

    Only the highlighted profile is upgraded, just as in an ordinary render.
    The authoritative derived oracle is populated by rendering, never eagerly.
    """

    def __init__(self):
        # shortcut: serial worker ownership; synchronize if shared across threads.
        self._key = self._decoded = None

    def load(self, infile):
        """Return isolated decoded input, or None for the reference decode path."""
        from sharpmod.rendering import cli

        key = _input_identity(infile)
        if key is None or key != self._key:
            self._key = self._decoded = None
        if key is None:
            return None
        decoded = self._decoded
        if decoded is None:
            # Decode failures propagate normally and never become cache entries.
            decoded = cli.decode(infile)
        try:
            prof = decoded[0].getHighlightedProf()
            if self._decoded is None:
                # A parcel fallback must retry its backend on the next request.
                if getattr(prof, "_sharpmod_downdraft", False) is None:
                    return decoded
                prof._sharpmod_prepared_derived = None
            isolated = deepcopy(decoded)
            if _input_identity(infile) != key:
                self._key = self._decoded = None
                return None
        except Exception:
            self._key = self._decoded = None
            return None
        self._key, self._decoded = key, decoded
        return isolated

    def retain_derived(self, infile, decoded):
        """Keep only lazily reached science whose source still matches the template."""
        if self._decoded is None or decoded is None:
            return
        try:
            if _input_identity(infile) != self._key:
                self._key = self._decoded = None
                return
            prof = decoded[0].getHighlightedProf()
            template = self._decoded[0].getHighlightedProf()
            cached = getattr(prof, "_sharpmod_prepared_derived", None)
            if cached is not None and cached[0] == _profile_signature(prof) \
                    and cached[0] == _profile_signature(template):
                # The template has never entered Qt; retain only the pure Profile.
                template._sharpmod_prepared_derived = deepcopy(cached)
        except Exception:
            # Optional reuse must never turn a successful render into a failure.
            self._key = self._decoded = None
