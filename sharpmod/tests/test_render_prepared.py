"""Bounded worker input reuse must preserve the full reference render."""

from copy import deepcopy
from datetime import datetime
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sharppy.sharptab.prof_collection import ProfCollection

from sharpmod import backends
from sharpmod.bridge.sounding_view import validate_view
from sharpmod.rendering import cli, prepared
from sharpmod.sharptab import derived
from sharpmod.tests._examples import examples_dir
from sharpmod.viz.SPCWindow import _derived_profile


SAMPLE = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"


@pytest.fixture
def counted_decode(monkeypatch):
    """Cheap profiles for cache policy tests; real science is checked below."""
    calls = []

    def decode(infile):
        calls.append(infile)
        prof = SimpleNamespace(
            pres=np.array([1000., 900., 800.]),
            hght=np.array([100., 1000., 2000.]),
            tmpc=np.array([25., 18., 10.]), dwpc=np.array([20., 15., 5.]),
            wdir=np.array([180., 200., 220.]), wspd=np.array([10., 20., 30.]),
            omeg=np.ma.array([-0.5, -0.4, -0.3], mask=[False, True, False]),
            meta={"nested": {"source": "original"}},
        )
        return ProfCollection({"": [prof]}, [datetime(2026, 7, 5)],
                              target_type=SimpleNamespace), "TEST"

    monkeypatch.setattr(cli, "decode", decode)
    return calls


def test_one_entry_clones_arrays_metadata_and_derived(tmp_path, counted_decode):
    source, other = tmp_path / "one.spc", tmp_path / "two.spc"
    source.write_bytes(b"one")
    other.write_bytes(b"two")
    cache = prepared.PreparedInputCache()
    first = cache.load(str(source))
    prof = first[0].getHighlightedProf()
    assert prepared._profile_signature(prof) == prepared._profile_signature(deepcopy(prof))
    assert cache._decoded[0].getHighlightedProf()._sharpmod_prepared_derived is None
    wrapper = _derived_profile(prof)
    assert "_sharpmod_convective_oracle" not in vars(wrapper)
    cache.retain_derived(str(source), first)
    prof.tmpc[0] += 1
    prof.meta["nested"]["source"] = "changed"
    wrapper.tmpc[0] += 1
    again = cache.load(source.as_uri())
    pristine = again[0].getHighlightedProf()
    assert pristine.tmpc[0] == 25
    assert pristine.meta["nested"]["source"] == "original"
    assert _derived_profile(pristine).tmpc[0] == 25
    assert not np.shares_memory(prof.tmpc, pristine.tmpc)
    assert _derived_profile(pristine) is not wrapper
    assert len(counted_decode) == 1
    cache.load(str(other))
    assert cache._decoded[1] == "TEST"
    cache.load(str(source))
    assert len(counted_decode) == 3


@pytest.mark.parametrize("suffix,sidecar_suffix", [
    (".npz", ".json"), (".spc", ".spc.json"), (".spc", ".json"),
])
def test_sidecar_add_change_replace_remove_invalidates(
        tmp_path, counted_decode, suffix, sidecar_suffix):
    source = tmp_path / ("input" + suffix)
    source.write_bytes(b"source")
    sidecar = tmp_path / ("input" + sidecar_suffix)
    cache = prepared.PreparedInputCache()
    cache.load(str(source))
    sidecar.write_text('{"source":"aaa"}', encoding="utf-8")
    cache.load(str(source))
    stamp = sidecar.stat()
    sidecar.write_text('{"source":"bbb"}', encoding="utf-8")
    os.utime(sidecar, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    cache.load(str(source))
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(sidecar.read_bytes())
    os.replace(replacement, sidecar)
    cache.load(str(source))
    sidecar.unlink()
    cache.load(str(source))
    assert len(counted_decode) == 5


def test_content_identity_catches_rewrite_even_with_identical_stat(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"before")
    stamp = source.stat()
    monkeypatch.setattr(Path, "stat", lambda _path: stamp)
    monkeypatch.setattr(os, "fstat", lambda _fd: stamp)
    first = prepared._file_identity(source, 100)
    source.write_bytes(b"after!")
    second = prepared._file_identity(source, 100)
    assert first[0] == second[0]
    assert first[1] != second[1]


def test_missing_remote_large_and_uncopyable_inputs_use_reference(
        tmp_path, counted_decode, monkeypatch):
    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    cache = prepared.PreparedInputCache()
    assert cache.load(str(source)) is not None
    for unsupported in (str(tmp_path / "missing.spc"), "https://example.test/a.spc"):
        assert cache.load(unsupported) is None
        assert cache._decoded is None
    with monkeypatch.context() as patch:
        patch.setenv("SHARPMOD_MAX_REMOTE_BYTES", "3")
        assert cache.load(str(source)) is None
    with monkeypatch.context() as patch:
        def uncopyable(_value):
            raise TypeError("uncopyable")

        patch.setattr(prepared, "deepcopy", uncopyable)
        assert cache.load(str(source)) is None
        assert cache._decoded is None
    assert cache.load(str(source)) is not None
    assert len(counted_decode) == 3


def test_decode_errors_and_source_changes_during_preparation_are_not_cached(tmp_path, monkeypatch):
    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    cache = prepared.PreparedInputCache()
    calls = []

    def fail(infile):
        calls.append(infile)
        raise cli.RenderError(infile, "bad sounding")

    monkeypatch.setattr(cli, "decode", fail)
    for _ in range(2):
        with pytest.raises(cli.RenderError, match="bad sounding"):
            cache.load(str(source))
        assert cache._decoded is None
    assert len(calls) == 2

    def replaced(_infile):
        source.write_bytes(b"changed")
        return ProfCollection({"": [SimpleNamespace()]}, [datetime(2026, 7, 5)],
                              target_type=SimpleNamespace), "TEST"

    monkeypatch.setattr(cli, "decode", replaced)
    assert cache.load(str(source)) is None
    assert cache._decoded is None


def test_invalid_real_npz_can_be_replaced_and_retried(tmp_path):
    source = tmp_path / "source.npz"
    source.write_bytes(b"invalid archive")
    cache = prepared.PreparedInputCache()
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid portable sounding"):
            cache.load(str(source))
        assert cache._decoded is None
    source.write_bytes(SAMPLE.read_bytes())
    assert cache.load(str(source)) is not None


def test_only_highlighted_forecast_profile_is_prepared():
    source = examples_dir() / "hrrr_kbvo_20260625_06z.buf"
    cache = prepared.PreparedInputCache()
    decoded = cache.load(str(source))
    for collection in (decoded[0], cache._decoded[0]):
        profiles = [prof for member in collection._profs.values() for prof in member]
        assert len(profiles) > 1
        assert sum(type(prof) is collection._target_type for prof in profiles) == 1
        assert collection.getHighlightedProf()._sharpmod_prepared_derived is None


def test_backend_and_decoder_configuration_invalidate(tmp_path, counted_decode, monkeypatch):
    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    cache = prepared.PreparedInputCache()
    cache.load(str(source))
    first_backend = backends.get_backend()
    monkeypatch.setenv("SHARPMOD_MAX_SPC_LEVELS", "701")
    cache.load(str(source))
    backends.reset_backend_cache()
    cache.load(str(source))
    assert backends.get_backend() is not first_backend
    assert len(counted_decode) == 3


@pytest.mark.parametrize("field", [
    "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v",
    "omeg", "wetbulb", "tmp_stdev", "dew_stdev", "meta", "date", "location",
    "latitude", "longitude", "srwind", "user_srwind", "bunkers",
    "surface_relative_vorticity", "sfc_relative_vorticity", "surface_vorticity",
    "sfc_vorticity", "vorticity", "mask",
])
def test_derived_reuse_invalidates_every_input(tmp_path, counted_decode, field):
    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    decoded = prepared.PreparedInputCache().load(str(source))
    prof = decoded[0].getHighlightedProf()
    wrapper = _derived_profile(prof)
    assert _derived_profile(prof) is wrapper
    if field == "mask":
        prof.omeg.mask[0] = True
    elif field == "meta":
        prof.meta["nested"]["source"] = "changed"
    elif field in {"pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg"}:
        getattr(prof, field)[0] += 0.5
    elif field in {"date", "location"}:
        setattr(prof, field, datetime(2026, 8, 5) if field == "date" else "new")
    elif field in {"u", "v", "wetbulb", "tmp_stdev", "dew_stdev"}:
        setattr(prof, field, np.array([1., 2., 3.]))
    elif field in {"srwind", "user_srwind", "bunkers"}:
        setattr(prof, field, (1., 2., 3., 4.))
    else:
        setattr(prof, field, 8e-5)
    assert _derived_profile(prof) is not wrapper


@pytest.mark.parametrize("filename", [SAMPLE.name, "14061619.OAX"])
def test_exact_independently_reconstructed_oracle_is_cloned(filename):
    from sharppy.sharptab.profile import ConvectiveProfile

    source = str(examples_dir() / filename)
    cache = prepared.PreparedInputCache()
    decoded = cache.load(source)
    prof = decoded[0].getHighlightedProf()
    reference_prof = deepcopy(prof)
    del reference_prof._sharpmod_prepared_derived
    reference = _derived_profile(reference_prof)
    actual = _derived_profile(prof)
    for attr in ("lscp", "nstp", "modified_sherbe"):
        np.testing.assert_equal(getattr(actual, attr), getattr(reference, attr))
    oracle = derived._convective_oracle_profile(actual)
    assert type(oracle) is ConvectiveProfile
    assert oracle is not prof
    cache.retain_derived(source, decoded)
    cloned = cache.load(source)[0].getHighlightedProf()
    reused = _derived_profile(cloned)
    assert reused is not actual
    assert reused._sharpmod_convective_oracle is not oracle
    for attr in ("lscp", "nstp", "modified_sherbe"):
        np.testing.assert_equal(getattr(reused, attr), getattr(reference, attr))


def test_failed_science_in_decoded_profile_is_not_retained(tmp_path, counted_decode, monkeypatch):
    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    decode = cli.decode

    def fallback(infile):
        decoded = decode(infile)
        decoded[0].getHighlightedProf()._sharpmod_downdraft = None
        return decoded

    monkeypatch.setattr(cli, "decode", fallback)
    cache = prepared.PreparedInputCache()
    assert cache.load(str(source)) is not None
    assert cache._decoded is None
    assert cache.load(str(source)) is not None
    assert len(counted_decode) == 2


@pytest.mark.parametrize("failure", ["oracle", "derived"])
def test_failed_science_in_derived_profile_is_retried(
        tmp_path, counted_decode, monkeypatch, failure):
    from sharppy.sharptab import profile as sp_profile
    from sharpmod.sharptab.constants import KTS_PER_MS, is_missing

    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    cache = prepared.PreparedInputCache()
    decoded = cache.load(str(source))
    wrapper = _derived_profile(decoded[0].getHighlightedProf())
    healthy = wrapper.lapserate_sfc_1km
    attr = "lscp" if failure == "oracle" else "dcp"

    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary scientific calculation failure")

    with monkeypatch.context() as patch:
        if failure == "oracle":
            patch.setattr(sp_profile, "create_profile", fail)
        else:
            patch.setattr(derived, "dcp", fail)
        assert is_missing(getattr(wrapper, attr))
        cache.retain_derived(str(source), decoded)

    if failure == "oracle":
        oracle = SimpleNamespace(
            etop=500., ebottom=900.,
            mupcl=SimpleNamespace(bplus=2000., bminus=-80.),
            left_esrh=[-100.], ebwspd=20. * KTS_PER_MS,
        )
        monkeypatch.setattr(sp_profile, "create_profile", lambda **_kwargs: oracle)
        expected = -2.
    else:
        monkeypatch.setattr(derived, "dcp", lambda _prof: 1.)
        expected = 1.

    prof = cache.load(str(source))[0].getHighlightedProf()
    reference_prof = deepcopy(prof)
    del reference_prof._sharpmod_prepared_derived
    reused = _derived_profile(prof)
    assert getattr(reused, attr) == getattr(_derived_profile(reference_prof), attr) == expected
    if failure == "derived":
        assert reused.lapserate_sfc_1km == healthy
    assert len(counted_decode) == 1


def _assert_no_qt(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen or isinstance(value, type):
        return
    seen.add(id(value))
    assert not type(value).__module__.startswith(("PySide", "PyQt", "qtpy"))
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_qt(item, seen)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _assert_no_qt(item, seen)
    elif hasattr(value, "__dict__") and not callable(value):
        _assert_no_qt(vars(value), seen)


def _render_pair(source, directory, cache, view, *, parcel="MU", mode="hd"):
    from qtpy.QtGui import QImage

    directory.mkdir()
    reference, actual = directory / "reference.png", directory / "prepared.png"
    expected, metadata = {}, {}
    cli.render(str(source), str(reference), view=view, view_result=expected,
               parcel=parcel, image_mode=mode, show_locator=False)
    cli.render(str(source), str(actual), view=view, view_result=metadata,
               parcel=parcel, image_mode=mode, show_locator=False, _prepared=cache)
    assert cache._decoded is not None
    assert metadata == expected
    assert not QImage(str(reference)).isNull()
    assert QImage(str(actual)) == QImage(str(reference))
    assert actual.read_bytes() == reference.read_bytes()
    _assert_no_qt(cache._decoded)
    return metadata, actual.read_bytes()


def test_complete_reference_png_and_view_metadata_parity(tmp_path, qt_app):
    cache = prepared.PreparedInputCache()
    _render_pair(SAMPLE, tmp_path / "pristine", cache, None)
    original, png = _render_pair(SAMPLE, tmp_path / "original", cache, validate_view({"edits": []}))
    temp = next(point for point in original["points"] if point["id"] == "tmpc:0")
    wind = next(point for point in original["points"] if point["id"] == "wind:0")
    right = next(point for point in original["points"] if point["id"] == "storm:-1")
    views = [
        {"edits": [], "chart": "theta", "panel": "COND STP"},
        {"edits": [{"index": 0, "values": {"tmpc": temp["values"][0]["value"] + 0.5}}],
         "chart": "cape", "panel": "FIRE"},
        {"edits": [{"index": 0, "values": {"u": wind["values"][0]["value"] + 1,
                                                "v": wind["values"][1]["value"] - 1}}],
         "chart": "srw", "panel": "WINTER"},
        {"edits": [{"index": -1, "values": {"u": 12., "v": 8.}},
                   {"index": -2, "values": {"u": -10., "v": 5.}}]},
        {"edits": [], "drag": {"target": "storm:-1", "x": right["x"] + 0.005,
                                   "y": right["y"]}},
    ]
    for index, view in enumerate(views):
        _render_pair(SAMPLE, tmp_path / str(index), cache, validate_view(view),
                     parcel="ML" if index == 0 else "MU")
    reset, reset_png = _render_pair(SAMPLE, tmp_path / "reset", cache, validate_view({"edits": []}))
    assert reset == original
    assert reset_png == png
    assert cache._decoded[0].getHighlightedProf()._sharpmod_prepared_derived is not None


@pytest.mark.parametrize("variant", ["observed", "missing"])
def test_observed_and_missing_inputs_match_reference(tmp_path, qt_app, variant):
    source = examples_dir() / "14061619.OAX"
    if variant == "missing":
        source = tmp_path / "missing.npz"
        with np.load(SAMPLE, allow_pickle=False) as archive:
            columns = dict(archive)
        columns["omeg"][:] = -9999.
        for name in ("tmpc", "dwpc", "wdir", "wspd"):
            columns[name][10] = -9999.
        columns["surface_relative_vorticity"] = 8e-5
        np.savez(source, **columns)
    cache = prepared.PreparedInputCache()
    _render_pair(source, tmp_path / "first", cache, validate_view({"edits": []}))
    _render_pair(source, tmp_path / "theta", cache,
                 validate_view({"edits": [], "chart": "theta", "panel": "WINTER"}),
                 mode="lossless")


def test_replaced_real_source_and_sidecar_match_reference(tmp_path, qt_app):
    source = tmp_path / "source.spc"
    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.spc"
    source.write_bytes(example.read_bytes())
    sidecar = Path(str(source) + ".json")
    sidecar.write_text('{"model":"First"}', encoding="utf-8")
    cache = prepared.PreparedInputCache()
    view = validate_view({"edits": [], "chart": "theta", "panel": "STP STATS"})
    _render_pair(source, tmp_path / "initial", cache, view)
    old = cache._decoded
    replacement = tmp_path / "replacement.spc"
    replacement.write_bytes((examples_dir() / "14061619.OAX").read_bytes())
    os.replace(replacement, source)
    sidecar.write_text('{"model":"Other"}', encoding="utf-8")
    _render_pair(source, tmp_path / "replaced", cache, view)
    assert cache._decoded is not old
    assert cache._decoded[0].getMeta("model") == "Other"


def test_worker_uses_fresh_clone_for_each_request(tmp_path, counted_decode, monkeypatch):
    from sharpmod.bridge import render_worker, server

    source = tmp_path / "source.spc"
    source.write_bytes(b"source")
    requests = [{"id": index, "infile": str(source), "outfile": str(tmp_path / "out.png"),
                 "parcel": "MU", "image_mode": "hd"} for index in range(2)]
    outgoing = io.BytesIO()
    incoming = io.BytesIO(b"\n".join(json.dumps(request).encode() for request in requests))
    monkeypatch.setattr(server, "_claim_process_streams", lambda: (incoming, outgoing, None))
    monkeypatch.setattr(render_worker.logging, "basicConfig", lambda **_kwargs: None)

    def render(_infile, _outfile, **kwargs):
        prof = kwargs["_prepared"].load(_infile)[0].getHighlightedProf()
        assert prof.tmpc[0] == 25
        prof.tmpc[0] += 1

    monkeypatch.setattr(cli, "render", render)
    assert render_worker.main() == 0
    assert all(reply["ok"] for reply in map(json.loads, outgoing.getvalue().splitlines()))
    assert len(counted_decode) == 1
