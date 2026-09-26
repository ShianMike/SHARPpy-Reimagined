"""Benchmark interactive four-panel map navigation and shared crosshair paints.

This is an offline Qt benchmark. It attaches a generated weather-like raster,
then sends real Qt mouse and wheel events through the same handlers as the app.
It reports interaction latency and counts expensive map paint work so regressions
can be attributed without downloading forecast data or starting a full test run.

Run::

    python benchmarks/benchmark_gui_maps.py
    python benchmarks/benchmark_gui_maps.py --repeat 40 --json out.json
    python benchmarks/benchmark_gui_maps.py --no-frame-cache --json baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_DRAW_COUNTS: Counter[str] = Counter()


def _track_calls(owner, name: str, label: str) -> None:
    """Count calls without timing the Python wrapper itself."""
    original = getattr(owner, name)

    def tracked(*args, **kwargs):
        _DRAW_COUNTS[label] += 1
        return original(*args, **kwargs)

    setattr(owner, name, tracked)


def _stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "median_ms": statistics.median(samples),
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "max_ms": max(samples),
    }


def _synthetic_raster():
    """Make a small PNG that exercises the raster draw path without network I/O."""
    from qtpy.QtCore import QBuffer, QIODevice
    from qtpy.QtGui import QColor, QImage, QPainter

    image = QImage(1024, 768, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#17251c"))
    painter = QPainter(image)
    for y in range(0, image.height(), 24):
        for x in range(0, image.width(), 24):
            painter.fillRect(
                x,
                y,
                24,
                24,
                QColor.fromHsv((x * 2 + y) % 360, 220, 210, 215),
            )
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    from sharpmod.maps.map_overlays import OverlayRaster

    payload = bytes(buffer.data())
    return OverlayRaster(
        key="hrrr_field",
        title="Synthetic HRRR field",
        image_bytes=payload,
        bounds=(-125.0, -66.0, 23.0, 50.0),
        short_name="refc",
        opacity=0.72,
    )


def _send_move(widget, x: float, y: float, *, buttons=None) -> None:
    from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QApplication

    buttons = Qt.NoButton if buttons is None else buttons
    position = QPointF(x, y)
    global_position = QPointF(
        widget.mapToGlobal(QPoint(round(x), round(y))))
    event = QMouseEvent(
        QEvent.MouseMove,
        position,
        global_position,
        Qt.NoButton,
        buttons,
        Qt.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _send_wheel(widget, x: float, y: float, delta: int) -> None:
    from qtpy.QtCore import QPoint, QPointF, Qt
    from qtpy.QtGui import QWheelEvent

    position = QPointF(x, y)
    global_position = QPointF(
        widget.mapToGlobal(QPoint(round(x), round(y))))
    event = QWheelEvent(
        position,
        global_position,
        QPoint(),
        QPoint(0, delta),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(event)


def _measure(app, repeat: int, operation) -> list[float]:
    samples: list[float] = []
    for index in range(repeat):
        start = time.perf_counter()
        operation(index)
        app.processEvents()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=24,
                        help="events per interaction (default: 24)")
    parser.add_argument("--json", type=Path,
                        help="also write raw samples to this path")
    parser.add_argument(
        "--no-frame-cache",
        action="store_true",
        help="measure the same paint path without the cursor-frame cache",
    )
    args = parser.parse_args(argv)
    if args.repeat < 5:
        parser.error("--repeat must be at least 5")

    from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
    from qtpy.QtGui import QMouseEvent, QPainter
    from qtpy.QtWidgets import QApplication

    from sharpmod.maps.map_field_inspection import FieldSample
    from sharpmod.ui.maps.basemap import MapBasemapMixin
    from sharpmod.ui.maps.crosshair_overlay import CrosshairOverlay
    from sharpmod.ui.maps.legend import MapLegendMixin
    from sharpmod.ui.maps.panels import MapPanelsView
    from sharpmod.ui.maps.point import PointMapMixin
    from sharpmod.ui.maps.presentation import MapPresentationMixin

    # Instrument the paint path before the Qt widgets are constructed so the
    # virtual overrides use these lightweight counters throughout the run.
    _track_calls(PointMapMixin, "paintEvent", "point_map_paint")
    _track_calls(CrosshairOverlay, "paintEvent", "crosshair_paint")
    _track_calls(CrosshairOverlay, "_draw_field_marker", "field_marker_draw")
    _track_calls(MapLegendMixin, "_draw_overlay_legend", "legend_draw")
    _track_calls(MapPresentationMixin, "_draw_place_labels", "place_labels_draw")
    _track_calls(MapBasemapMixin, "_draw_basemap", "basemap_draw")

    if args.no_frame_cache:
        def paint_without_cache(self, event) -> None:
            painter = QPainter(self)
            self._draw_point_map_frame(painter)
            painter.end()

        PointMapMixin._paint_cached_frame = paint_without_cache

    app = QApplication.instance() or QApplication([])
    view = MapPanelsView(panel_count=4)
    view.resize(1600, 1000)
    view.show()
    app.processEvents()

    raster = _synthetic_raster()
    for panel in view._panels:
        panel.map.set_overlay("hrrr_field", raster, visible=True)
        panel.map.sample_field_at = lambda lat, lon: FieldSample(
            product_key="refc",
            label="Composite Reflectivity",
            value=35.0 + (float(lon) + 100.0) * 0.2,
            units="dBZ",
            requested_lat=float(lat),
            requested_lon=float(lon),
        )
        panel.map.grab()
    app.processEvents()

    widget = view._panels[0].map
    width, height = widget.width(), widget.height()
    center = QPointF(width / 2.0, height / 2.0)

    # Finish first-paint and raster-decoding work before timing interactions.
    for panel in view._panels:
        panel.map.grab()
    app.processEvents()
    if any(panel.map._legend_bar is None for panel in view._panels):
        raise RuntimeError("synthetic HRRR raster did not produce a field legend")
    _send_move(widget, center.x(), center.y())
    app.processEvents()

    measurements: dict[str, list[float]] = {}
    phase_draws: dict[str, dict[str, int]] = {}

    def phase(name: str, operation) -> None:
        before = _DRAW_COUNTS.copy()
        measurements[name] = _measure(app, args.repeat, operation)
        phase_draws[name] = {
            key: _DRAW_COUNTS[key] - before[key]
            for key in (
                "point_map_paint",
                "crosshair_paint",
                "legend_draw",
                "place_labels_draw",
                "basemap_draw",
                "field_marker_draw",
            )
        }

    # Exercise both pan gestures through Qt events. Each move remains one
    # interaction sample and updates the preview.
    press_position = QPointF(width * 0.45, height * 0.5)
    global_press = QPointF(widget.mapToGlobal(
        QPoint(round(press_position.x()), round(press_position.y()))))
    for name, button in (("pan_move", Qt.MiddleButton),
                         ("left_pan_move", Qt.LeftButton)):
        press = QMouseEvent(
            QEvent.MouseButtonPress,
            press_position,
            global_press,
            button,
            button,
            Qt.NoModifier,
        )
        QApplication.sendEvent(widget, press)
        phase(
            name,
            lambda index: _send_move(
                widget,
                press_position.x() + 12.0 + (index % 3) * 2.0,
                press_position.y() + 8.0 + (index % 4) * 2.0,
                buttons=button,
            ),
        )
        release = QMouseEvent(
            QEvent.MouseButtonRelease,
            press_position,
            global_press,
            button,
            Qt.NoButton,
            Qt.NoModifier,
        )
        QApplication.sendEvent(widget, release)
        for panel in view._panels:
            panel.map._finish_map_preview()
        app.processEvents()

    # Wheel zoom uses the same cursor-anchored handler as the desktop app.
    phase(
        "wheel_zoom",
        lambda index: _send_wheel(
            widget,
            width * (0.35 + 0.3 * (index % 5) / 4),
            height * (0.35 + 0.3 * (index % 7) / 6),
            120 if index % 2 == 0 else -120,
        ),
    )
    for panel in view._panels:
        panel.map._finish_map_preview()
    app.processEvents()

    # Cursor-only movement keeps view bounds fixed and fans one crosshair out
    # to the other active maps; this is the reported high-frequency hot path.
    phase(
        "shared_hover",
        lambda index: _send_move(
            widget,
            90.0 + (index * 23) % max(1, width - 120),
            60.0 + (index * 17) % max(1, height - 100),
        ),
    )
    QApplication.sendEvent(widget, QEvent(QEvent.Leave))
    app.processEvents()
    if view.crosshair() is not None:
        raise RuntimeError("shared crosshair did not clear when the cursor left")

    payload = {
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "qt_platform": app.platformName(),
        "repeat": args.repeat,
        "active_panels": view.panel_count(),
        "map_size": [width, height],
        "synthetic_raster_png_bytes": len(raster.image_bytes),
        "field_legends_visible": sum(
            panel.map._legend_bar is not None for panel in view._panels),
        "crosshair_cleared_on_leave": view.crosshair() is None,
        "measurements": {
            name: {**_stats(values), "samples_ms": values}
            for name, values in measurements.items()
        },
        "paint_work_calls": phase_draws,
    }

    print(json.dumps(payload, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    view.hide()
    view.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
