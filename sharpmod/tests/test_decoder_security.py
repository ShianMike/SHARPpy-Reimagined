"""Security and metadata-parity regressions for sounding inputs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod import gui_viewer, gui_workers, render
from sharpmod.io import decoder
from sharpmod.tests._examples import examples_dir


def _write_marker(path):
    Path(path).write_text("unsafe pickle executed", encoding="utf-8")
    return "executed"


class _PicklePayload:
    def __init__(self, marker):
        self.marker = str(marker)

    def __reduce__(self):
        return _write_marker, (self.marker,)


def _portable_arrays(**overrides):
    count = 6
    arrays = {
        "pres": np.linspace(1000.0, 500.0, count),
        "hght": np.linspace(100.0, 5600.0, count),
        "tmpc": np.linspace(25.0, -15.0, count),
        "dwpc": np.linspace(20.0, -20.0, count),
        "wdir": np.linspace(180.0, 250.0, count),
        "wspd": np.linspace(5.0, 45.0, count),
        "omeg": np.zeros(count),
        "lat": 35.2,
        "lon": -97.4,
        "loc": "TEST",
        "model": "HRRR",
        "run": "2026-07-27 00:00",
        "valid": "2026-07-27 03:00",
    }
    arrays.update(overrides)
    return arrays


def test_npz_decoder_never_executes_pickled_object_data(tmp_path):
    marker = tmp_path / "pickle-ran.txt"
    archive = tmp_path / "unsafe.npz"
    np.savez(
        archive,
        **_portable_arrays(
            loc=np.array([_PicklePayload(marker)], dtype=object),
        ),
    )

    with pytest.raises(ValueError, match="Object arrays|unsafe object"):
        decoder.load_npz(archive)

    assert not marker.exists()


def test_gui_cache_validator_rejects_pickled_object_data(tmp_path):
    marker = tmp_path / "cache-pickle-ran.txt"
    archive = tmp_path / "unsafe-cache.npz"
    np.savez(
        archive,
        **_portable_arrays(
            loc=np.array([_PicklePayload(marker)], dtype=object),
        ),
    )
    archive.with_suffix(".json").write_text("{}", encoding="utf-8")

    assert gui_workers._portable_pair_valid(archive) is False
    assert not marker.exists()


def test_npz_decoder_rejects_inconsistent_profile_lengths(tmp_path):
    archive = tmp_path / "mismatched.npz"
    np.savez(archive, **_portable_arrays(wspd=np.arange(3.0)))

    with pytest.raises(ValueError, match="wspd.*levels"):
        decoder.load_npz(archive)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lat", 91.0, "latitude"),
        ("lon", -181.0, "longitude"),
        ("lat", np.inf, "latitude"),
    ],
)
def test_npz_decoder_rejects_invalid_coordinates(
        tmp_path, field, value, message):
    archive = tmp_path / f"invalid-{field}.npz"
    np.savez(archive, **_portable_arrays(**{field: value}))

    with pytest.raises(ValueError, match=message):
        decoder.load_npz(archive)


@pytest.mark.parametrize("invalid_observed", ["false", 0, 1])
def test_npz_decoder_rejects_non_boolean_observed_flag(
        tmp_path, invalid_observed):
    """Text and integer truthiness must not invert forecast classification."""
    archive = tmp_path / "invalid-observed.npz"
    np.savez(
        archive,
        **_portable_arrays(observed=invalid_observed),
    )

    with pytest.raises(ValueError, match="observed.*Boolean"):
        decoder.load_npz(archive)


def test_npz_decoder_accepts_boolean_observed_flag(tmp_path):
    archive = tmp_path / "forecast.npz"
    np.savez(archive, **_portable_arrays(observed=np.bool_(False)))

    collection, _station_id = decoder.load_npz(archive)

    assert collection.getMeta("observed") is False


def test_spc_decode_attaches_adjacent_coordinate_sidecar():
    sounding = (
        examples_dir()
        / "hrrr_point_36.68N_95.66W_f018.spc"
    )

    collection, station_id = render.decode(str(sounding))

    assert station_id == "HRRRpt"
    assert collection.getMeta("model") == "HRRR"
    assert collection.getMeta("lat") == pytest.approx(36.675168663242175)
    assert collection.getMeta("lon") == pytest.approx(-95.65655745938363)
    assert collection.getMeta("run") == datetime(2026, 6, 25, 6)
    assert collection.getMeta("observed") is False
    assert collection.getMeta("metadata_sidecar").endswith(".spc.json")


def test_spc_decode_uses_generated_common_stem_forecast_sidecar(tmp_path):
    """Extractor-style ``forecast.json`` preserves forecast classification."""
    source = examples_dir() / "hrrr_point_36.68N_95.66W_f018.spc"
    sounding = tmp_path / "forecast.spc"
    sounding.write_bytes(source.read_bytes())
    sounding.with_suffix(".json").write_text(
        '{"model": "HRRR", "run": "2026-06-25 06:00", '
        '"observed": false}',
        encoding="utf-8",
    )

    collection, _station_id = render.decode(str(sounding))

    assert collection.getMeta("model") == "HRRR"
    assert collection.getMeta("observed") is False
    assert collection.getMeta("metadata_sidecar").endswith("forecast.json")


def test_sidecar_rejects_string_observed_flag(tmp_path):
    sounding = tmp_path / "forecast.spc"
    sounding.with_suffix(".spc.json").write_text(
        '{"observed": "false"}', encoding="utf-8"
    )

    class Collection:
        def __init__(self):
            self.metadata = {"loc": "TEST"}

        def getMeta(self, key):
            return self.metadata[key]

        def setMeta(self, key, value):
            self.metadata[key] = value

    collection = Collection()
    decoder.attach_json_sidecar(collection, sounding)

    assert "observed" not in collection.metadata
    assert collection.metadata["metadata_sidecar"].endswith(".spc.json")


def test_remote_decode_downloads_once_before_trying_registry(monkeypatch):
    fetches = []
    attempted_paths = []
    collection = SimpleNamespace()

    def fake_fetch(url):
        fetches.append(url)
        return b"remote sounding"

    class RejectingDecoder:
        def __init__(self, path):
            attempted_paths.append(path)
            assert Path(path).is_file()
            raise ValueError("wrong format")

    class AcceptingDecoder:
        def __init__(self, path):
            attempted_paths.append(path)
            assert Path(path).read_bytes() == b"remote sounding"

        def getProfiles(self):
            return collection

        def getStnId(self):
            return "REMOTE"

    monkeypatch.setattr(render, "fetch_url", fake_fetch)
    monkeypatch.setattr(
        decoder,
        "getDecoders",
        lambda: {"first": RejectingDecoder, "second": AcceptingDecoder},
    )

    result, station_id = render.decode(
        "https://example.test/profile.spc?download=1")

    assert result is collection
    assert station_id == "REMOTE"
    assert fetches == ["https://example.test/profile.spc?download=1"]
    assert len(attempted_paths) == 2
    assert len(set(attempted_paths)) == 1
    assert not Path(attempted_paths[0]).exists()


def test_decoder_remote_read_has_timeout_size_limit_and_closes(
        monkeypatch):
    class Response:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True
            return False

        def read(self, limit):
            assert limit == 4
            return b"abcd"

    opened = Response()
    calls = []

    def fake_urlopen(url, *, timeout, context):
        calls.append((url, timeout, context))
        return opened

    monkeypatch.setenv("SHARPMOD_MAX_REMOTE_BYTES", "3")
    monkeypatch.setenv("SHARPMOD_REMOTE_TIMEOUT", "2.5")
    monkeypatch.setattr(decoder.ssl, "create_default_context", lambda **_k: "ctx")
    monkeypatch.setattr(decoder, "urlopen", fake_urlopen)

    class TextDecoder(decoder.Decoder):
        def _parse(self):
            return self._downloadFile()

    with pytest.raises(OSError, match="safety limit"):
        TextDecoder("https://example.test/profile.txt")

    assert calls == [("https://example.test/profile.txt", 2.5, "ctx")]
    assert opened.closed is True


def test_file_url_npz_uses_local_safe_loader(tmp_path):
    archive = tmp_path / "point.npz"
    np.savez(archive, **_portable_arrays())

    collection, station_id = render.decode(archive.as_uri())

    assert station_id == "TEST"
    assert collection.getMeta("lon") == pytest.approx(-97.4)


def test_interactive_metadata_uses_same_location_resolver_as_headless(
        monkeypatch):
    metadata = {
        "loc": "HRRRpt",
        "lat": 35.2,
        "lon": -97.4,
        "base_time": datetime(2026, 7, 27),
        "observed": False,
    }

    class Collection:
        _meta = metadata

        def getMeta(self, key):
            return metadata[key]

        def setMeta(self, key, value):
            metadata[key] = value

        def getCurrentDate(self):
            return datetime(2026, 7, 27)

    calls = []
    monkeypatch.setattr(
        gui_viewer,
        "_render",
        lambda: SimpleNamespace(
            _resolve_location_title=lambda collection, explicit_loc=None:
            calls.append((collection, explicit_loc))
        ),
    )

    collection = Collection()
    gui_viewer._fill_metadata(collection, "HRRRpt")

    assert calls == [(collection, None)]
