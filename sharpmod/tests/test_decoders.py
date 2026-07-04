"""Unit tests for the existing SHARPpy Reimagined decoders (task 2.2).

Exercises the modernized decoder registry in :mod:`sharpmod.io.decoder` against
the bundled example inputs, covering Requirement 12 (backward compatibility with
the existing decoders):

* **12.1** SPC tabular -> populated Profile
* **12.2** BUFKIT -> populated Profile
* **12.3** PECAN -> populated Profile (skipped: no bundled sample file)
* **12.4** WRF-ARW -> populated Profile (skipped: no bundled sample file)
* **12.5** HRRR ``.npz`` sidecar -> populated Profile *including* the OMEGA
  (vertical-velocity) column, via :func:`sharpmod.io.decoder.load_npz`
* **12.6** a malformed/garbage input is rejected with an error while any
  previously loaded Profile is left unchanged

Notes
-----
The bundled examples cover only the SPC, BUFKIT and HRRR ``.npz`` formats; the
PECAN and WRF-ARW cases are marked ``skip`` (no sample file in the workspace)
but the decoders are still registered and available (asserted separately).

The decoded profile is read straight off the ``ProfCollection`` raw store
(``_profs``) rather than through :meth:`getHighlightedProf` /
:meth:`getCurrentProfs`. Those public accessors eagerly upgrade the raw profile
to a legacy ``ConvectiveProfile`` whose PWV-climatology lookup still calls the
removed ``np.float`` alias and raises on modern NumPy -- that lazy full-profile
computation is out of scope for the decoder layer (it is modernized under task
8). These tests validate what the *decoder* produces: a profile exposing
populated ``pres``/``hght``/``tmpc``/``dwpc``/``wdir``/``wspd`` (and ``omeg``)
arrays.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import numpy.ma as ma
import pytest

from sharpmod.io import decoder as decoder_mod
from sharpmod.tests._examples import examples_dir

# The bundled example soundings live in "<repo root>/examples/soundings"
# (resolved robustly, with fallbacks, by ``examples_dir``).
EXAMPLES_DIR = examples_dir()

SPC_OAX = EXAMPLES_DIR / "14061619.OAX"
SPC_HRRR = EXAMPLES_DIR / "hrrr_point_36.68N_95.66W_f018.spc"
BUFKIT = EXAMPLES_DIR / "hrrr_kbvo_20260625_06z.buf"
HRRR_NPZ = EXAMPLES_DIR / "hrrr_point_36.68N_95.66W_f018.npz"

#: Core per-level arrays every decoded Profile must expose (Req 12.1-12.5).
CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")

#: Missing-value sentinel the decoders fill masked positions with.
MISSING = -9999.0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _raw_profile(prof_collection):
    """Return the raw decoded profile from a ProfCollection.

    Reads the collection's raw store directly so we observe exactly what the
    decoder produced, without triggering the legacy full-profile upgrade (see
    the module docstring).
    """
    profs = prof_collection._profs
    assert profs, "decoder produced an empty profile collection"
    member = next(iter(profs))
    members = profs[member]
    assert members, "decoder produced a member with no profiles"
    return members[0]


def _valid_values(field):
    """Return the finite, non-missing entries of a decoded field as an array."""
    arr = ma.asarray(field, dtype=float)
    # Drop masked entries, then drop the -9999 sentinel / non-finite values.
    data = np.asarray(arr.filled(MISSING), dtype=float)
    finite = data[np.isfinite(data)]
    return finite[finite != MISSING]


def _assert_populated(prof, fields=CORE_FIELDS):
    """Assert every core field is present and has >= 1 valid reported value."""
    lengths = set()
    for name in fields:
        assert hasattr(prof, name), f"profile is missing the {name!r} array"
        arr = np.asarray(ma.asarray(getattr(prof, name)))
        assert arr.size > 0, f"{name!r} array is empty"
        lengths.add(arr.size)
        valid = _valid_values(getattr(prof, name))
        assert valid.size > 0, f"{name!r} has no valid (non-missing) values"
    # All core fields describe the same set of levels.
    assert len(lengths) == 1, f"core fields have mismatched lengths: {lengths}"


def _decode_spc(path):
    """Decode an SPC tabular file and return its raw Profile."""
    spc_cls = decoder_mod.getDecoder("spc")
    return _raw_profile(spc_cls(str(path)).getProfiles())


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_exposes_all_builtin_decoders():
    """The modernized registry exposes every built-in format (Req 12.1-12.4)."""
    decoders = decoder_mod.getDecoders()
    for fmt in ("spc", "bufkit", "pecan", "wrf-arw"):
        assert fmt in decoders, f"missing built-in decoder {fmt!r}"


# --------------------------------------------------------------------------- #
# 12.1 SPC tabular
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not SPC_OAX.exists(), reason="no SPC .OAX example file")
def test_spc_oax_decodes_to_populated_profile():
    """Req 12.1: an SPC tabular (.OAX) file decodes to a populated Profile."""
    prof = _decode_spc(SPC_OAX)
    _assert_populated(prof)


@pytest.mark.skipif(not SPC_HRRR.exists(), reason="no SPC .spc example file")
def test_spc_hrrr_point_decodes_to_populated_profile():
    """Req 12.1: a second SPC tabular (.spc) example decodes correctly."""
    prof = _decode_spc(SPC_HRRR)
    _assert_populated(prof)


# --------------------------------------------------------------------------- #
# 12.2 BUFKIT
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not BUFKIT.exists(), reason="no BUFKIT example file")
def test_bufkit_decodes_to_populated_profile():
    """Req 12.2: a BUFKIT file decodes to a populated Profile."""
    buf_cls = decoder_mod.getDecoder("bufkit")
    prof = _raw_profile(buf_cls(str(BUFKIT)).getProfiles())
    _assert_populated(prof)


# --------------------------------------------------------------------------- #
# 12.3 PECAN / 12.4 WRF-ARW  (minimal valid samples synthesized at runtime)
# --------------------------------------------------------------------------- #
def _write_pecan_sample(path: Path) -> Path:
    """Write a minimal, valid single-member PECAN sounding to ``path``.

    Mirrors the PECAN section layout the decoder parses (member / TIME / STID /
    STIM header lines, a comma-separated column header, then data rows). Written
    with explicit LF newlines because the decoder's ``strptime`` on the ``TIME``
    line breaks on a trailing ``\\r`` (Windows ``\\r\\n``).
    """
    n = 12
    pres = np.linspace(1000.0, 200.0, n)
    hght = np.linspace(300.0, 12000.0, n)
    tmpc = np.linspace(25.0, -55.0, n)
    dwpc = tmpc - 5.0
    wdir = np.linspace(180.0, 270.0, n)
    wspd = np.linspace(10.0, 60.0, n)

    lines = [
        "MEMBER = MEAN",
        "TIME = 140616/1900",
        "STID = OAX SLAT = 41.32 SLON = -96.37 SELV = 350",
        "STIM = 0000",
        "PRES, HGHT, TEMP, DWPC, WDIR, WSPD",
    ]
    for i in range(n):
        lines.append("%.2f, %.2f, %.2f, %.2f, %.1f, %.1f" % (
            pres[i], hght[i], tmpc[i], dwpc[i], wdir[i], wspd[i]))
    path.write_text("\n".join(lines), newline="\n")
    return path


def _write_wrf_arw_sample(path: Path, nt=1, nz=12, ny=3, nx=3) -> Path:
    """Write a minimal, valid WRF-ARW ``wrfout`` netCDF to ``path``.

    Provides exactly the variables the ARW decoder reads (T, QVAPOR, P, PB,
    staggered PH/PHB, U, V, XLONG/XLAT, COS/SINALPHA, Times) on a tiny grid, so
    the nearest-point extraction yields a populated column.
    """
    from netCDF4 import Dataset

    G = 9.80665
    ds = Dataset(str(path), "w", format="NETCDF4")
    try:
        ds.START_DATE = "2014-06-16_19:00:00"
        ds.createDimension("Time", nt)
        ds.createDimension("bt", nz)
        ds.createDimension("bts", nz + 1)
        ds.createDimension("sn", ny)
        ds.createDimension("we", nx)
        ds.createDimension("DateStrLen", 19)

        pres_pa = np.linspace(1000.0, 150.0, nz) * 100.0
        theta = np.linspace(300.0, 370.0, nz)
        hstag = np.linspace(0.0, 16000.0, nz + 1)

        def _v4(name, col):
            var = ds.createVariable(name, "f4", ("Time", "bt", "sn", "we"))
            var[:] = np.broadcast_to(col[None, :, None, None], (nt, nz, ny, nx))

        _v4("T", theta - 300.0)          # perturbation potential temperature
        _v4("QVAPOR", np.full(nz, 0.008))  # kg/kg
        _v4("P", np.zeros(nz))           # perturbation pressure (Pa)
        _v4("PB", pres_pa)               # base-state pressure (Pa)
        _v4("U", np.full(nz, 10.0))      # m/s (grid-relative)
        _v4("V", np.full(nz, 5.0))

        for name in ("PH", "PHB"):
            var = ds.createVariable(name, "f4", ("Time", "bts", "sn", "we"))
            col = (hstag * G) if name == "PHB" else np.zeros(nz + 1)
            var[:] = np.broadcast_to(col[None, :, None, None], (nt, nz + 1, ny, nx))

        lons = np.linspace(-97.0, -95.0, nx)
        lats = np.linspace(40.0, 42.0, ny)
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        for name, grid in (("XLONG", lon_grid), ("XLAT", lat_grid)):
            var = ds.createVariable(name, "f4", ("Time", "sn", "we"))
            var[:] = np.broadcast_to(grid[None], (nt, ny, nx))
        for name, val in (("COSALPHA", 1.0), ("SINALPHA", 0.0)):
            var = ds.createVariable(name, "f4", ("Time", "sn", "we"))
            var[:] = val

        times = ds.createVariable("Times", "S1", ("Time", "DateStrLen"))
        times[0] = np.array(list("2014-06-16_19:00:00"), dtype="S1")
    finally:
        ds.close()
    return path


def test_pecan_decodes_to_populated_profile(tmp_path):
    """Req 12.3: a PECAN file decodes to a populated Profile."""
    pecan_file = _write_pecan_sample(tmp_path / "sample.pecan")
    pecan_cls = decoder_mod.getDecoder("pecan")
    prof = _raw_profile(pecan_cls(str(pecan_file)).getProfiles())
    _assert_populated(prof)


def test_wrf_arw_decodes_to_populated_profile(tmp_path):
    """Req 12.4: a WRF-ARW netCDF decodes to a populated Profile.

    The ARW decoder is invoked with the ``(path, lon, lat)`` tuple it expects
    and supplies winds as the ``u`` / ``v`` components (not ``wdir`` / ``wspd``),
    so wind population is asserted on those columns.
    """
    wrf_file = _write_wrf_arw_sample(tmp_path / "wrfout.nc")
    wrf_cls = decoder_mod.getDecoder("wrf-arw")
    prof = _raw_profile(wrf_cls((str(wrf_file), -96.0, 41.0)).getProfiles())
    _assert_populated(prof, fields=("pres", "hght", "tmpc", "dwpc", "u", "v"))


# --------------------------------------------------------------------------- #
# 12.5 HRRR .npz sidecar (with OMEGA)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not HRRR_NPZ.exists(), reason="no HRRR .npz example file")
def test_npz_decodes_with_populated_omega():
    """Req 12.5: the HRRR .npz sidecar decodes with core arrays *and* OMEGA."""
    prof_collection, loc = decoder_mod.load_npz(str(HRRR_NPZ))
    assert loc, "load_npz returned an empty location label"

    prof = _raw_profile(prof_collection)
    _assert_populated(prof)

    # The vertical-velocity (OMEGA) column must survive the .npz path.
    assert hasattr(prof, "omeg"), "profile is missing the OMEGA array"
    omeg = np.asarray(ma.asarray(getattr(prof, "omeg")))
    assert omeg.size == np.asarray(ma.asarray(prof.pres)).size, \
        "OMEGA array length does not match the pressure levels"
    assert _valid_values(prof.omeg).size > 0, "OMEGA has no valid values"


# --------------------------------------------------------------------------- #
# 12.6 malformed rejection leaves prior state intact
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not SPC_OAX.exists(), reason="no SPC .OAX example file")
def test_malformed_spc_is_rejected_and_prior_profile_unchanged(tmp_path):
    """Req 12.6: a malformed SPC file raises, prior Profile stays unchanged."""
    # Load a valid profile first and snapshot its arrays.
    good = _decode_spc(SPC_OAX)
    before = {f: np.array(ma.asarray(getattr(good, f)).filled(MISSING),
                           dtype=float, copy=True) for f in CORE_FIELDS}

    # Attempt to decode a garbage file of the same "format".
    bad_file = tmp_path / "garbage.txt"
    bad_file.write_text("this is not a sounding\njust random garbage\n1,2,3\n")

    spc_cls = decoder_mod.getDecoder("spc")
    with pytest.raises(Exception):
        spc_cls(str(bad_file))

    # The previously loaded profile is untouched.
    for f in CORE_FIELDS:
        after = np.array(ma.asarray(getattr(good, f)).filled(MISSING),
                         dtype=float)
        np.testing.assert_array_equal(
            after, before[f],
            err_msg=f"prior profile field {f!r} changed after a failed decode")


@pytest.mark.skipif(not HRRR_NPZ.exists(), reason="no HRRR .npz example file")
def test_malformed_npz_is_rejected_and_prior_profile_unchanged(tmp_path):
    """Req 12.6: a malformed .npz raises, prior Profile stays unchanged."""
    good_collection, _ = decoder_mod.load_npz(str(HRRR_NPZ))
    good = _raw_profile(good_collection)
    before = {f: np.array(ma.asarray(getattr(good, f)).filled(MISSING),
                          dtype=float, copy=True)
              for f in CORE_FIELDS + ("omeg",)}

    bad_file = tmp_path / "garbage.npz"
    bad_file.write_bytes(b"this is definitely not a valid npz archive")

    with pytest.raises(Exception):
        decoder_mod.load_npz(str(bad_file))

    for f in CORE_FIELDS + ("omeg",):
        after = np.array(ma.asarray(getattr(good, f)).filled(MISSING),
                         dtype=float)
        np.testing.assert_array_equal(
            after, before[f],
            err_msg=f"prior profile field {f!r} changed after a failed decode")


if __name__ == "__main__":  # pragma: no cover
    warnings.simplefilter("ignore")
    raise SystemExit(pytest.main([__file__, "-v"]))
