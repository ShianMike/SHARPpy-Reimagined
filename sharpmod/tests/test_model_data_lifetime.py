"""Forecast-model GUI data lifetime regressions."""

from __future__ import annotations

from sharpmod import gui


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
