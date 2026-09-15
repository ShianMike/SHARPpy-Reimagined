from __future__ import annotations

import base64
from datetime import datetime, timezone
from time import monotonic

from qtpy.QtCore import QEventLoop, QTimer

from sharpmod.gui_environmental_context import EnvironmentalContextController
from sharpmod.gui_maps import PointMapWidget
from sharpmod.map_overlays import OverlayRaster
from sharpmod.satellite_context import GOES_OVERLAY_KEY, SatelliteOverlay
from sharpmod.surface_observations import (
    SURFACE_OVERLAY_KEY,
    SurfaceObservation,
    SurfaceObservationSet,
    SurfaceStation,
)


UTC = timezone.utc
WHEN = datetime(2026, 9, 13, 1, 45, tzinfo=UTC)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _pump_until(predicate, timeout_ms=3000):
    deadline = monotonic() + timeout_ms / 1000.0
    while monotonic() < deadline:
        if predicate():
            return
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()
    assert predicate(), "condition did not become true before timeout"


def test_environmental_controls_are_opt_in_and_run_off_the_gui_thread(
    qt_app, monkeypatch
):
    map_widget = PointMapWidget()
    map_widget.resize(720, 480)
    map_widget.set_extent(-101, -93, 32, 39, pad=0)
    map_widget.set_point(35.2, -97.4)

    def fake_satellite(when, *, bounds, channel, **_kwargs):
        raster = OverlayRaster(
            GOES_OVERLAY_KEY,
            "GOES satellite",
            PNG,
            bounds,
            valid_time=when,
            opacity=0.72,
            attribution="NOAA",
        )
        return SatelliteOverlay(raster, when, when, "G19", channel, 1.0, 2.0)

    def fake_surface(latitude, longitude, when, **_kwargs):
        station = SurfaceStation("KOUN", "Norman", latitude, longitude, 357)
        observation = SurfaceObservation(
            station, when, 24.0, 18.0, 180.0, 20.0
        )
        return SurfaceObservationSet(when, latitude, longitude, (observation,), 1)

    monkeypatch.setattr(
        "sharpmod.gui_environmental_context.fetch_satellite_overlay", fake_satellite
    )
    monkeypatch.setattr(
        "sharpmod.gui_environmental_context.fetch_surface_observations", fake_surface
    )

    controller = EnvironmentalContextController(map_widget)
    assert map_widget.overlay_keys() == ()
    controller.set_valid_time(WHEN)

    controller._sat_check.setChecked(True)
    _pump_until(lambda: map_widget.overlay(GOES_OVERLAY_KEY) is not None)
    assert controller.satellite_overlay().selected_time == WHEN
    assert "actual 2026-09-13" in controller._sat_status.text()

    controller._surface_check.setChecked(True)
    _pump_until(lambda: map_widget.overlay(SURFACE_OVERLAY_KEY) is not None)
    layer = map_widget.overlay(SURFACE_OVERLAY_KEY)
    assert layer.shapes[0].station_id == "KOUN"
    assert "1 displayed / 1 matched" in controller._surface_status.text()
    assert not map_widget.grab().isNull()

    controller._sat_check.setChecked(False)
    controller._surface_check.setChecked(False)
    assert map_widget.overlay(GOES_OVERLAY_KEY) is None
    assert map_widget.overlay(SURFACE_OVERLAY_KEY) is None
    controller.shutdown()
    map_widget.close()


def test_environment_context_rows_retain_time_coverage_and_sample_basis(
    qt_app,
):
    map_widget = PointMapWidget()
    controller = EnvironmentalContextController(map_widget)
    station = SurfaceStation("KOUN", "Norman", 35.2, -97.4)
    observation = SurfaceObservation(station, WHEN, None, 18.0, 180.0, 20.0)
    controller._surface = SurfaceObservationSet(
        WHEN, 35.2, -97.4, (observation,), 5, ("KOKC",)
    )
    raster = OverlayRaster(
        GOES_OVERLAY_KEY,
        "GOES",
        PNG,
        (-110, -85, 25, 45),
        valid_time=WHEN,
    )
    controller._satellite = SatelliteOverlay(
        raster, WHEN, WHEN, "G19", "infrared", 0.8, 2.0
    )
    rows = controller.context_rows()
    assert rows[0]["coverage"] == "80%"
    assert rows[1]["matched"] == 1
    assert rows[1]["discovered"] == 5
    controller.shutdown()
    map_widget.close()
