"""Hodograph height-level marker regressions."""

from __future__ import annotations

import os
from itertools import combinations
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from qtpy import QtCore, QtGui
from qtpy.QtGui import QColor, QPixmap
from qtpy.QtWidgets import QApplication

from sharpmod.viz.hodo_levels import (
    HEIGHT_LEVELS_M,
    MARKER_RADIUS_PX,
    draw_hodo_height_levels,
    height_level_points,
    layout_height_level_markers,
    _height_level_exclusions,
    _marker_font_pixel_size,
    _marker_font_letter_spacing,
    _marker_text_color,
)
from sharpmod.viz.hodo_locator import locator_rect_for_widget


def _widget():
    profile = SimpleNamespace(
        sfc=0,
        hght=np.ma.array([100.0, 600.0, 1100.0, 3100.0, 6100.0,
                          9100.0, 12100.0, 13100.0]),
        u=np.ma.array([0.0, 5.0, 10.0, 30.0, 60.0, 90.0, 120.0, 130.0]),
        v=np.ma.array([0.0, 2.5, 5.0, 15.0, 30.0, 45.0, 60.0, 65.0]),
    )
    pixmap = QPixmap(420, 320)
    pixmap.fill(QColor("black"))
    return SimpleNamespace(
        prof=profile,
        plotBitMap=pixmap,
        tlx=0,
        tly=0,
        brx=420,
        bry=320,
        colors=("#00ff00", "#ffff00", "#ff0000", "#ff80ff"),
        fg_color=QColor("#ffffff"),
        bg_color=QColor("#000000"),
        uv_to_pix=lambda u, v: (80.0 + u * 2.0, 260.0 - v * 2.0),
    )


def test_all_standard_agl_levels_are_defined_on_hodograph():
    QApplication.instance() or QApplication([])
    points = height_level_points(_widget())

    assert tuple(point[1] for point in points) == HEIGHT_LEVELS_M
    assert tuple(point[0] for point in points) == (
        "0.5", "1", "3", "6", "9", "12")
    assert points[0][2:] == (90.0, 255.0)
    assert points[-1][2:] == (320.0, 140.0)


def test_height_level_overlay_paints_numbered_dots():
    QApplication.instance() or QApplication([])
    widget = _widget()
    before = widget.plotBitMap.toImage().pixelColor(90, 255)

    assert draw_hodo_height_levels(widget)

    after = widget.plotBitMap.toImage().pixelColor(90, 255)
    assert after != before


def test_external_painter_keeps_markers_out_of_one_x_backing_bitmap():
    QApplication.instance() or QApplication([])
    widget = _widget()
    target = QPixmap(widget.plotBitMap.size())
    target.fill(QColor("black"))
    backing_before = widget.plotBitMap.toImage().pixelColor(90, 255)
    target_before = target.toImage().pixelColor(90, 255)

    painter = QtGui.QPainter(target)
    try:
        assert draw_hodo_height_levels(widget, painter=painter)
        assert painter.isActive()
    finally:
        painter.end()

    assert widget.plotBitMap.toImage().pixelColor(90, 255) == backing_before
    assert target.toImage().pixelColor(90, 255) != target_before


def test_every_dot_uses_a_solid_black_numeral():
    for color in (
            "#ff00ff", "#ff0000", "#00ff00", "#ffff00", "#00ffff",
            "#0000aa", "#333333"):
        assert _marker_text_color(
            QColor(color), QtGui).name() == "#000000"


def test_multi_character_marker_labels_leave_circle_edge_clearance():
    assert _marker_font_pixel_size("0.5") == 7
    assert _marker_font_pixel_size("12") == 8
    assert _marker_font_pixel_size("1") == 8
    assert _marker_font_pixel_size("9") == 8
    assert _marker_font_letter_spacing("0.5") == -0.5
    assert _marker_font_letter_spacing("12") == 0.0
    assert _marker_font_letter_spacing("1") == 0.0


def test_collocated_height_levels_are_deterministically_deconflicted():
    points = tuple(
        (label, level, 150.0, 150.0)
        for label, level in zip(
            ("0.5", "1", "3", "6", "9", "12"),
            HEIGHT_LEVELS_M,
            strict=True,
        )
    )

    first = layout_height_level_markers(
        points, (0.0, 0.0, 320.0, 300.0))
    second = layout_height_level_markers(
        points, (0.0, 0.0, 320.0, 300.0))

    assert first == second
    assert len(first) == len(HEIGHT_LEVELS_M)
    minimum_separation = MARKER_RADIUS_PX * 2.0 + 2.0
    for left, right in combinations(first, 2):
        distance = np.hypot(left[4] - right[4], left[5] - right[5])
        assert distance >= minimum_separation - 1e-6


def test_height_markers_are_moved_outside_locator_rectangle():
    points = tuple(
        (label, level, 80.0, 50.0)
        for label, level in zip(
            ("0.5", "1", "3", "6", "9", "12"),
            HEIGHT_LEVELS_M,
            strict=True,
        )
    )
    locator = (1.0, 1.0, 151.0, 97.0)

    layout = layout_height_level_markers(
        points,
        (2.0, 2.0, 418.0, 318.0),
        exclusions=(locator,),
    )

    assert len(layout) == len(HEIGHT_LEVELS_M)
    for _label, _height, _source_x, _source_y, x, y in layout:
        assert (
            x - MARKER_RADIUS_PX >= locator[2]
            or x + MARKER_RADIUS_PX <= locator[0]
            or y - MARKER_RADIUS_PX >= locator[3]
            or y + MARKER_RADIUS_PX <= locator[1]
        )


def test_height_markers_avoid_recorded_hodograph_annotations():
    QApplication.instance() or QApplication([])
    widget = _widget()
    annotation = QtCore.QRectF(125.0, 215.0, 45.0, 30.0)
    widget._sharpmod_hodo_annotation_rects = [annotation]
    exclusions = _height_level_exclusions(widget, QtCore)

    assert any(
        QtCore.QRectF(rect) == annotation
        for rect in exclusions
    )

    layout = layout_height_level_markers(
        height_level_points(widget),
        (2.0, 2.0, 418.0, 318.0),
        exclusions=exclusions,
    )
    for _label, _height, _source_x, _source_y, x, y in layout:
        assert (
            x - MARKER_RADIUS_PX >= annotation.right()
            or x + MARKER_RADIUS_PX <= annotation.left()
            or y - MARKER_RADIUS_PX >= annotation.bottom()
            or y + MARKER_RADIUS_PX <= annotation.top()
        )


def test_moved_height_markers_paint_after_locator_exclusion():
    QApplication.instance() or QApplication([])
    widget = _widget()
    widget.prof.latitude = 39.0319
    widget.prof.longitude = -88.6713
    widget.uv_to_pix = lambda _u, _v: (80.0, 50.0)
    points = height_level_points(widget)
    locator = locator_rect_for_widget(widget, QtCore)
    layout = layout_height_level_markers(
        points,
        (2.0, 2.0, 418.0, 318.0),
        exclusions=(locator,),
    )

    assert draw_hodo_height_levels(widget)

    image = widget.plotBitMap.toImage()
    for _label, _height, _source_x, _source_y, x, y in layout:
        sample = image.pixelColor(round(x + MARKER_RADIUS_PX - 2.0), round(y))
        assert sample.name() != "#000000"


def test_markers_are_compact():
    assert MARKER_RADIUS_PX < 7.0
