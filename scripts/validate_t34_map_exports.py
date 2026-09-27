"""Deterministic no-network T34 map-export acceptance artifacts.

Builds the real main-map, four-panel, box-field and locator painters from
captured scientific state. Panel controllers remain disabled, so no provider
worker is constructed. Saved PNG pixels are compared with the exact preview
composition before the artifact is accepted.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QBuffer, QByteArray, Qt  # noqa: E402
from qtpy.QtGui import QColor, QImage, QPixmap  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from sharpmod import box_analysis, locator_presentation, map_overlays, render  # noqa: E402
from sharpmod.box_sounding import BoxRegion, plan_box_samples  # noqa: E402
from sharpmod.export_paths import export_directory  # noqa: E402
from sharpmod.export_presentation import ExportPresentation  # noqa: E402
from sharpmod.gui_export import ExportImageDialog  # noqa: E402
from sharpmod.gui_map_export import (  # noqa: E402
    capture_locator,
    capture_map,
    render_map_figure,
)
from sharpmod.gui_map_panels import MapPanelsView  # noqa: E402
from sharpmod.gui_maps import BoxFieldMapWidget, PointMapWidget  # noqa: E402
from sharpmod.gui_theme import apply_theme  # noqa: E402


RUN = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
REQUESTED = RUN + timedelta(hours=6)
CAPTURED = RUN + timedelta(hours=7)


def _settle(app, cycles=8):
    for _ in range(cycles):
        app.processEvents()


def _encoded_image(color: str) -> bytes:
    image = QImage(160, 100, QImage.Format_ARGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QBuffer.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode validation raster")
    return bytes(data)


def _raster(key: str, title: str, color: str, actual, opacity: float):
    return map_overlays.OverlayRaster(
        key="hrrr_field", title=title, short_name=key,
        image_bytes=_encoded_image(color), bounds=(-105.0, -87.0, 27.0, 48.0),
        valid_time=actual, retrieved_at=actual + timedelta(minutes=4),
        opacity=opacity, attribution="NOAA/NCEP HRRR",
    )


def _outlook():
    rings = map_overlays.rings_from_geometry({
        "type": "Polygon",
        "coordinates": [[
            [-99.0, 34.0], [-91.0, 34.0], [-91.0, 41.5],
            [-99.0, 41.5], [-99.0, 34.0],
        ]],
    })[0]
    shape = map_overlays.OverlayShape(
        rings=rings, bounds=map_overlays.bounds_of(rings),
        stroke="#ffae3d", fill="#8d4b18", label="SLGT", hatch=True,
    )
    return map_overlays.build_layer(
        "spc_outlook", "SPC tornado outlook", (shape,), short_name="TOR",
        valid_from=RUN, valid_to=RUN + timedelta(days=1),
        issued=RUN - timedelta(hours=1),
        attribution="NOAA/NWS Storm Prediction Center",
    )


def _main_map(app):
    widget = PointMapWidget()
    widget.setMinimumSize(1, 1)
    widget.resize(900, 620)
    widget.set_extent(-102.0, -90.0, 31.0, 44.0, pad=0.0)
    widget.set_point(36.45, -96.10)
    widget.set_box((34.8, -99.2, 39.2, -93.0))
    widget.set_domain((-105.0, -87.0, 27.0, 48.0), "HRRR CONUS")
    widget.set_forecast_reference(RUN, 6)
    widget.set_valid_time(REQUESTED)
    widget.set_overlay(
        "hrrr_field",
        _raster("refc", "Composite Reflectivity", "#4e9a55",
                REQUESTED - timedelta(hours=1), 0.72),
    )
    widget.set_overlay("spc_outlook", _outlook())
    widget.set_scale_locked("refc", True)
    widget.set_map_mode("history")
    widget.show()
    _settle(app)
    return widget, capture_map(
        widget, title="Historical HRRR analysis map", captured_at=CAPTURED)


def _panel_frames(app):
    view = MapPanelsView(panel_count=4)
    view.resize(1400, 940)
    view.ensurePolished()
    view.layout().activate()
    # Do not show this view: its production showEvent deliberately starts the
    # live field controllers. Fixed child sizes exercise the same painters and
    # keep this acceptance harness deterministic and network-free.
    for panel in view._panels:
        panel.map.setMinimumSize(1, 1)
        panel.map.resize(650, 420)
    view.set_point(36.45, -96.10)
    view.set_domain((-105.0, -87.0, 27.0, 48.0), "HRRR CONUS")
    products = (
        ("refc", "Composite Reflectivity", "#428bca"),
        ("mlcape", "Mixed Layer CAPE", "#d8932f"),
        ("shear-0-6km", "Bulk Shear: 0–6 km AGL", "#8f61bd"),
        ("stp", "Significant Tornado Parameter", "#bd4f58"),
    )
    for index, (panel, product) in enumerate(zip(view._panels, products)):
        key, title, color = product
        panel.map.set_extent(-102.0, -90.0, 31.0, 44.0, pad=0.0)
        panel.map.set_forecast_reference(RUN, 6)
        panel.map.set_valid_time(REQUESTED)
        panel.map.set_overlay(
            "hrrr_field",
            _raster(key, title, color,
                    REQUESTED + timedelta(minutes=15 * (index - 2)),
                    0.55 + index * 0.1),
        )
        panel.map.set_scale_locked(key, index % 2 == 0)
    _settle(app)
    frames = tuple(capture_map(
        panel.map, title=panel.title.text(), captured_at=CAPTURED)
                   for panel in view._panels)
    return view, frames


def _box_frame(app):
    region = BoxRegion.from_corners(34.8, -99.2, 39.2, -93.0)
    plan = plan_box_samples("hrrr", region, target_points=16)
    points = tuple(
        box_analysis.BoxPointAnalysis(
            row=point.row, col=point.col, lat=point.lat, lon=point.lon,
            request_id=point.request_id,
            values={"mucape": 250.0 + point.row * 550.0 + point.col * 180.0},
        )
        for point in plan.points
    )
    analysis = box_analysis.BoxAnalysis(
        plan=plan, points=points, tiers=(box_analysis.FAST_TIER,),
        run_time=RUN, valid_time=REQUESTED, fxx=6,
    )
    widget = BoxFieldMapWidget()
    widget.setMinimumSize(1, 1)
    widget.resize(900, 620)
    widget.set_analysis(analysis, "mucape")
    widget.set_box((region.lat0, region.lon0, region.lat1, region.lon1))
    widget.set_view(region, pad=0.12)
    widget.set_selected_cell(1, 2)
    widget._screen = "Organized convection validation screen"
    widget._screen_mask = tuple(
        tuple((row + col) % 3 == 0 for col in range(plan.cols))
        for row in range(plan.rows)
    )
    widget.show()
    _settle(app)
    frame = capture_map(
        widget, title="Box field · Most-Unstable CAPE · F006",
        captured_at=CAPTURED)
    frame = replace(
        frame, requested_time=REQUESTED, run=RUN, forecast_hour=6,
        time_header=("Box analysis · Valid 21 Sep 2026 18:00Z · "
                     "Run 21 Sep 2026 12:00Z · F006"),
        source_label="HRRR extracted box · Most-Unstable CAPE",
        legend=frame.legend + " · Hatched screen: Organized convection",
    )
    return widget, frame


class _Collection:
    def __init__(self):
        self._meta = {
            "lat": 35.25, "lon": -97.47,
            "requested_lat": 35.22, "requested_lon": -97.44,
            "loc": "Norman, Oklahoma", "model": "HRRR",
        }

    def getMeta(self, key):  # noqa: N802
        return self._meta.get(key)

    def setMeta(self, key, value):  # noqa: N802
        self._meta[key] = value

    def getCurrentDate(self):  # noqa: N802
        return REQUESTED


def _locator_frame():
    collection = _Collection()
    map_overlays.attach_locator_overlay(collection, _outlook())
    locator_presentation.set_collection_presentation(
        collection,
        locator_presentation.LocatorPresentation(
            link_mode=locator_presentation.LINK_PINNED,
            extent_mode=locator_presentation.EXTENT_CUSTOM,
            custom_bounds=(-100.2, 33.0, -94.2, 38.7),
            selected_area=(-99.2, 34.8, -93.0, 39.2),
            scale_units="imperial",
        ),
    )
    locator_presentation.set_overlay_status(
        collection, "spc_outlook", "available", family="risk", product="torn",
        detail="Captured 21 Sep 18Z outlook",
    )
    source = SimpleNamespace(
        prof_collections=(collection,), pc_idx=0,
        prof=SimpleNamespace(latitude=35.25, longitude=-97.47, date=REQUESTED),
        bg_color=QColor("#05090b"), fg_color=QColor("#ffffff"),
    )
    return capture_locator(source, captured_at=CAPTURED)


def _save_exact(pixmap, path):
    if not render.save_pixmap_png_atomic(pixmap, str(path)):
        raise RuntimeError(f"Could not publish {path}")
    loaded = QImage(str(path)).convertToFormat(QImage.Format_ARGB32)
    expected = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    if loaded != expected:
        raise RuntimeError(f"Saved pixels differ from preview composition: {path}")


def _save_widget(widget, path):
    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.transparent)
    widget.render(pixmap)
    _save_exact(pixmap, path)


def validate(text_scale: int):
    app = QApplication.instance() or QApplication([])
    render.install_font(app)
    apply_theme(app, color_style="standard", text_scale=text_scale)
    destination = export_directory() / "improvement-goal" / "t34"
    destination.mkdir(parents=True, exist_ok=True)
    suffix = f"standard-text{text_scale}"

    main = panels = box = dialog = None
    try:
        main, main_frame = _main_map(app)
        main_size = main.size()
        main_extent = tuple(main.view_bounds())
        if main_frame.extent != main_extent:
            raise RuntimeError("Capture did not preserve the effective viewport")
        panels, panel_frames = _panel_frames(app)
        box, box_frame = _box_frame(app)
        locator_frame = _locator_frame()

        small = ExportPresentation(1000, 900, "custom", True, "current")
        large = ExportPresentation(2400, 1600, "print", True, "current")
        standard = ExportPresentation(1600, 1200, "presentation", True, "current")
        artifacts = {
            f"{suffix}-main-small-1000x900.png": render_map_figure(
                (main_frame,), small, title="Captured historical analysis"),
            f"{suffix}-main-large-2400x1600.png": render_map_figure(
                (main_frame,), large, title="Captured historical analysis"),
            f"{suffix}-two-panel-2400x1600.png": render_map_figure(
                panel_frames[:2], large, title="HRRR two-panel ingredient analysis"),
            f"{suffix}-four-panel-2400x1600.png": render_map_figure(
                panel_frames, large, title="HRRR four-panel ingredient analysis"),
            f"{suffix}-box-1600x1200.png": render_map_figure(
                (box_frame,), standard, title="Captured box analysis"),
            f"{suffix}-locator-1600x1200.png": render_map_figure(
                (locator_frame,), standard, title="Captured sounding locator"),
        }
        for name, pixmap in artifacts.items():
            _save_exact(pixmap, destination / name)

        # Prove the capture remains stable after the live map moves and receives
        # a newer green field. The result must remain byte-identical.
        before = artifacts[f"{suffix}-main-large-2400x1600.png"].toImage()
        main.set_extent(-112.0, -103.0, 35.0, 45.0, pad=0.0)
        main.set_overlay(
            "hrrr_field", _raster("refc", "Newer unrelated field", "#00ff00",
                                  REQUESTED + timedelta(hours=3), 1.0))
        frozen_again = render_map_figure(
            (main_frame,), large, title="Captured historical analysis")
        if frozen_again.toImage() != before:
            raise RuntimeError("Captured map changed after a live newer frame arrived")
        if main.size() != main_size:
            raise RuntimeError("Export resized the live map")

        dialog = ExportImageDialog(
            lambda choices, title: render_map_figure(
                (main_frame,), choices, title=title),
            content="Historical HRRR analysis map · frozen requested/actual time",
            presentation=standard, title="Captured historical analysis",
        )
        dialog.resize(980, 900)
        dialog.show()
        _settle(app)
        dialog.capture_preview()
        if dialog.output_pixmap is None:
            raise RuntimeError(dialog.status.text())
        _save_widget(dialog, destination / f"{suffix}-preview-dialog.png")

        if tuple(main.view_bounds()) == main_extent:
            raise RuntimeError("Live-map mutation did not exercise extent freezing")
        if main_frame.requested_time != REQUESTED:
            raise RuntimeError("Requested time was not captured")
        if main_frame.layers[0].actual_time != REQUESTED - timedelta(hours=1):
            raise RuntimeError("Actual frame time was not captured")
        if len({frame.layers[0].actual_time for frame in panel_frames}) != 4:
            raise RuntimeError("Panel actual times were collapsed")
        if "Requested" not in locator_frame.selection or \
                "Sampled" not in locator_frame.selection:
            raise RuntimeError("Locator requested/sampled points were lost")
    finally:
        if dialog is not None:
            dialog.close()
            dialog.release()
        if panels is not None:
            panels.shutdown()
            panels.close()
        for widget in (main, box):
            if widget is not None:
                widget.close()
        _settle(app)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-scale", type=int, choices=(100, 200), default=100)
    args = parser.parse_args()
    validate(args.text_scale)


if __name__ == "__main__":
    main()
