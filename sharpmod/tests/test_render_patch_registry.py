"""Ordered, version-gated SHARPpy render monkeypatch installation."""

from __future__ import annotations

import builtins
from datetime import datetime
from types import SimpleNamespace

import pytest

from sharpmod import render
from sharpmod.tools import model_extract
from sharpmod.render_patch_registry import (
    PatchSpec,
    RenderPatchError,
    UnsupportedSHARPpyVersion,
    apply_patch_registry,
    detected_sharppy_version,
    validate_sharppy_version,
)
from sharpmod.render_patch_groups import PANEL_PATCHES, patch_names_for_panel


def test_installed_sharppy_version_is_explicitly_supported():
    detected = detected_sharppy_version()

    assert detected == "1.4.0a5"
    assert validate_sharppy_version() == detected


def test_unsupported_sharppy_stops_before_mutation():
    called = []

    with pytest.raises(UnsupportedSHARPpyVersion, match="9.9"):
        apply_patch_registry(
            [PatchSpec("example", lambda: called.append(True))],
            sharppy_version="9.9",
        )

    assert called == []


def test_registry_validates_all_names_before_installing_any_patch():
    called = []
    patches = [
        PatchSpec("same", lambda: called.append("first")),
        PatchSpec("same", lambda: called.append("second")),
    ]

    with pytest.raises(RenderPatchError, match="duplicate"):
        apply_patch_registry(patches, sharppy_version="1.4.0a5")

    assert called == []


def test_registry_preserves_order_and_reports_installed_names():
    called = []
    patches = [
        PatchSpec("first", lambda: called.append("first")),
        PatchSpec("second", lambda: called.append("second")),
    ]

    installed = apply_patch_registry(
        patches, sharppy_version="1.4.0a5")

    assert called == ["first", "second"]
    assert installed == ("first", "second")


def test_real_patch_installer_failure_is_named_and_not_silenced(monkeypatch):
    """A missing vendored module must stop startup with the patch name."""
    original_import = builtins.__import__

    def fail_skew_import(name, *args, **kwargs):
        if name == "sharppy.viz.skew":
            raise ImportError("simulated missing skew module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(render, "_title_override_installed", False)
    monkeypatch.setattr(builtins, "__import__", fail_skew_import)

    with pytest.raises(
        RenderPatchError,
        match=r"title\.override.*simulated missing skew module",
    ):
        apply_patch_registry(
            [PatchSpec("title.override", render._install_title_override)],
            sharppy_version="1.4.0a5",
        )


def test_renderer_declares_one_named_spec_per_patch_installer():
    patches = render.render_patch_specs()
    names = [patch.name for patch in patches]

    assert len(patches) == 37
    assert len(names) == len(set(names))
    assert names[0] == "skewt.user-parcel-backend"
    assert names[1] == "title.override"
    assert "hodo.height-levels" in names
    assert "skewt.lapse-rate-label-transparency" in names
    assert "hodo.storm-motion-label-transparency" in names
    assert names.index("hodo.zoom") < names.index("hodo.mean-wind-default")
    # The transparency patch wraps whatever ``draw_max_lapse_rate_layer`` it
    # finds, so the placement it is meant to describe has to be installed first.
    assert (
        names.index("skewt.lapse-rate-label-placement")
        < names.index("skewt.lapse-rate-label-transparency")
    )
    assert names.index("hodo.locator") < names.index("hodo.height-levels")
    # Both wrap ``plotData``, and the box-mean callout has to composite on top
    # of the frame outline rather than under it.
    assert (names.index("skewt.frame-on-top")
            < names.index("skewt.box-mean-badge"))
    # Frame matching wraps whatever ``draw_frame`` the patches above leave
    # behind, so it has to be installed after all of them.
    assert names[-1] == "panels.match-skewt-frames"
    assert (
        names.index("tables.spacing")
        < names.index("panels.match-skewt-frames")
    )
    assert (
        names.index("slinky.title-fit")
        < names.index("panels.match-skewt-frames")
    )
    # The fire and winter text fits both replace their panel's ``draw_frame``,
    # so frame matching has to wrap the replacement rather than the original.
    for panel_fit in ("winter-text.fit", "fire-text.fit"):
        assert (
            names.index(panel_fit)
            < names.index("panels.match-skewt-frames")
        ), panel_fit


def test_patch_declarations_have_explicit_panel_ownership():
    assert len(PANEL_PATCHES) == 37
    assert patch_names_for_panel("hodo") == (
        "hodo.0500",
        "hodo.zoom",
        "hodo.mean-wind-default",
        "hodo.interpolation-menu",
        "hodo.label-fit",
        "hodo.locator",
        "hodo.height-levels",
        "hodo.storm-motion-label-transparency",
    )
    assert "skewt.surface-label-mask" in patch_names_for_panel("skewt")
    assert PANEL_PATCHES[-1].panel == "panels"


def test_the_lapse_rate_label_patch_installs_on_the_vendored_skewt():
    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    assert plotSkewT._sharpmod_lapse_rate_label_transparent is True


def test_the_rect_suppressing_painter_drops_only_draw_rect():
    """The patch removes a background plate without restating the draw method.

    Everything about the max-lapse-rate label -- its tier colours, geometry, and
    text -- stays in the vendored method. Only the opaque rectangle it fills
    first is dropped, so this proxy has to forward every other painter call
    untouched or the label would lose its bracket or its colour.
    """
    calls = []

    class _Recorder:
        def __getattr__(self, name):
            def record(*_args, **_kwargs):
                calls.append(name)
                return name
            return record

    proxy = render._RectSuppressingPainter(_Recorder())

    assert proxy.drawRect(1, 2, 3, 4) is None, "the plate must be dropped"
    assert proxy.drawText("rect", 0, "6.5 C/km") == "drawText"
    proxy.setPen("pen")
    proxy.setBrush("brush")
    proxy.drawLine(0, 0, 1, 1)
    proxy.setFont("font")
    proxy.setClipping(False)

    assert "drawRect" not in calls
    assert calls == ["drawText", "setPen", "setBrush", "drawLine",
                     "setFont", "setClipping"]


def test_hodo_height_markers_use_the_live_scale_aware_overlay():
    render.install_render_patches()
    from sharppy.viz.hodo import plotHodo

    assert plotHodo._sharpmod_live_overlay_host is True
    overlays = tuple(plotHodo._sharpmod_live_overlays)
    assert [overlay.__name__ for overlay in overlays].count(
        "height_level_overlay") == 1


@pytest.mark.parametrize(
    "model",
    [config.label for config in model_extract.available_models()],
)
def test_forecast_model_title_includes_full_run_and_valid_dates(model):
    class Collection:
        metadata = {
            "model": model,
            "run": datetime(2026, 7, 14, 0),
            "base_time": datetime(2026, 7, 14, 0),
            "lat": 41.54,
            "lon": -92.93,
        }

        def getMeta(self, key):  # noqa: N802 - upstream API shape
            return self.metadata.get(key)

        def getCurrentDate(self):  # noqa: N802 - upstream API shape
            return datetime(2026, 7, 14, 6)

    render._install_title_override()
    from sharppy.viz.skew import plotSkewT

    title = plotSkewT.getPlotTitle(SimpleNamespace(prof=None), Collection())

    assert title == (
        f"   {model} 2026-07-14 00z, F006  VALID: Tue 2026-07-14 06z"
        "  @41.54\N{DEGREE SIGN}N 92.93\N{DEGREE SIGN}W"
    )


class _RecordingPainter:
    """Captures what ``drawTitles`` asks to draw, and where."""

    def __init__(self):
        self.draws = []

    def setClipping(self, *_args):
        pass

    def setFont(self, *_args):
        pass

    def setPen(self, *_args):
        pass

    def drawText(self, rect, align, text):
        self.draws.append(
            SimpleNamespace(
                x=rect.x(), y=rect.y(),
                width=rect.width(), height=rect.height(),
                align=align, text=text))


def _title_host(titles, *, width=900, pad=10, height=20):
    """A stand-in for ``plotSkewT`` carrying only what ``drawTitles`` reads."""
    from qtpy.QtGui import QColor, QFont

    collections = [
        SimpleNamespace(
            getCurrentDate=lambda: datetime(2026, 7, 14, 6), _title=title)
        for title in titles
    ]
    return SimpleNamespace(
        prof_collections=collections,
        pc_idx=0,
        all_observed=False,
        lpad=pad,
        rpad=pad,
        title_height=height,
        title_font=QFont(),
        fg_color=QColor("white"),
        background_colors=["#ff0000", "#00ff00"],
        width=lambda: width,
        getPlotTitle=lambda pc: pc._title,
    )


def test_soundings_sharing_a_valid_time_get_their_own_title_lines(qt_app):
    """Two soundings used to draw their titles on the same baseline.

    The secondary titles were enumerated from 0, so the first one landed on the
    focused title's row and the two wrote through each other.
    """
    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    host = _title_host(["FIRST", "SECOND", "THIRD"])
    painter = _RecordingPainter()
    plotSkewT.drawTitles(host, painter)

    assert len(painter.draws) == 3
    rows = [draw.y for draw in painter.draws]
    assert rows == sorted(rows)
    assert len(set(rows)) == 3, f"titles share a baseline: {rows}"
    assert painter.draws[0].text == "FIRST"


def test_a_single_sounding_still_draws_one_title_on_the_first_row(qt_app):
    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    host = _title_host(["ONLY"])
    painter = _RecordingPainter()
    plotSkewT.drawTitles(host, painter)

    assert len(painter.draws) == 1
    assert painter.draws[0].y == 0
    assert painter.draws[0].text == "ONLY"


def test_titles_use_the_whole_panel_width(qt_app):
    """A 150 px rect with TextDontClip is what let long titles spill."""
    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    host = _title_host(["FIRST", "SECOND"], width=900, pad=10)
    painter = _RecordingPainter()
    plotSkewT.drawTitles(host, painter)

    assert {draw.width for draw in painter.draws} == {880}


def test_an_over_long_title_is_elided_rather_than_overflowing(qt_app):
    from qtpy.QtGui import QFontMetrics

    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    host = _title_host(["X" * 4000])
    painter = _RecordingPainter()
    plotSkewT.drawTitles(host, painter)

    drawn = painter.draws[0].text
    assert len(drawn) < 4000
    metrics = QFontMetrics(host.title_font)
    assert metrics.horizontalAdvance(drawn) <= painter.draws[0].width


# -- panel frames ---------------------------------------------------------- #


def test_every_framed_inset_is_wrapped_to_match_the_skewt_border(qt_app):
    """Every plot frame must use the same reduced foreground Skew-T rule."""
    import importlib

    render.install_render_patches()
    for module_name, class_name in render._FRAMED_INSETS:
        module = importlib.import_module(f"sharppy.viz.{module_name}")
        cls = getattr(module, class_name)
        assert getattr(cls, "_sharpmod_matching_frame", False), class_name


def test_the_frame_wrapper_matches_only_the_frame_pen(qt_app):
    from qtpy.QtGui import QColor, QPen

    seen = []

    class Recorder:
        def setPen(self, pen):
            seen.append((float(pen.widthF()), pen.color().name()))

        def __getattr__(self, name):
            return lambda *a, **k: None

    proxy = render._MatchingFramePainter(
        Recorder(), QPen, QColor("white"))
    proxy.setPen(QPen(QColor("red"), 4.0))
    # A later wide pen is plotted data, not a second panel frame.
    proxy.setPen(QPen(QColor("red"), 4.0))
    proxy.setPen(QPen(QColor("white"), 1))
    assert seen == [
        (1.0, "#ffffff"),
        (4.0, "#ff0000"),
        (1.0, "#ffffff"),
    ]


def test_the_frame_wrapper_passes_non_pens_through(qt_app):
    """``Qt.NoPen`` and a bare colour have no frame width to match."""
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QColor, QPen

    forwarded = []

    class Recorder:
        def setPen(self, pen):
            forwarded.append(pen)

        def __getattr__(self, name):
            return lambda *a, **k: None

    proxy = render._MatchingFramePainter(
        Recorder(), QPen, QColor("white"))
    proxy.setPen(Qt.NoPen)
    proxy.setPen(QColor("red"))
    assert len(forwarded) == 2


def test_the_frame_wrapper_paints_one_solid_white_pixel(qt_app):
    """Integer-aligned AA strokes were two gray pixels instead of white."""
    from qtpy.QtGui import QColor, QImage, QPainter, QPen

    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    proxy = render._MatchingFramePainter(
        painter, QPen, QColor("white"), bounds=(10, 10))
    proxy.setPen(QPen(QColor("gray"), 2.0))
    proxy.drawLine(0, 0, 10, 0)
    proxy.drawLine(10, 0, 10, 10)
    proxy.drawLine(10, 10, 0, 10)
    proxy.drawLine(0, 10, 0, 0)
    painter.end()

    assert image.pixelColor(5, 0).name() == "#ffffff"
    assert image.pixelColor(5, 1).name() == "#000000"
    assert image.pixelColor(9, 5).name() == "#ffffff"
    assert image.pixelColor(8, 5).name() == "#000000"


def test_adjacent_plot_frames_paint_each_shared_divider_once(qt_app):
    from qtpy.QtWidgets import QFrame, QGridLayout

    parent = QFrame()
    left = QFrame(parent)
    right = QFrame(parent)
    below = QFrame(parent)
    layout = QGridLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(left, 0, 0)
    layout.addWidget(right, 0, 1)
    layout.addWidget(below, 1, 0, 1, 2)
    parent.setGeometry(0, 0, 20, 20)
    parent.show()
    left.show()
    right.show()
    below.show()
    qt_app.processEvents()
    try:
        assert render._frame_sides_for_widget(left) == frozenset({
            "left", "top",
        })
        assert "left" in render._frame_sides_for_widget(right)
        assert "top" in render._frame_sides_for_widget(below)
    finally:
        parent.close()


def test_a_real_inset_frame_matches_the_skewt_pen(qt_app):
    from qtpy.QtGui import QColor
    import sharppy.viz.hodo as hodo_mod

    render.install_render_patches()
    pens = []

    class FrameSpy:
        def setPen(self, pen):
            try:
                pens.append((float(pen.widthF()), pen.color().name()))
            except Exception:
                pass

        def __getattr__(self, name):
            return lambda *a, **k: None

    stub = SimpleNamespace(
        fg_color=QColor("white"), tlx=0, tly=0, brx=100, bry=100,
        centerx=50, centery=50)
    hodo_mod.backgroundHodo.draw_frame(stub, FrameSpy())
    assert pens[0] == (render.PANEL_FRAME_WIDTH, "#ffffff")


def test_stylesheet_plot_frame_matches_the_skewt_pen(qt_app):
    from qtpy.QtGui import QColor

    class StyledPanel:
        def __init__(self):
            self.fg_color = QColor("#f1f2f3")
            self.sheet = (
                "QFrame { border-width: 2px; border-style: solid; "
                "border-color: #3399CC; }"
            )

        def styleSheet(self):
            return self.sheet

        def setStyleSheet(self, sheet):
            self.sheet = sheet

    panel = StyledPanel()
    render._match_panel_stylesheet(panel)

    assert "border-width: 1px" in panel.sheet
    assert "border-color: #f1f2f3" in panel.sheet

    panel.fg_color = QColor("#202122")
    render._match_panel_stylesheet(panel)
    assert "border-color: #202122" in panel.sheet


def test_nested_right_inset_keeps_only_one_internal_separator(qt_app):
    from qtpy.QtGui import QColor

    class StyledPanel:
        def __init__(self):
            self.fg_color = QColor("#f1f2f3")
            self._sharpmod_frame_sides = "left"
            self.sheet = (
                "QFrame { border-width: 2px; border-style: solid; "
                "border-color: #3399CC; }"
            )

        def styleSheet(self):
            return self.sheet

        def setStyleSheet(self, sheet):
            self.sheet = sheet

    panel = StyledPanel()
    render._match_panel_stylesheet(panel)

    assert "border-width: 0px" in panel.sheet
    assert "border-left-width: 1px" in panel.sheet
    assert "border-color: #f1f2f3" in panel.sheet

    render._match_panel_stylesheet(panel)
    assert panel.sheet.count("border-left-width: 1px") == 1


def test_a_real_stylesheet_inset_matches_the_skewt_border(qt_app):
    from sharppy.viz.stp import plotSTP

    render.install_render_patches()
    panel = plotSTP()
    try:
        sheet = panel.styleSheet().lower()
        assert "border-width: 1px" in sheet
        assert "border-color: #ffffff" in sheet
    finally:
        panel.close()


def test_installing_the_frame_patch_twice_does_not_rewrap(qt_app):
    import sharppy.viz.hodo as hodo_mod

    render.install_render_patches()
    before = hodo_mod.backgroundHodo.draw_frame
    render._install_matching_panel_frames()
    assert hodo_mod.backgroundHodo.draw_frame is before


def test_titles_are_not_vertically_clipped(qt_app):
    """The rect bounds the glyphs, so it must never be shorter than the font.

    Dropping ``TextDontClip`` to stop two titles overlapping also started
    cropping the ascenders and degree signs off a single title.
    """
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QFontMetrics

    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    host = _title_host(["A TITLE @40.38\N{DEGREE SIGN}N 83.17\N{DEGREE SIGN}W"])
    # A deliberately cramped slot, which is what upstream can hand us.
    host.title_height = 4
    painter = _RecordingPainter()
    plotSkewT.drawTitles(host, painter)

    metrics = QFontMetrics(host.title_font)
    draw = painter.draws[0]
    assert draw.height >= metrics.height(), (
        "the title rect is shorter than the font it must hold")
    assert draw.align & Qt.TextDontClip, "the title can still be cropped"
