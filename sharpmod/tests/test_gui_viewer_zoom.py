"""Tests for the sounding viewer's zoom, pan, and View controls.

The viewer previously scaled the sounding to fit and capped at 1:1 with no user
control. On a 1920x1080 display that made the whole layout visible but the index
tables and parameter values near-illegible, with no way to magnify a region.

These tests exercise :class:`~sharpmod.gui_viewer._ScaledSoundingView` against a
plain fixed-size stand-in rather than a fully composed sounding. The zoom logic
is independent of what the scene contains, and composing a real sounding costs
several seconds per test.

The safety property that matters most is the last group: zooming must never
change the canvas widget's own geometry, because the export path grabs that
widget directly and the window is sized from it.
"""

from __future__ import annotations

import pytest

from qtpy.QtCore import QPoint, QSize, Qt
from qtpy.QtWidgets import QMainWindow, QWidget

from sharpmod import gui_theme, gui_viewer

#: Stand-in canvas geometry for the zoom tests: the vendored 1180x800 base plus
#: the +280/+200 canvas grow.
#:
#: Not the real composed size. A real sounding settles at 1630x1091 -- see
#: ``test_composed_canvas_matches_the_documented_geometry`` in
#: ``test_gui_viewer_sidebar``, which is where that contract is pinned. The zoom
#: behaviour under test depends only on the ratio between canvas and viewport,
#: so a cheap fixed-size widget is enough here; using a smaller one keeps the
#: arithmetic in these tests easy to check by hand.
NATURAL = QSize(1460, 1000)

#: A 1080p-class viewport, i.e. too small to show the sounding at 1:1. This is
#: the case the zoom controls exist for.
SMALL_VIEWPORT = (1600, 900)


@pytest.fixture
def scaled_view(qt_app):
    """A scaled sounding view over a fixed-size stand-in canvas."""
    gui_theme.apply_theme(qt_app, color_style="standard")

    window = QMainWindow()
    canvas = QWidget()
    canvas.setFixedSize(NATURAL)
    view = gui_viewer._ScaledSoundingView(canvas, NATURAL, window)
    window.setCentralWidget(view)
    window.resize(*SMALL_VIEWPORT)
    window.show()
    for _ in range(6):
        qt_app.processEvents()
    yield view, canvas, window
    window.close()


# ---------------------------------------------------------------------------
# Fit mode
# ---------------------------------------------------------------------------


def test_starts_in_fit_mode_showing_the_whole_sounding(scaled_view):
    view, _canvas, _window = scaled_view
    assert view.is_fit_mode()
    assert view.current_scale() == pytest.approx(view.fit_scale())
    assert view.current_scale() < 1.0, (
        "the stand-in should not fit at 1:1 in a 1080p-class viewport")


def test_fit_shows_the_whole_canvas_even_when_it_arrives_offset(qt_app):
    """The canvas reaches the scene carrying its previous parent's offset.

    ``QGraphicsScene.addWidget`` takes the widget's *geometry*, position
    included, and the sounding arrives here having been the central widget of a
    QMainWindow -- so it carried a y offset equal to the menu bar's height
    (29 px in practice). The scene rect starts at 0, so that offset pushed the
    bottom 29 px of the sounding outside the region the fit covers, cutting off
    the lowest index rows. Fit mode turns the scrollbars off, so there was no
    way to reach them and nothing to hint the content was there.

    Asserting on the scene's own content bounds rather than its rect is the
    point: the fit is computed *from* the rect, so measuring the rect reports
    success by construction, which is how this survived the earlier tests.
    """
    gui_theme.apply_theme(qt_app, color_style="standard")

    window = QMainWindow()
    canvas = QWidget()
    canvas.setFixedSize(NATURAL)
    # What a QMainWindow central widget carries when it is reparented out.
    canvas.move(0, 29)
    view = gui_viewer._ScaledSoundingView(canvas, NATURAL, window)
    window.setCentralWidget(view)
    window.resize(*SMALL_VIEWPORT)
    window.show()
    for _ in range(6):
        qt_app.processEvents()

    try:
        content = view.scene().itemsBoundingRect()
        assert (content.top(), content.left()) == (0, 0), (
            f"canvas sits at {content.topLeft()} in the scene, so the fitted "
            f"region does not cover all of it")

        bottom_right = view.mapFromScene(content.bottomRight())
        viewport = view.viewport()
        assert bottom_right.y() <= viewport.height() + 1, (
            f"bottom of the sounding is {bottom_right.y() - viewport.height()}"
            f"px below the viewport while fitted")
        assert bottom_right.x() <= viewport.width() + 1, (
            f"right of the sounding is {bottom_right.x() - viewport.width()}"
            f"px past the viewport while fitted")
    finally:
        window.close()


def test_fit_mode_never_upscales(qt_app):
    """Upscaling past 1:1 would blur the canvas text."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    window = QMainWindow()
    canvas = QWidget()
    small = QSize(400, 300)
    canvas.setFixedSize(small)
    view = gui_viewer._ScaledSoundingView(canvas, small, window)
    window.setCentralWidget(view)
    window.resize(1600, 1200)  # far larger than the canvas
    window.show()
    try:
        for _ in range(6):
            qt_app.processEvents()
        assert view.fit_scale() == pytest.approx(1.0)
        assert view.current_scale() <= 1.0
    finally:
        window.close()


def test_fit_mode_tracks_the_viewport_across_resizes(scaled_view, qt_app):
    view, _canvas, window = scaled_view
    before = view.current_scale()
    window.resize(1200, 700)
    for _ in range(4):
        qt_app.processEvents()
    assert view.current_scale() == pytest.approx(view.fit_scale())
    assert view.current_scale() != pytest.approx(before)


def test_native_size_window_keeps_zoom_when_resized_smaller(qt_app):
    """A viewer that opens at 1:1 must not permanently lose its zoom controls."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    window = QMainWindow()
    gui_viewer._install_view_controls(window)
    canvas = QWidget()
    natural = QSize(400, 300)
    canvas.setFixedSize(natural)
    window.spc_widget = canvas
    window.setCentralWidget(canvas)

    gui_viewer._fit_window_to_screen(qt_app, window)
    gui_viewer._bind_view_controls(window)
    window.show()
    for _ in range(6):
        qt_app.processEvents()

    try:
        view = window.centralWidget()
        assert isinstance(view, gui_viewer._ScaledSoundingView)
        assert view.current_scale() == pytest.approx(1.0)
        assert all(action.isEnabled()
                   for action in window._sharpmod_view_actions.values())
        assert window._sharpmod_zoom_slider.isEnabled()

        # Height is the binding axis, independent of a window manager's minimum
        # toolbar width. Fit mode must follow the smaller viewport automatically.
        window.resize(window.width(), 180)
        for _ in range(6):
            qt_app.processEvents()

        assert view.current_scale() < 1.0
        assert view.current_scale() == pytest.approx(view.fit_scale())
        assert window._sharpmod_view_actions["fit"].isEnabled()
        assert window._sharpmod_zoom_slider.isEnabled()
    finally:
        window.close()


def test_scrollbars_are_hidden_while_fitting(scaled_view):
    """Nothing can overflow in fit mode, so the bars would be noise."""
    view, _canvas, _window = scaled_view
    assert view.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


# ---------------------------------------------------------------------------
# Manual zoom
# ---------------------------------------------------------------------------


def test_zooming_in_leaves_fit_mode(scaled_view):
    view, _canvas, _window = scaled_view
    view.zoom_in()
    assert not view.is_fit_mode()
    assert view.current_scale() > view.fit_scale()


def test_manual_zoom_survives_a_resize(scaled_view, qt_app):
    """A resize must not silently discard the user's chosen zoom."""
    view, _canvas, window = scaled_view
    view.zoom_to(2.0)
    window.resize(1000, 600)
    for _ in range(4):
        qt_app.processEvents()
    assert view.current_scale() == pytest.approx(2.0)
    assert not view.is_fit_mode()


def test_fit_to_window_returns_to_fit_mode(scaled_view, qt_app):
    view, _canvas, _window = scaled_view
    view.zoom_to(3.0)
    view.fit_to_window()
    for _ in range(4):
        qt_app.processEvents()
    assert view.is_fit_mode()
    assert view.current_scale() == pytest.approx(view.fit_scale())


def test_zoom_is_clamped_to_its_bounds(scaled_view):
    view, _canvas, _window = scaled_view
    view.zoom_to(999.0)
    assert view.current_scale() == pytest.approx(view.MAX_SCALE)
    view.zoom_to(0.0001)
    assert view.current_scale() == pytest.approx(view.MIN_SCALE)


def test_zoom_steps_are_reversible(scaled_view):
    view, _canvas, _window = scaled_view
    view.zoom_to(1.0)
    view.zoom_in()
    view.zoom_out()
    assert view.current_scale() == pytest.approx(1.0)


def test_scrollbars_appear_once_zoomed_beyond_the_viewport(scaled_view, qt_app):
    """Panning needs a scroll range, or zoom would strand the hidden edges."""
    view, _canvas, _window = scaled_view
    view.zoom_to(2.0)
    for _ in range(4):
        qt_app.processEvents()
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert view.verticalScrollBar().maximum() > 0
    assert view.horizontalScrollBar().maximum() > 0


def test_scale_changes_are_announced(scaled_view):
    """The toolbar readout is driven by this signal rather than polling."""
    view, _canvas, _window = scaled_view
    seen = []
    view.scaleChanged.connect(seen.append)
    view.zoom_to(1.5)
    view.zoom_in()
    view.fit_to_window()
    assert len(seen) >= 3
    assert seen[0] == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# The canvas must not move (export safety)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scale", [0.25, 0.5, 1.0, 2.0, 4.0])
def test_zoom_never_changes_the_canvas_geometry(scaled_view, qt_app, scale):
    """Zoom is a *view* transform only.

    The export path grabs ``spc_widget`` directly and the window is sized from
    its natural geometry, so a zoom that resized the widget would change
    exported images and re-trigger the layout-compensation passes.
    """
    view, canvas, _window = scaled_view
    view.zoom_to(scale)
    for _ in range(4):
        qt_app.processEvents()
    assert canvas.size() == NATURAL


def test_fit_mode_never_changes_the_canvas_geometry(scaled_view, qt_app):
    view, canvas, window = scaled_view
    view.fit_to_window()
    for size in ((900, 600), (1920, 1080), (640, 480)):
        window.resize(*size)
        for _ in range(4):
            qt_app.processEvents()
        assert canvas.size() == NATURAL


# ---------------------------------------------------------------------------
# Wheel routing
# ---------------------------------------------------------------------------


def test_plain_wheel_does_not_zoom_the_view(scaled_view, qt_app):
    """The vendored Skew-T and hodograph own the plain wheel for their own zoom.

    Stealing it for view zoom would break an interaction documented in the
    README and the on-screen controls guide.
    """
    from qtpy.QtCore import QPoint, QPointF
    from qtpy.QtGui import QWheelEvent

    view, _canvas, _window = scaled_view
    before = view.current_scale()

    centre = QPointF(view.viewport().rect().center())
    event = QWheelEvent(
        centre, view.mapToGlobal(QPoint(0, 0)) + QPoint(10, 10),
        QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    view.wheelEvent(event)
    qt_app.processEvents()

    assert view.current_scale() == pytest.approx(before), (
        "a plain wheel scroll changed the view scale; it must reach the canvas")


class _WheelSpy(QWidget):
    """A stand-in panel that records the wheel events it is sent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.events = []

    def wheelEvent(self, event):  # noqa: N802 - Qt override
        self.events.append((event.angleDelta().y(),
                            event.position().toPoint()))
        event.accept()


@pytest.fixture
def view_with_panel(qt_app):
    """A scaled view whose canvas contains one wheel-recording child panel."""
    gui_theme.apply_theme(qt_app, color_style="standard")

    window = QMainWindow()
    canvas = QWidget()
    canvas.setFixedSize(NATURAL)
    panel = _WheelSpy(canvas)
    # Offset and inset, so a wrong coordinate mapping is visible rather than
    # accidentally correct.
    panel.setGeometry(200, 150, 600, 500)
    view = gui_viewer._ScaledSoundingView(canvas, NATURAL, window)
    window.setCentralWidget(view)
    window.resize(*SMALL_VIEWPORT)
    window.show()
    for _ in range(6):
        qt_app.processEvents()
    yield view, canvas, panel, window
    window.close()


def _wheel_at(view, viewport_point, angle=0, pixel=0,
              phase=Qt.NoScrollPhase, modifiers=Qt.NoModifier):
    from qtpy.QtCore import QPoint, QPointF
    from qtpy.QtGui import QWheelEvent

    return QWheelEvent(
        QPointF(viewport_point),
        QPointF(view.viewport().mapToGlobal(viewport_point)),
        QPoint(0, pixel), QPoint(0, angle),
        Qt.NoButton, modifiers, phase, False)


def _point_over(view, canvas, panel):
    """The viewport point over the panel's centre."""
    from qtpy.QtCore import QPointF

    centre = panel.geometry().center()
    return view.mapFromScene(QPointF(centre))


@pytest.mark.parametrize("label,angle,pixel,phase", [
    # A classic mouse wheel.
    ("wheel notch", 120, 0, Qt.NoScrollPhase),
    # A precision touchpad: every event after the first carries ScrollUpdate,
    # and these were dropped entirely before reaching the panel.
    ("phased notch", 120, 0, Qt.ScrollUpdate),
    ("phased begin", 120, 0, Qt.ScrollBegin),
    # A slow touchpad scroll: small angle deltas, forwarded proportionally.
    ("small angle", 15, 40, Qt.ScrollUpdate),
    # A touchpad reporting only pixels. angleDelta is zero here, and the
    # vendored zoom reads nothing else, so this moved the scale not at all.
    ("pixel only", 0, 40, Qt.ScrollUpdate),
])
def test_plain_wheel_reaches_the_panel_from_every_device(
        view_with_panel, qt_app, label, angle, pixel, phase):
    """Panel zoom must work for a mouse wheel *and* a precision touchpad.

    The vendored Skew-T and hodograph zoom from ``angleDelta`` alone and expect
    the 120-unit notches a wheel sends. Passing the original event through the
    graphics proxy failed two ways: phased full-notch events never arrived, and
    pixel-only events arrived carrying nothing the zoom could use.
    """
    view, canvas, panel, _window = view_with_panel
    point = _point_over(view, canvas, panel)
    assert view.viewport().rect().contains(point)

    view.wheelEvent(_wheel_at(view, point, angle=angle, pixel=pixel,
                              phase=phase))
    qt_app.processEvents()

    assert panel.events, f"{label}: the panel received no wheel event"
    delta, _pos = panel.events[0]
    assert delta != 0, f"{label}: forwarded a zero delta, which zooms nothing"
    assert (delta > 0) == (angle > 0 or pixel > 0), (
        f"{label}: forwarded delta {delta} has the wrong sign")


def test_forwarded_wheel_is_anchored_under_the_cursor(view_with_panel, qt_app):
    """The vendored zoom anchors on the event position, so it must be exact."""
    view, canvas, panel, _window = view_with_panel
    # A point clearly off-centre inside the panel.
    target_local = QPoint(120, 90)
    scene_point = panel.geometry().topLeft() + target_local
    from qtpy.QtCore import QPointF

    point = view.mapFromScene(QPointF(scene_point))

    view.wheelEvent(_wheel_at(view, point, angle=120))
    qt_app.processEvents()

    assert panel.events
    _delta, position = panel.events[0]
    assert abs(position.x() - target_local.x()) <= 2, position
    assert abs(position.y() - target_local.y()) <= 2, position


def test_ctrl_wheel_does_not_reach_the_panel(view_with_panel, qt_app):
    """View zoom and panel zoom must stay on separate gestures."""
    view, canvas, panel, _window = view_with_panel
    point = _point_over(view, canvas, panel)
    before = view.current_scale()

    view.wheelEvent(_wheel_at(view, point, angle=120,
                              modifiers=Qt.ControlModifier))
    qt_app.processEvents()

    assert view.current_scale() > before
    assert not panel.events, "Ctrl+wheel leaked through to the panel"


def test_wheel_outside_the_canvas_is_not_swallowed(view_with_panel, qt_app):
    """Only scrolls over the sounding belong to the panels.

    Asserts on ``wheelEvent`` rather than on ``_panel_under``. The private
    lookup returning ``(None, None)`` is one layer too deep to establish the
    behaviour in the name: the routing decision is made in ``wheelEvent``, and a
    version that ignored the lookup and forwarded unconditionally would keep a
    ``_panel_under`` assertion green.
    """
    from qtpy.QtCore import QPointF

    view, canvas, panel, _window = view_with_panel
    before = view.current_scale()
    # A scene point beyond the canvas, i.e. in the surrounding letterbox.
    outside = view.mapFromScene(
        QPointF(NATURAL.width() + 50, NATURAL.height() + 50))

    event = _wheel_at(view, outside, angle=120)
    view.wheelEvent(event)
    qt_app.processEvents()

    assert not panel.events, "a scroll off the canvas reached a panel anyway"
    assert not event.isAccepted(), (
        "the view consumed a wheel it did not act on, so nothing further -- "
        "the scroll area, or an ancestor -- can ever see it")
    assert view.current_scale() == pytest.approx(before), (
        "a plain wheel off the canvas must not zoom the view either; that is "
        "the Ctrl+wheel gesture")


def test_ctrl_wheel_zooms_the_view(scaled_view, qt_app):
    from qtpy.QtCore import QPoint, QPointF
    from qtpy.QtGui import QWheelEvent

    view, _canvas, _window = scaled_view
    before = view.current_scale()

    centre = QPointF(view.viewport().rect().center())
    event = QWheelEvent(
        centre, view.mapToGlobal(QPoint(0, 0)) + QPoint(10, 10),
        QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
    view.wheelEvent(event)
    qt_app.processEvents()

    assert view.current_scale() > before
    assert not view.is_fit_mode()


# ---------------------------------------------------------------------------
# View controls (menu + toolbar)
# ---------------------------------------------------------------------------


@pytest.fixture
def viewer_with_controls(qt_app):
    """A window carrying the View controls over a scalable host."""
    gui_theme.apply_theme(qt_app, color_style="standard")

    window = QMainWindow()
    gui_viewer._install_view_controls(window)
    canvas = QWidget()
    canvas.setFixedSize(NATURAL)
    view = gui_viewer._ScaledSoundingView(canvas, NATURAL, window)
    window.setCentralWidget(view)
    window.resize(*SMALL_VIEWPORT)
    window.show()
    for _ in range(4):
        qt_app.processEvents()
    gui_viewer._bind_view_controls(window)
    yield window, view
    window.close()


def test_view_controls_install_a_toolbar_and_menu(viewer_with_controls):
    """This is the application's first toolbar; zoom is used too often for a menu."""
    from qtpy.QtWidgets import QMenu, QToolBar

    window, _view = viewer_with_controls
    assert isinstance(getattr(window, "_sharpmod_view_toolbar", None), QToolBar)
    assert set(window._sharpmod_view_actions) == {"fit", "actual", "in", "out"}

    titles = [menu.title() for menu in window.menuBar().findChildren(QMenu)]
    assert "View" in titles, f"no View menu among {titles}"


def test_view_control_shortcuts_are_the_conventional_ones(viewer_with_controls):
    window, _view = viewer_with_controls
    actions = window._sharpmod_view_actions
    assert actions["fit"].shortcut().toString() == "Ctrl+0"
    assert actions["actual"].shortcut().toString() == "Ctrl+1"
    assert actions["out"].shortcut().toString() == "Ctrl+-"
    # Zoom in carries both bindings, because an unshifted "+" sends Ctrl+=.
    assert {s.toString() for s in actions["in"].shortcuts()} == {"Ctrl++",
                                                                "Ctrl+="}


def test_triggering_the_actions_drives_the_view(viewer_with_controls, qt_app):
    window, view = viewer_with_controls
    actions = window._sharpmod_view_actions

    actions["actual"].trigger()
    qt_app.processEvents()
    assert view.current_scale() == pytest.approx(1.0)

    actions["in"].trigger()
    qt_app.processEvents()
    assert view.current_scale() > 1.0

    actions["out"].trigger()
    qt_app.processEvents()
    assert view.current_scale() == pytest.approx(1.0)

    actions["fit"].trigger()
    qt_app.processEvents()
    assert view.is_fit_mode()


def test_zoom_readout_tracks_the_scale(viewer_with_controls, qt_app):
    window, view = viewer_with_controls
    readout = window._sharpmod_zoom_readout

    view.zoom_to(1.0)
    qt_app.processEvents()
    assert readout.text() == "100%"

    view.zoom_to(2.0)
    qt_app.processEvents()
    assert readout.text() == "200%"

    view.zoom_to(0.5)
    qt_app.processEvents()
    assert readout.text() == "50%"


def test_zoom_is_disabled_rather_than_inert_on_the_one_to_one_host(qt_app):
    """When the sounding already fits at 1:1 there is no transform to drive.

    Present-but-dead controls are worse than disabled ones, so the actions are
    disabled and say why.
    """
    gui_theme.apply_theme(qt_app, color_style="standard")

    window = QMainWindow()
    gui_viewer._install_view_controls(window)
    canvas = QWidget()
    size = QSize(400, 300)
    canvas.setFixedSize(size)
    window.setCentralWidget(
        gui_viewer._FixedSoundingScrollArea(canvas, size, window))
    gui_viewer._bind_view_controls(window)
    try:
        actions = window._sharpmod_view_actions
        assert not any(a.isEnabled() for a in actions.values())
        assert "actual size" in actions["in"].toolTip().lower()
    finally:
        window.close()


def test_binding_without_installed_controls_is_a_no_op(qt_app):
    """Compose order must not make binding fragile."""
    window = QMainWindow()
    try:
        gui_viewer._bind_view_controls(window)  # must not raise
    finally:
        window.close()


# ---------------------------------------------------------------------------
# Mode feedback
# ---------------------------------------------------------------------------


def test_fit_action_latches_so_the_mode_is_visible(viewer_with_controls,
                                                   qt_app):
    """Fit must show that it is the active mode.

    Without a checked state, clicking "Fit to Window" while already fitted
    changes nothing and the button reads as broken.
    """
    window, view = viewer_with_controls
    actions = window._sharpmod_view_actions

    assert actions["fit"].isCheckable()
    assert actions["fit"].isChecked(), "should open latched in fit mode"

    view.zoom_to(2.0)
    qt_app.processEvents()
    assert not actions["fit"].isChecked(), (
        "fit stayed latched after a manual zoom")

    actions["fit"].trigger()
    qt_app.processEvents()
    assert actions["fit"].isChecked()


def test_clicking_fit_while_fitted_keeps_it_latched(viewer_with_controls,
                                                    qt_app):
    """A checkable action toggles itself on click; fit must re-latch.

    Otherwise the label would claim manual mode while the view is still
    tracking the window.
    """
    window, view = viewer_with_controls
    fit = window._sharpmod_view_actions["fit"]

    for _ in range(3):
        fit.trigger()
        qt_app.processEvents()
        assert fit.isChecked(), "fit unlatched itself"
        assert view.is_fit_mode()


def test_readout_distinguishes_fit_from_an_equal_manual_zoom(
        viewer_with_controls, qt_app):
    """Fit and a manual zoom can land on the same percentage.

    The user needs to know which, because only one of them follows the next
    window resize.
    """
    window, view = viewer_with_controls
    readout = window._sharpmod_zoom_readout

    view.fit_to_window()
    qt_app.processEvents()
    fitted_text = readout.text()
    fitted_scale = view.current_scale()
    assert fitted_text.startswith("Fit"), (
        f"fit mode readout does not say so: {fitted_text!r}")

    # Same scale, reached manually.
    view.zoom_to(fitted_scale)
    qt_app.processEvents()
    assert not readout.text().startswith("Fit")
    assert readout.text() != fitted_text


def test_actual_size_latches_only_at_exactly_one_to_one(viewer_with_controls,
                                                        qt_app):
    window, view = viewer_with_controls
    actions = window._sharpmod_view_actions

    view.zoom_to(1.0)
    qt_app.processEvents()
    assert actions["actual"].isChecked()

    view.zoom_in()
    qt_app.processEvents()
    assert not actions["actual"].isChecked()


# ---------------------------------------------------------------------------
# Aspect-ratio waste (documents a real limitation)
# ---------------------------------------------------------------------------


#: A real composed sounding's geometry, measured from the HRRR example after the
#: renderer's layout-compensation and canvas-grow passes.
REAL_CANVAS = QSize(1630, 1091)

#: A maximized widescreen window whose viewport is *shorter* than the canvas, so
#: the 1:1 cap is not the binding constraint. This reproduces the reported case;
#: the shared fixture's smaller stand-in does not, because there the viewport
#: exceeds the canvas on both axes and the cap does bind.
WIDESCREEN = (1920, 1000)


@pytest.fixture
def realistic_view(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard")
    window = QMainWindow()
    canvas = QWidget()
    canvas.setFixedSize(REAL_CANVAS)
    view = gui_viewer._ScaledSoundingView(canvas, REAL_CANVAS, window)
    window.setCentralWidget(view)
    window.resize(*WIDESCREEN)
    window.show()
    for _ in range(6):
        qt_app.processEvents()
    yield view, window
    window.close()


def test_fit_is_limited_by_the_tighter_axis(realistic_view, qt_app):
    """Uniform scaling means the tighter axis wins and the other has slack.

    The sounding's aspect is ~1.49 and a maximized widescreen viewport is ~1.8,
    so fit maxes out the height and leaves horizontal slack. That is inherent to
    preserving aspect ratio, not a zoom bug -- reclaiming it needs the canvas
    layout itself to become responsive.
    """
    view, _window = realistic_view
    view.fit_to_window()
    for _ in range(4):
        qt_app.processEvents()

    vp = view.viewport().size()
    scale = view.current_scale()
    scaled_w = REAL_CANVAS.width() * scale
    scaled_h = REAL_CANVAS.height() * scale

    assert scale < 1.0, "this geometry must not be capped at 1:1"
    # Neither axis may overflow.
    assert scaled_w <= vp.width() + 1
    assert scaled_h <= vp.height() + 1
    # Height is the tight axis; width carries the slack.
    assert abs(scaled_h - vp.height()) < 2, "fit did not max out the height"
    assert vp.width() - scaled_w > 100, (
        "expected substantial horizontal slack from the aspect mismatch")


def test_uncapping_the_fit_scale_would_not_reclaim_the_slack(realistic_view,
                                                             qt_app):
    """The 1:1 cap is not what causes the letterbox in this case.

    Worth recording because lifting the cap is the obvious wrong fix. The cap
    only binds when the viewport exceeds the canvas on *both* axes; here the
    viewport is shorter than the canvas, so the height ratio is already below
    1.0 and removing the cap changes nothing.

    Phrased against the scale the view actually computes. Comparing
    ``min(a, b, 1.0)`` with ``min(a, b)`` is arithmetically guaranteed once
    ``b < 1.0`` is asserted, so the earlier form could not fail and ran no
    product code -- it recorded the reasoning without checking it.
    """
    view, _window = realistic_view
    view.fit_to_window()
    for _ in range(4):
        qt_app.processEvents()

    vp = view.viewport().size()
    by_width = vp.width() / REAL_CANVAS.width()
    by_height = vp.height() / REAL_CANVAS.height()

    assert by_height < 1.0, "fixture geometry does not reproduce the case"
    assert by_height < by_width, "height must be the tighter axis here"
    # The scale the view chose is the height ratio, so the 1.0 term in the fit's
    # min() never entered into it: an uncapped fit would land on this same
    # number and leave exactly the same horizontal slack.
    assert view.current_scale() == pytest.approx(by_height, abs=2e-3), (
        f"fit chose {view.current_scale():.4f}, not the height ratio "
        f"{by_height:.4f}; the cap or something else is binding")


# ---------------------------------------------------------------------------
# Zoom slider
# ---------------------------------------------------------------------------


def test_slider_range_matches_the_view_bounds(viewer_with_controls):
    """A slider that can request a scale the view clamps would feel broken."""
    window, view = viewer_with_controls
    slider = window._sharpmod_zoom_slider
    assert slider.minimum() == int(view.MIN_SCALE * 100)
    assert slider.maximum() == int(view.MAX_SCALE * 100)


@pytest.mark.parametrize("percent", [20, 75, 100, 150, 220, 400])
def test_dragging_the_slider_zooms_the_view(viewer_with_controls, qt_app,
                                           percent):
    window, view = viewer_with_controls
    window._sharpmod_zoom_slider.setValue(percent)
    qt_app.processEvents()
    assert view.current_scale() == pytest.approx(percent / 100.0)
    assert not view.is_fit_mode(), "a slider drag is a manual zoom"


def test_slider_follows_the_buttons_and_fit(viewer_with_controls, qt_app):
    """The slider is also a display, so it must track changes it did not cause."""
    window, view = viewer_with_controls
    slider = window._sharpmod_zoom_slider
    actions = window._sharpmod_view_actions

    actions["actual"].trigger()
    qt_app.processEvents()
    assert slider.value() == 100

    actions["in"].trigger()
    qt_app.processEvents()
    assert slider.value() == pytest.approx(
        round(view.current_scale() * 100), abs=1)

    actions["fit"].trigger()
    qt_app.processEvents()
    assert slider.value() == pytest.approx(
        round(view.current_scale() * 100), abs=1)


def test_slider_does_not_feed_back_into_itself(viewer_with_controls, qt_app):
    """The slider is both input and display of one value.

    Echoing the view's scale back without blocking signals would re-enter the
    slider handler and fight the user mid-drag, and round-trip the value through
    integer percent on every frame.
    """
    window, view = viewer_with_controls
    slider = window._sharpmod_zoom_slider

    emissions = []
    view.scaleChanged.connect(emissions.append)
    slider.setValue(180)
    qt_app.processEvents()

    assert len(emissions) == 1, (
        f"one slider change produced {len(emissions)} scale changes; "
        f"the binding is looping")
    assert slider.value() == 180, "the slider did not settle where it was put"


def test_slider_is_disabled_on_the_one_to_one_host(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard")
    window = QMainWindow()
    gui_viewer._install_view_controls(window)
    canvas = QWidget()
    size = QSize(400, 300)
    canvas.setFixedSize(size)
    window.setCentralWidget(
        gui_viewer._FixedSoundingScrollArea(canvas, size, window))
    gui_viewer._bind_view_controls(window)
    try:
        assert not window._sharpmod_zoom_slider.isEnabled()
    finally:
        window.close()


def test_slider_steps_are_usable(viewer_with_controls):
    """A one-percent page step would make clicking the groove feel dead."""
    slider = viewer_with_controls[0]._sharpmod_zoom_slider
    assert slider.pageStep() >= 10
    assert slider.singleStep() >= 2


# ---------------------------------------------------------------------------
# The canvas surround follows a runtime theme change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory,label", [
    (lambda canvas, size, parent: gui_viewer._ScaledSoundingView(
        canvas, size, parent), "scaled view"),
    (lambda canvas, size, parent: gui_viewer._FixedSoundingScrollArea(
        canvas, size, parent), "fixed scroll area"),
])
def test_canvas_host_is_not_inline_styled(qt_app, factory, label):
    """Both sounding hosts must take their surround from the app style sheet.

    They used to build an inline style sheet from ``current_theme()`` in
    ``__init__``. An inline sheet outranks the application sheet *and* is never
    recomputed, so switching colour style with a sounding open (gui_picker's
    runtime ``apply_theme`` call) repainted everything except the surround
    immediately around the canvas -- which stayed on the previous theme.

    Asserting the absence of the inline sheet is the point: with one present the
    widget cannot follow a theme change no matter what the app sheet says.
    """
    from sharpmod.theme import OBJ_CANVAS_HOST

    gui_theme.apply_theme(qt_app, color_style="standard")
    window = QMainWindow()
    canvas = QWidget()
    canvas.setFixedSize(NATURAL)
    host = factory(canvas, NATURAL, window)
    try:
        assert host.styleSheet() == "", (
            f"{label} carries an inline style sheet, which outranks the app "
            f"sheet and cannot follow a theme change: {host.styleSheet()!r}")
        assert host.objectName() == OBJ_CANVAS_HOST, (
            f"{label} is not named {OBJ_CANVAS_HOST}, so the app sheet's "
            f"canvas-host rule does not reach it")
    finally:
        window.close()


def test_canvas_host_surround_repaints_on_a_theme_switch(qt_app):
    """End to end: the pixels behind the canvas must change with the theme.

    The structural test above proves nothing outranks the app sheet; this proves
    the app sheet actually reaches the widget. Grabs the host with a canvas
    smaller than the viewport, so there is real surround to sample, and compares
    a corner pixel across a dark -> light switch.
    """
    from sharpmod.theme import THEMES

    dark = THEMES["graphite-dark"]
    light = THEMES["paper-light"]

    window = QMainWindow()
    canvas = QWidget()
    canvas.setFixedSize(QSize(80, 60))
    host = gui_viewer._ScaledSoundingView(canvas, QSize(80, 60), window)
    window.setCentralWidget(host)
    window.resize(400, 300)
    window.show()

    def corner_pixel(theme):
        gui_theme.apply_theme(qt_app, theme=theme)
        for _ in range(6):
            qt_app.processEvents()
        image = host.grab().toImage()
        # Bottom-left of the viewport: the canvas is pinned top-centre, so this
        # is surround rather than canvas.
        return image.pixelColor(2, image.height() - 3)

    try:
        dark_px = corner_pixel(dark)
        light_px = corner_pixel(light)

        assert dark_px != light_px, (
            f"the canvas surround did not repaint across a theme switch: "
            f"still {dark_px.name()} after moving to {light.name}")
        # And each matches its theme's token, so this is the intended colour
        # rather than merely some difference.
        assert dark_px.name().lower() == dark.surface_sunken.lower(), (
            f"dark surround is {dark_px.name()}, expected "
            f"{dark.surface_sunken}")
        assert light_px.name().lower() == light.surface_sunken.lower(), (
            f"light surround is {light_px.name()}, expected "
            f"{light.surface_sunken}")
    finally:
        window.close()
        gui_theme.apply_theme(qt_app, color_style="standard")


# ---------------------------------------------------------------------------
# Ctrl+wheel across input devices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,angle,pixel", [
    ("classic wheel notch", 120, 0),
    # A Windows precision touchpad: angleDelta is zero and the scroll arrives in
    # pixelDelta. This is the device the plain-wheel path was fixed for; the
    # Ctrl path read angleDelta directly and so did nothing at all here, while
    # the guide advertises Ctrl+scroll as the whole-sounding zoom.
    ("touchpad, pixels only", 0, 60),
    ("touchpad, small angle", 15, 40),
])
def test_ctrl_wheel_zooms_from_every_device(scaled_view, qt_app, label, angle,
                                           pixel):
    view, _canvas, _window = scaled_view
    before = view.current_scale()

    point = view.viewport().rect().center()
    view.wheelEvent(_wheel_at(view, point, angle=angle, pixel=pixel,
                              modifiers=Qt.ControlModifier))
    qt_app.processEvents()

    assert view.current_scale() > before, (
        f"{label}: Ctrl+wheel did not zoom (scale stayed "
        f"{view.current_scale():.4f})")
    assert not view.is_fit_mode(), f"{label}: Ctrl+wheel is a manual zoom"


@pytest.mark.parametrize("delta", [15, -15, 60, -60, 120, -120])
def test_ctrl_wheel_zoom_is_proportional_to_delta(scaled_view, qt_app, delta):
    """Partial touchpad events must be partial steps, not repeated 25% jumps."""
    view, _canvas, _window = scaled_view
    before = view.current_scale()
    expected = before * view.ZOOM_STEP ** (delta / view._WHEEL_NOTCH)

    point = view.viewport().rect().center()
    view.wheelEvent(_wheel_at(view, point, angle=delta,
                              modifiers=Qt.ControlModifier))
    qt_app.processEvents()

    assert view.current_scale() == pytest.approx(expected)


def test_ctrl_wheel_partial_stream_equals_one_full_notch(scaled_view, qt_app):
    """A touchpad stream totaling 120 units should equal one wheel notch."""
    view, _canvas, _window = scaled_view
    before = view.current_scale()
    point = view.viewport().rect().center()

    for _ in range(8):
        view.wheelEvent(_wheel_at(view, point, angle=15,
                                  modifiers=Qt.ControlModifier))
    qt_app.processEvents()

    assert view.current_scale() == pytest.approx(before * view.ZOOM_STEP)


def test_ctrl_wheel_with_no_delta_is_not_swallowed(scaled_view, qt_app):
    """An event carrying nothing must be left for something else to handle.

    The Ctrl branch used a bare ``return`` on a zero delta, which leaves the
    event accepted -- the same fail-silently shape
    ``test_wheel_outside_the_canvas_is_not_swallowed`` guards on the plain path.
    """
    view, _canvas, _window = scaled_view
    before = view.current_scale()

    event = _wheel_at(view, view.viewport().rect().center(), angle=0, pixel=0,
                      modifiers=Qt.ControlModifier)
    view.wheelEvent(event)
    qt_app.processEvents()

    assert view.current_scale() == pytest.approx(before)
    assert not event.isAccepted(), (
        "the view consumed a Ctrl+wheel it did nothing with, so nothing "
        "downstream can ever see it")
