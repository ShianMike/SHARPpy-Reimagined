"""Forecast-model GUI data lifetime regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import time

from sharpmod import gui, gui_picker, gui_workers
from sharpmod.model_hour_cache import ModelHourCache
from sharpmod.tests.era5_synth import make_era5_dataset
from sharpmod.tools import model_extract


class _FakeSignal:
    def __init__(self):
        self._callback = None

    def connect(self, callback):
        self._callback = callback

    def emit(self):
        self._callback()


class _FakeViewer:
    def __init__(self):
        self.destroyed = _FakeSignal()
        self.attribute = None
        self.enabled = None

    def setAttribute(self, attribute, enabled):
        self.attribute = attribute
        self.enabled = enabled


def test_model_data_is_removed_only_when_viewer_is_destroyed(tmp_path):
    data_dir = tmp_path / "model-fetch"
    data_dir.mkdir()
    npz_path = data_dir / "sounding.npz"
    json_path = data_dir / "sounding.json"
    grib_path = data_dir / "gfs" / "raw.grib2"
    grib_path.parent.mkdir()
    npz_path.write_bytes(b"npz")
    json_path.write_text("{}", encoding="utf-8")
    grib_path.write_bytes(b"grib")
    viewer = _FakeViewer()

    gui._retain_model_data_until_close(
        viewer, str(npz_path), str(data_dir))

    assert data_dir.exists()
    assert viewer.attribute == gui.Qt.WA_DeleteOnClose
    assert viewer.enabled is True

    viewer.destroyed.emit()

    assert not data_dir.exists()


def test_finished_model_worker_is_cleared_before_ui_becomes_ready():
    """A deleted QThread wrapper must not block the next model fetch."""

    class _FakeWorker:
        def __init__(self):
            self.deleted = False

        def deleteLater(self):
            self.deleted = True

    worker = _FakeWorker()
    ready_states = []
    picker = SimpleNamespace(_model_worker=worker, sender=lambda: worker)
    picker._set_model_busy = lambda busy: ready_states.append(
        (busy, picker._model_worker))

    gui.PickerWindow._on_model_fetch_finished(picker)

    assert picker._model_worker is None
    assert ready_states == [(False, None)]
    assert worker.deleted is True


def test_gui_workers_reuse_one_decoded_model_hour(tmp_path, monkeypatch):
    """New points in one model hour must not download or decode it again."""
    # This contract applies to a decoded full-hour GRIB. Point/subregion
    # backends intentionally include coordinates in the key so a sounding from
    # one location can never be reused for another.
    monkeypatch.setenv("SHARPMOD_POINT_BACKENDS", "grib")
    run_time = datetime(2026, 7, 14, 0, tzinfo=timezone.utc)
    dataset = make_era5_dataset(
        lats=[34.0, 35.0],
        lons=[260.0, 261.0],
        levels=[1000.0, 850.0, 700.0],
        times=[run_time],
        seed=41,
    )
    cache = ModelHourCache(max_entries=1)
    retrieve_calls = []

    def _fake_retrieve(config, run_dt, fxx, member=None, download_dir=None,
                       progress_callback=None, cancelled=None, lat=None,
                       lon=None):
        retrieve_calls.append((config.key, run_dt, fxx, member, download_dir))
        Path(download_dir, "subset.grib2").write_bytes(b"grib")
        if progress_callback is not None:
            progress_callback("downloading", 4)
            progress_callback("decoding", 4)
        return dataset, SimpleNamespace(grib="memory://gfs-hour")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", _fake_retrieve)
    progress = [[], []]
    outputs = []

    for index, (lat, lon) in enumerate(((35.0, -99.0), (34.0, -100.0))):
        point_dir = tmp_path / f"point-{index}"
        point_dir.mkdir()
        out_path = point_dir / "sounding.npz"
        worker = gui_workers._ModelFetchWorker(
            "gfs", lat, lon, run_time, 0, str(out_path),
            download_dir=str(point_dir), model_hour_cache=cache,
        )
        worker.progress.connect(
            lambda stage, total, target=progress[index]: target.append(
                (stage, total)
            )
        )
        worker.finished_ok.connect(
            lambda path, *_args, target=outputs: target.append(path)
        )
        worker.run()

    assert len(retrieve_calls) == 1
    assert outputs == [
        str(tmp_path / "point-0" / "sounding.npz"),
        str(tmp_path / "point-1" / "sounding.npz"),
    ]
    assert any(stage == "cached" for stage, _total in progress[1])
    cache_dir = Path(retrieve_calls[0][4])
    assert cache_dir.exists()
    assert cache_dir not in (tmp_path / "point-0", tmp_path / "point-1")
    with open(tmp_path / "point-1" / "sounding.json", encoding="utf-8") as handle:
        assert json.load(handle)["source_grib"] == "memory://gfs-hour"

    gui_workers._cleanup_model_data(
        outputs[0], str(tmp_path / "point-0")
    )
    assert cache_dir.exists()
    assert not (tmp_path / "point-0").exists()
    cache.clear()
    assert not cache_dir.exists()


def test_gui_debug_log_directory_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_GUI_LOG_DIR", str(tmp_path))

    assert gui._debug_log_path() == tmp_path / "sharpmod-gui.log"


def test_model_fetch_checks_grib_runtime_before_starting_worker(monkeypatch):
    """A broken native GRIB stack must fail on the GUI thread, not QThread."""
    shown = []
    statuses = []
    picker = SimpleNamespace(
        _model_worker=None,
        _model_config=lambda: model_extract.get_config("hrrr"),
        _model_lat=SimpleNamespace(value=lambda: 35.63),
        _model_lon=SimpleNamespace(value=lambda: -97.44),
        _model_point_ok=lambda: True,
        statusBar=lambda: SimpleNamespace(showMessage=statuses.append),
    )

    def _broken_runtime():
        raise model_extract.RetrievalError("ecCodes binary is unavailable")

    monkeypatch.setattr(
        model_extract, "require_runtime_dependencies", _broken_runtime,
        raising=False,
    )
    monkeypatch.setattr(
        gui_picker.QMessageBox, "critical",
        lambda _parent, _title, message: shown.append(message),
    )
    monkeypatch.setattr(
        gui_picker.tempfile, "mkdtemp",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("temporary fetch directory was created")),
    )

    gui.PickerWindow._model_fetch(picker)

    assert shown == [
        "Forecast model support is unavailable:\n"
        "ecCodes binary is unavailable"
    ]
    assert statuses == ["Forecast model support unavailable"]


class _FakeProgressWidget:
    def __init__(self):
        self.range = None
        self.value = None
        self.format = None
        self.visible = False

    def setRange(self, minimum, maximum):
        self.range = (minimum, maximum)

    def setValue(self, value):
        self.value = value

    def setFormat(self, text):
        self.format = text

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _FakeTextWidget:
    def __init__(self):
        self.text = ""
        self.visible = False

    def setText(self, text):
        self.text = text

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def test_cached_model_hour_has_explicit_progress_message():
    progress = _FakeProgressWidget()
    detail = _FakeTextWidget()
    button = _FakeTextWidget()
    statuses = []
    picker = SimpleNamespace(
        _model_progress=progress,
        _model_progress_detail=detail,
        _model_fetch_btn=button,
        _model_progress_stage="",
        _model_progress_total=0,
        statusBar=lambda: SimpleNamespace(showMessage=statuses.append),
    )

    gui.PickerWindow._on_model_fetch_progress(picker, "cached", 0)

    assert progress.visible is True
    assert detail.text == "Using cached model hour…"
    assert button.text == "Extracting…"
    assert statuses == ["Using cached model hour…"]


def test_model_download_progress_uses_bytes_written_and_expected_total(tmp_path):
    """The visible percentage follows the actual isolated GRIB file size."""
    grib = tmp_path / "hrrr" / "subset.grib2"
    grib.parent.mkdir()
    grib.write_bytes(b"x" * 250)
    progress = _FakeProgressWidget()
    detail = _FakeTextWidget()
    button = _FakeTextWidget()
    statuses = []
    worker = SimpleNamespace(_download_dir=str(tmp_path), _model="hrrr")
    picker = SimpleNamespace(
        _model_worker=worker,
        _model_progress=progress,
        _model_progress_detail=detail,
        _model_fetch_btn=button,
        _model_progress_stage="downloading",
        _model_progress_total=1000,
        _model_progress_started=time.monotonic() - 10.0,
        statusBar=lambda: SimpleNamespace(showMessage=statuses.append),
    )

    gui.PickerWindow._poll_model_fetch_progress(picker)

    assert progress.range == (0, 100)
    assert progress.value == 25
    assert progress.format == "25%"
    assert "250 B / 1000 B" in detail.text
    assert button.text == "Downloading\u2026 25%"
    assert statuses[-1].startswith("Downloading HRRR: 25%")
