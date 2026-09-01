"""Coverage for re-rasterizing a live widget tree at the export density.

Scientific panels paint into a persistent ``plotBitMap`` and blit it from
``paintEvent``. The headless renderer composes its whole window inside
:func:`sharpmod.render._target_density_pixmaps`, so those caches are allocated at
the export density and text is rasterized once, at final size. An interactive
window is composed at 1x, so capturing it through a scaled painter used to
enlarge caches that were already rasterized -- which made an on-screen HD export
visibly softer than the ``sharpmod-render`` output at identical pixel
dimensions.

These tests pin the fix and, just as importantly, the guarantee that it leaves
the window on screen exactly as it was.
"""

from __future__ import annotations

import pytest
from qtpy import QtGui
from qtpy.QtCore import QRect, Qt
from qtpy.QtWidgets import QWidget

from sharpmod import render as R

SIZE = (260, 160)

#: Drawn by the background pass and the data pass respectively. Both are painted
#: aliased so the colour survives exactly and can be counted, which lets a test
#: say *which* pass went missing rather than only that ink was lost.
BACKGROUND_INK = "#3a7bd5"
DATA_INK = "#ff2d55"


def _mid_tone_share(image) -> float:
    """Share of inked pixels sitting at mid-tone.

    A crisp glyph edge ramps over about one pixel; a smoothly enlarged one ramps
    over several. So a higher share of mid-tones means a softer image.
    """
    mid = inked = 0
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            lightness = max(colour.red(), colour.green(), colour.blue())
            if lightness < 24:
                continue
            inked += 1
            if 40 <= lightness <= 200:
                mid += 1
    return mid / max(1, inked)


def _count_colour(image, hex_colour: str) -> int:
    """Count pixels exactly matching ``hex_colour``."""
    target = QtGui.QColor(hex_colour).rgb() & 0xFFFFFF
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if (image.pixel(x, y) & 0xFFFFFF) == target
    )


class CachedPanel(QWidget):
    """A stand-in with the vendored panels' cache-and-blit structure."""

    def __init__(self):
        super().__init__()
        self.resize(*SIZE)
        self.bg_color = QtGui.QColor("#050505")
        # Referenced through the module attribute, exactly as the vendored
        # widgets do, so a substituted QPixmap type is picked up.
        self.plotBitMap = QtGui.QPixmap(*SIZE)
        self.plotBitMap.fill(self.bg_color)
        self.plotBackground()
        self.backgroundBitMap = self.plotBitMap.copy()

    def plotBackground(self):
        painter = QtGui.QPainter(self.plotBitMap)
        try:
            painter.setPen(QtGui.QPen(QtGui.QColor("#3a3a3a"), 1))
            for offset in range(0, SIZE[1], 16):
                painter.drawLine(0, offset, SIZE[0], offset)
        finally:
            painter.end()

    def clearData(self):
        self.plotBitMap = self.backgroundBitMap.copy()

    def plotData(self):
        painter = QtGui.QPainter(self.plotBitMap)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            painter.setPen(QtGui.QPen(QtGui.QColor("#f0f0f0")))
            font = painter.font()
            font.setPointSize(8)
            painter.setFont(font)
            for row in range(6):
                painter.drawText(
                    QRect(6, 6 + row * 22, SIZE[0] - 12, 20),
                    Qt.AlignLeft, "CAPE 1234 J/kg  LCL 850m  STP 0.6")
        finally:
            painter.end()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QtGui.QPainter(self)
        try:
            painter.drawPixmap(0, 0, self.plotBitMap)
        finally:
            painter.end()


class LivePaintPanel(CachedPanel):
    """A panel whose data pass takes the live widget painter.

    ``plotAdvection`` is shaped this way: its cache holds only the background and
    ``plotData(qp)`` draws onto the widget. Calling it with no argument raises, so
    the rebuild must recognise it and skip it.
    """

    def plotData(self, qp):  # noqa: D102 - signature is the point
        qp.setPen(QtGui.QPen(QtGui.QColor("#ff2d55")))
        qp.drawText(QRect(6, 6, SIZE[0] - 12, 20), Qt.AlignLeft, "live text")

    def paintEvent(self, event):  # noqa: N802
        painter = QtGui.QPainter(self)
        try:
            painter.drawPixmap(0, 0, self.plotBitMap)
            self.plotData(painter)
        finally:
            painter.end()


class ResettingPanel(QWidget):
    """A panel whose ``clearData`` blanks the cache instead of restoring it.

    This is the shape most vendored panels actually have: no ``backgroundBitMap``
    at all, and a ``clearData`` that allocates a fresh blank cache for the two
    draw passes to fill again. ``clearData`` is a reset for when the profile
    changes, not a step in the draw sequence.

    :class:`CachedPanel` restores from a snapshot, which made the rebuild look
    correct while it was calling ``clearData`` between the background and data
    passes. On this shape that call discarded the background, and HD and UHD
    exports shipped without axes, tick labels, titles, or legends -- in some
    cases without a whole panel. The fixture existed; it just did not stand for
    the thing that broke.
    """

    def __init__(self):
        super().__init__()
        self.resize(*SIZE)
        self.bg_color = QtGui.QColor("#050505")
        self.plotBitMap = QtGui.QPixmap(*SIZE)
        self.plotBitMap.fill(self.bg_color)
        self.plotBackground()
        self.plotData()

    def plotBackground(self):
        """Axes and gridlines -- the part that went missing."""
        painter = QtGui.QPainter(self.plotBitMap)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            painter.setPen(QtGui.QPen(QtGui.QColor(BACKGROUND_INK), 1))
            for offset in range(0, SIZE[1], 16):
                painter.drawLine(0, offset, SIZE[0], offset)
        finally:
            painter.end()

    def clearData(self):
        """Deliberately destructive, exactly as the real panels are."""
        self.plotBitMap = QtGui.QPixmap(*SIZE)
        self.plotBitMap.fill(self.bg_color)

    def plotData(self):
        painter = QtGui.QPainter(self.plotBitMap)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            painter.fillRect(QRect(20, 20, 40, 24), QtGui.QColor(DATA_INK))
        finally:
            painter.end()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QtGui.QPainter(self)
        try:
            painter.drawPixmap(0, 0, self.plotBitMap)
        finally:
            painter.end()


@pytest.fixture
def panel(qt_app):
    widget = CachedPanel()
    widget.clearData()
    widget.plotData()
    widget.show()
    qt_app.processEvents()
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()


def test_the_density_rebuild_is_crisper_than_scaling_a_1x_cache(panel, tmp_path):
    """The reported symptom: an on-screen export softer than the CLI's."""
    scale = 2.0
    scaled_only = R.grab_widget_pixmap(panel, scale=scale)

    rebuilt_path = tmp_path / "rebuilt.png"
    with R._panels_at_target_density(panel, scale) as rebuilt:
        assert rebuilt == 1, "the panel's cache should have been rebuilt"
        assert panel.plotBitMap.devicePixelRatioF() == pytest.approx(scale)
        dense = R.grab_widget_pixmap(panel, scale=scale)
    assert dense.save(str(rebuilt_path), "PNG", 0)

    soft = _mid_tone_share(scaled_only.toImage())
    crisp = _mid_tone_share(dense.toImage())
    assert crisp < soft, (
        f"density rebuild should reduce mid-tone pixels: "
        f"{crisp:.4f} vs {soft:.4f}")


def test_save_widget_png_applies_the_rebuild(panel, tmp_path):
    scaled_only = tmp_path / "scaled.png"
    exported = tmp_path / "exported.png"
    assert R.grab_widget_pixmap(panel, scale=2.0).save(
        str(scaled_only), "PNG", 0)

    monkey_scale = R._png_image_scale(R.PNG_IMAGE_HD)
    assert R.save_widget_png(panel, str(exported), image_mode=R.PNG_IMAGE_HD)

    soft = _mid_tone_share(QtGui.QImage(str(scaled_only)))
    crisp = _mid_tone_share(QtGui.QImage(str(exported)))
    assert monkey_scale > 1.0
    assert crisp < soft


@pytest.mark.parametrize("mode", [
    R.PNG_IMAGE_LOSSLESS, R.PNG_IMAGE_HD, R.PNG_IMAGE_UHD,
])
def test_export_dimensions_are_unchanged(panel, tmp_path, mode):
    """The rebuild must not move the output size."""
    out = tmp_path / f"{mode}.png"
    assert R.save_widget_png(panel, str(out), image_mode=mode)
    image = QtGui.QImage(str(out))
    scale = R._png_image_scale(mode)
    assert (image.width(), image.height()) == (
        round(panel.width() * scale), round(panel.height() * scale))


def test_the_live_cache_is_restored_after_export(panel, tmp_path):
    """Otherwise the window would be left drawing from a density cache."""
    original = panel.plotBitMap
    original_background = panel.backgroundBitMap

    assert R.save_widget_png(panel, str(tmp_path / "out.png"),
                             image_mode=R.PNG_IMAGE_UHD)

    assert panel.plotBitMap is original
    assert panel.backgroundBitMap is original_background
    assert panel.plotBitMap.devicePixelRatioF() == pytest.approx(1.0)


def test_the_global_pixmap_type_is_restored(panel, tmp_path):
    assert R.save_widget_png(panel, str(tmp_path / "out.png"),
                             image_mode=R.PNG_IMAGE_HD)
    assert QtGui.QPixmap is R._NATIVE_QPIXMAP


def test_the_on_screen_render_is_untouched(panel, tmp_path, qt_app):
    """Exporting must not change what the user is looking at."""
    before = panel.grab().toImage()
    assert R.save_widget_png(panel, str(tmp_path / "out.png"),
                             image_mode=R.PNG_IMAGE_UHD)
    qt_app.processEvents()
    assert panel.grab().toImage() == before


def test_repeated_exports_are_stable(panel, tmp_path):
    """Guards against the rebuild accumulating state across exports."""
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    assert R.save_widget_png(panel, str(first), image_mode=R.PNG_IMAGE_HD)
    assert R.save_widget_png(panel, str(second), image_mode=R.PNG_IMAGE_HD)
    assert first.read_bytes() == second.read_bytes()


def test_lossless_export_does_not_rebuild(panel):
    """At 1x there is nothing to gain, and the caches are already correct."""
    original = panel.plotBitMap
    with R._panels_at_target_density(panel, 1.0) as rebuilt:
        assert rebuilt == 0
        assert panel.plotBitMap is original


def test_a_live_painting_panel_is_left_alone(qt_app, tmp_path):
    """``plotData(qp)`` must not be invoked with no argument."""
    widget = LivePaintPanel()
    widget.show()
    qt_app.processEvents()
    try:
        # The background cache is still rebuilt; only the data pass is skipped.
        with R._panels_at_target_density(widget, 2.0) as rebuilt:
            assert rebuilt == 1
            assert widget.plotBitMap.devicePixelRatioF() == pytest.approx(2.0)
        assert R.save_widget_png(widget, str(tmp_path / "live.png"),
                                 image_mode=R.PNG_IMAGE_HD)
    finally:
        widget.close()
        widget.deleteLater()


def test_zero_arg_method_detects_a_required_parameter(qt_app):
    widget = LivePaintPanel()
    try:
        assert R._zero_arg_method(widget, "plotData") is None
        assert R._zero_arg_method(widget, "clearData") is not None
        assert R._zero_arg_method(widget, "nope") is None
    finally:
        widget.deleteLater()


def test_cached_panels_finds_only_widgets_with_a_cache(qt_app):
    parent = QWidget()
    child = CachedPanel()
    child.setParent(parent)
    plain = QWidget()
    plain.setParent(parent)
    try:
        found = list(R._cached_panels(parent))
        assert child in found
        assert plain not in found
        assert parent not in found
    finally:
        parent.deleteLater()


def test_a_failing_panel_does_not_abort_the_export(qt_app, tmp_path,
                                                   monkeypatch):
    """One broken panel must not cost the whole image."""
    widget = CachedPanel()
    widget.show()
    qt_app.processEvents()

    def boom():
        raise RuntimeError("panel is broken")

    monkeypatch.setattr(widget, "plotData", boom)
    try:
        assert R.save_widget_png(widget, str(tmp_path / "out.png"),
                                 image_mode=R.PNG_IMAGE_HD)
        assert widget.plotBitMap.devicePixelRatioF() == pytest.approx(1.0)
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# Content retention
#
# The rebuild replaces a panel's cache and repopulates it. If it cannot put back
# everything that was there, the export loses content -- which is worse than
# simply exporting a softer image, and is invisible in a sharpness measurement.
# --------------------------------------------------------------------------- #
def test_a_resetting_panels_clear_data_is_destructive(qt_app):
    """Pins what this fixture stands for.

    If ``clearData`` here ever restores content instead of blanking it, the test
    below silently stops guarding anything. That is precisely how the regression
    reached a release candidate: the only panel fixture restored from a
    ``backgroundBitMap`` that most real panels do not have.
    """
    widget = ResettingPanel()
    try:
        assert not hasattr(widget, "backgroundBitMap")
        assert _count_colour(widget.plotBitMap.toImage(), BACKGROUND_INK) > 0
        widget.clearData()
        assert _count_colour(widget.plotBitMap.toImage(), BACKGROUND_INK) == 0
    finally:
        widget.deleteLater()


def test_the_rebuild_keeps_the_background_of_a_resetting_panel(qt_app):
    """The reported bug: axes, labels, and whole panels missing from HD/UHD."""
    widget = ResettingPanel()
    widget.show()
    qt_app.processEvents()
    try:
        before = widget.plotBitMap.toImage()
        background_before = _count_colour(before, BACKGROUND_INK)
        data_before = _count_colour(before, DATA_INK)
        assert background_before > 0 and data_before > 0

        with R._panels_at_target_density(widget, 2.0) as rebuilt:
            assert rebuilt == 1
            after = widget.plotBitMap.toImage()

        assert _count_colour(after, BACKGROUND_INK) > 0, (
            "the background pass was discarded: this is the export that lost "
            "its axes, tick labels, titles, and legends")
        assert _count_colour(after, DATA_INK) > 0, "the data pass did not run"

        # Same marks at twice the density, so the counts grow rather than shrink.
        assert _count_colour(after, BACKGROUND_INK) >= background_before
        assert _count_colour(after, DATA_INK) >= data_before
    finally:
        widget.close()
        widget.deleteLater()


def test_a_resetting_panel_exports_without_losing_content(qt_app, tmp_path):
    """End to end through the public entry point, at both scaled modes."""
    widget = ResettingPanel()
    widget.show()
    qt_app.processEvents()
    try:
        for mode in (R.PNG_IMAGE_HD, R.PNG_IMAGE_UHD):
            out = tmp_path / f"{mode}.png"
            assert R.save_widget_png(widget, str(out), image_mode=mode)
            image = QtGui.QImage(str(out))
            assert _count_colour(image, BACKGROUND_INK) > 0, (
                f"{mode} export lost the background pass")
            assert _count_colour(image, DATA_INK) > 0, (
                f"{mode} export lost the data pass")
    finally:
        widget.close()
        widget.deleteLater()


def test_the_rebuild_does_not_call_clear_data(qt_app):
    """States the rule directly, independently of what the passes draw.

    ``clearData`` is a profile-change reset. Calling it between the two draw
    passes is what dropped the background, so the rebuild must not invoke it at
    all.
    """
    widget = ResettingPanel()
    calls = []
    widget.clearData = lambda: calls.append("clearData")  # type: ignore[method-assign]
    widget.show()
    qt_app.processEvents()
    try:
        with R._panels_at_target_density(widget, 2.0) as rebuilt:
            assert rebuilt == 1
        assert calls == [], "the density rebuild must not reset panel data"
    finally:
        widget.close()
        widget.deleteLater()
