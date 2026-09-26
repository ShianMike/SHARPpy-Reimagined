"""Viewer Scaled for the sounding viewer."""

from __future__ import annotations

from qtpy.QtCore import QPoint
from qtpy.QtCore import QPointF
from qtpy.QtCore import QSize
from qtpy.QtCore import Qt
from qtpy.QtCore import Signal
from qtpy.QtGui import QPainter
from qtpy.QtGui import QTransform
from qtpy.QtGui import QWheelEvent
from qtpy.QtWidgets import QApplication
from qtpy.QtWidgets import QFrame
from qtpy.QtWidgets import QGraphicsScene
from qtpy.QtWidgets import QGraphicsView
from qtpy.QtWidgets import QScrollArea
from sharpmod.ui.styles.theme import OBJ_CANVAS_HOST
from sharpmod import gui_viewer as _api


class _FixedSoundingScrollArea(QScrollArea):
    """Host the composed sounding at its settled CLI geometry."""

    def __init__(self, widget, natural_size, parent=None):
        super().__init__(parent)
        self._widget = widget
        self._natural_size = QSize(
            max(1, natural_size.width()), max(1, natural_size.height())
        )
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        # Named, not inline-styled. An inline style sheet outranks the
        # application sheet and is never recomputed, so baking the colour in
        # here left the surround pinned to whichever theme happened to be
        # current at construction when the user switched colour style with a
        # sounding open. OBJ_CANVAS_HOST carries the same declarations.
        self.setObjectName(OBJ_CANVAS_HOST)
        self._lock_widget_size()
        self.setWidget(widget)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._lock_widget_size()
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._lock_widget_size()
        super().showEvent(event)

    def _lock_widget_size(self) -> None:
        self._widget.setFixedSize(self._natural_size)
        if self._widget.size() != self._natural_size:
            self._widget.resize(self._natural_size)


class _ScaledSoundingView(QGraphicsView):
    """Host the composed sounding, scaled to fit or to a user-chosen zoom.

    Wraps the fixed-size ``spc_widget`` in a graphics scene and applies a view
    transform. The widget itself stays pinned at its natural render geometry --
    only a view transform scales it -- so the proportions match the PNG renderer
    and interactive editing and exports are unaffected.

    Two modes:

    ``fit``
        The default. The whole sounding is scaled to fit the viewport, capped at
        1:1 so a large screen never upscales and blurs the text. Recomputed on
        every resize.
    ``manual``
        An explicit zoom factor set by the user. Scrollbars appear as needed and
        the view can be panned, so the sounding is readable on a display too
        small to show it whole.

    Manual zoom exists because the fit cap made the sounding uncomfortably small
    on a 1920x1080 display -- the entire layout was visible but the index tables
    and parameter values were near-illegible, with no way to magnify a region.

    Interaction constraints
    -----------------------
    The scene contains a *live, interactive* widget: the Skew-T accepts
    click-and-drag profile edits, both panels accept right-click menus, and the
    vendored hodograph and Skew-T already use the **plain mouse wheel** for their
    own zoom. So:

    * plain wheel is passed straight through to the canvas and never zooms the
      view;
    * view zoom is on **Ctrl+wheel**, anchored under the cursor;
    * panning is on the **middle button**, which no canvas interaction uses.
      ``ScrollHandDrag`` is deliberately not used -- it would swallow the
      left-button drags that edit the profile.
    """

    #: Emitted with the effective scale whenever the transform changes, so a
    #: toolbar can display the current zoom without polling.
    scaleChanged = Signal(float)

    #: Manual-zoom bounds. The lower bound is below any realistic fit scale so
    #: "zoom out" still works on a small window; the upper bound is where the
    #: canvas text becomes visibly interpolated.
    MIN_SCALE = 0.20
    MAX_SCALE = 4.00

    #: Multiplier per zoom step. 1.25 gives a perceptible change without
    #: needing many presses to cross a useful range.
    ZOOM_STEP = 1.25

    #: One classic mouse-wheel notch, in ``angleDelta`` units.
    _WHEEL_NOTCH = 120

    def __init__(self, widget, natural_size, parent=None):
        super().__init__(parent)
        self._natural = QSize(
            max(1, natural_size.width()), max(1, natural_size.height())
        )
        self._widget = widget
        self._fit_mode = True
        self._scale = 1.0
        self._pan_origin = None
        self._pan_scroll = None

        self.setFrameShape(QFrame.NoFrame)
        # See _FixedSoundingScrollArea: named so the surround follows a runtime
        # theme switch instead of freezing at construction.
        self.setObjectName(OBJ_CANVAS_HOST)
        # Scrollbars are off while fitting (there is nothing to scroll) and
        # switch to as-needed once a manual zoom can overflow the viewport.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )
        # Pin the sounding to the top of the view so any spare vertical space
        # (e.g. when the window is maximized or taller than the scaled canvas)
        # collects at the bottom instead of leaving a gap above the Skew-T.
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        widget.setFixedSize(self._natural)
        scene = QGraphicsScene(self)
        proxy = scene.addWidget(widget)
        # Pin the proxy to the scene origin. addWidget keeps whatever position
        # the widget had under its previous parent, and the sounding arrives here
        # having been the central widget of a QMainWindow, so it carried a y
        # offset of the menu bar's height (29 px in practice). The scene rect
        # below starts at 0, so that offset pushed the *bottom* 29 px of the
        # sounding outside the fitted area -- and fit mode turns the scrollbars
        # off, so the lower index rows were silently unreachable.
        proxy.setPos(0, 0)
        scene.setSceneRect(0, 0, self._natural.width(), self._natural.height())
        self.setScene(scene)

    # -- scale state -------------------------------------------------------- #

    def current_scale(self) -> float:
        """Return the scale currently applied to the view."""
        return self._scale

    def is_fit_mode(self) -> bool:
        return self._fit_mode

    def fit_scale(self) -> float:
        """Return the scale that shows the whole sounding, capped at 1:1."""
        vp = self.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return 1.0
        return min(
            vp.width() / self._natural.width(),
            vp.height() / self._natural.height(),
            1.0,
        )

    def _apply_scale(self, scale: float) -> None:
        scale = max(self.MIN_SCALE, min(float(scale), self.MAX_SCALE))
        self._scale = scale
        self.setTransform(QTransform().scale(scale, scale))
        self.scaleChanged.emit(scale)

    def _set_scrollbars_for_mode(self) -> None:
        policy = Qt.ScrollBarAlwaysOff if self._fit_mode else Qt.ScrollBarAsNeeded
        self.setHorizontalScrollBarPolicy(policy)
        self.setVerticalScrollBarPolicy(policy)

    # -- public actions ----------------------------------------------------- #

    def fit_to_window(self) -> None:
        """Return to fit mode and rescale to show the whole sounding."""
        self._fit_mode = True
        self._set_scrollbars_for_mode()
        self._apply_scale(self.fit_scale())

    def zoom_to(self, scale: float) -> None:
        """Switch to manual mode at an explicit ``scale`` (1.0 = actual size)."""
        self._fit_mode = False
        self._set_scrollbars_for_mode()
        self._apply_scale(scale)

    def zoom_in(self) -> None:
        self.zoom_to(self._scale * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_to(self._scale / self.ZOOM_STEP)

    # -- Qt overrides ------------------------------------------------------- #

    def _refit(self) -> None:
        """Recompute the fit scale. A no-op once the user has set a zoom."""
        if not self._fit_mode:
            return
        vp = self.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return
        self._apply_scale(self.fit_scale())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._refit()

    def _panel_under(self, viewport_pos):
        """The canvas child under a viewport point, and the point in its coords.

        The proxy sits at the scene origin at 1:1, so scene coordinates *are*
        coordinates in the composed sounding widget.
        """
        scene_pt = self.mapToScene(viewport_pos).toPoint()
        if not self._widget.rect().contains(scene_pt):
            return None, None
        target = self._widget.childAt(scene_pt) or self._widget
        return target, target.mapFrom(self._widget, scene_pt)

    def _plain_wheel_delta(self, event) -> int:
        """The scroll amount in ``angleDelta`` units, whatever the device sent.

        The vendored zoom reads ``angleDelta`` and nothing else. A classic mouse
        wheel obliges with 120 units per notch, but a Windows precision touchpad
        can report ``angleDelta`` of zero and put the scroll in ``pixelDelta``,
        which moved the zoom not at all.

        The amount is passed through proportionally rather than rounded up to
        whole notches: a touchpad sends a stream of small deltas, and forwarding
        each one keeps the zoom smooth. Quantising instead imposed a threshold
        below which a slow scroll did nothing, which is the complaint in a
        different costume.
        """
        delta = event.angleDelta().y()
        if delta:
            return delta
        pixels = event.pixelDelta().y()
        if not pixels:
            return 0
        # 50 px per notch: enough that a deliberate swipe zooms noticeably
        # without a nudge throwing the scale across the panel.
        return int(round(pixels * (self._WHEEL_NOTCH / 50.0)))

    def _forward_panel_zoom(self, event) -> bool:
        """Send a wheel event the vendored panels understand, and say so.

        Returns ``True`` when the event was handled here. Re-emitting the event
        rather than letting it pass through the graphics proxy fixes two separate
        faults: a full-notch event carrying a ``ScrollUpdate`` phase -- what a
        touchpad sends for every event after the first -- was dropped before it
        reached the panel at all, and a pixel-only event moved nothing. It also
        puts the zoom anchor exactly under the cursor, because the position is
        computed here rather than taken on trust.
        """
        position = (
            event.position().toPoint() if hasattr(event, "position") else event.pos()
        )
        target, local = self._panel_under(position)
        if target is None:
            return False

        delta = self._plain_wheel_delta(event)
        if not delta:
            return False

        synthetic = QWheelEvent(
            QPointF(local),
            QPointF(target.mapToGlobal(local)),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(target, synthetic)
        return True

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Ctrl+wheel zooms the view; a plain wheel belongs to the canvas."""
        if not (event.modifiers() & Qt.ControlModifier):
            # The vendored Skew-T and hodograph use the plain wheel for their
            # own zoom. Forward it explicitly rather than relying on the
            # graphics proxy: see _forward_panel_zoom for what the proxy route
            # dropped.
            if self._forward_panel_zoom(event):
                event.accept()
                return
            super().wheelEvent(event)
            return

        # Through _plain_wheel_delta, not event.angleDelta() directly. The whole
        # reason that helper exists is that a Windows precision touchpad reports
        # angleDelta of zero and puts the scroll in pixelDelta -- and reading
        # angleDelta here meant Ctrl+scroll did nothing at all on a touchpad,
        # while the guide advertises it as the whole-sounding zoom. The plain
        # wheel was fixed for exactly this device; this branch was not.
        delta = self._plain_wheel_delta(event)
        if not delta:
            # Explicitly unhandled, so the scroll area or an ancestor still gets
            # a chance. A bare return leaves the event accepted and swallows it.
            event.ignore()
            return

        # Anchor under the cursor so Ctrl+wheel magnifies whatever the user is
        # pointing at, then restore the resize anchor used by fit mode.
        previous_anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        try:
            # A full 120-unit wheel notch remains one ZOOM_STEP. Precision
            # touchpads report streams of partial angle deltas, so scale those
            # proportionally instead of turning every nonzero event into a full
            # 25% jump.
            step = self.ZOOM_STEP ** (delta / self._WHEEL_NOTCH)
            self.zoom_to(self._scale * step)
        finally:
            self.setTransformationAnchor(previous_anchor)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Begin a middle-button pan, or hand the event to the canvas."""
        if event.button() == Qt.MiddleButton:
            self._pan_origin = (
                event.position().toPoint()
                if hasattr(event, "position")
                else event.pos()
            )
            self._pan_scroll = (
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._pan_origin is not None and self._pan_scroll is not None:
            position = (
                event.position().toPoint()
                if hasattr(event, "position")
                else event.pos()
            )
            delta = position - self._pan_origin
            h_start, v_start = self._pan_scroll
            self.horizontalScrollBar().setValue(h_start - delta.x())
            self.verticalScrollBar().setValue(v_start - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MiddleButton and self._pan_origin is not None:
            self._pan_origin = None
            self._pan_scroll = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _fit_window_to_screen(app, win) -> None:
    """Display the sounding at a size that fits the current screen.

    The vendored ``spc_widget`` keeps the composed natural (CLI-render)
    geometry, so the Skew-T, hodograph, and index panels keep the same
    proportions used by the PNG renderer. If that size fits the screen it is
    shown 1:1 in a scroll host; otherwise (e.g. on 1920x1080) it is embedded in
    a :class:`_ScaledSoundingView` that scales the whole sounding down to fit,
    so the entire layout is visible without scrolling.
    """
    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return
    nat_w, nat_h = sw.width(), sw.height()
    if nat_w <= 1 or nat_h <= 1:
        return
    try:
        # Pin the sounding to its settled render size so no child layout can
        # squish it back to the vendored Windows geometry.
        natural = QSize(nat_w, nat_h)
        sw.setFixedSize(natural)

        # Detach the original central widget before re-parenting it into the
        # new host; otherwise QMainWindow may delete it when replacing the
        # central widget.
        old_central = win.takeCentralWidget()
        if old_central is not None and old_central is not sw:
            old_central.setParent(None)
        sw.setParent(None)

        mb = win.menuBar()
        mb_h = mb.sizeHint().height() or mb.height() or 26
        # Toolbars are stacked above the central widget, so they cost the
        # sounding height exactly as the menu bar does. This was omitted, which
        # understated the chrome by the View toolbar's ~57px (and by the timeline
        # toolbar's as well, on a forecast sounding). The resulting initial scale
        # was too large and the lower index rows were only reachable by scrolling
        # until the post-show fit corrected it.
        chrome_h = mb_h + _api._reserved_toolbar_height(win)
        screen = app.primaryScreen().availableGeometry()

        # Reserve space for the window frame (title bar + borders) so the whole
        # window -- including its bottom edge -- stays on screen when it opens
        # non-maximized. Without this the client area can be as tall as the
        # work area and the frame pushes the bottom index tables off-screen.
        FRAME_W = 16
        FRAME_H = 48
        # Docked panels sit beside the central widget, so the sounding never
        # gets their width. Subtracting it here keeps the pre-show estimate
        # honest; without it the window opens too narrow to hold both and the
        # view is briefly squeezed before _finalize_scaled_fit corrects it.
        avail_w = max(320, screen.width() - FRAME_W - _api._reserved_dock_width(win))
        avail_h = max(240, screen.height() - FRAME_H)

        # Largest uniform scale (never above 1:1) that fits the sounding plus
        # the menu bar and toolbars inside the available work area.
        content_max_h = max(1, avail_h - chrome_h)
        scale = min(avail_w / nat_w, content_max_h / nat_h, 1.0)

        # Use the transform-capable host even when the sounding initially fits at
        # 1:1. A native-size window can later be shrunk or moved to a smaller
        # display; keeping the same host lets fit mode respond to that resize and
        # keeps the zoom controls useful for the lifetime of the viewer.
        win.setCentralWidget(_api._ScaledSoundingView(sw, natural, win))
        client_w = int(round(nat_w * scale))
        client_h = int(round(nat_h * scale))

        # client_w is the sounding's width; the window also has to hold the
        # docked panel beside it, and the menu bar plus toolbars above it.
        win.resize(client_w + _api._reserved_dock_width(win), client_h + chrome_h)
        try:
            # Centre the window on the work area.
            frame = win.frameGeometry()
            frame.moveCenter(screen.center())
            # Keep the top-left within the work area so the title bar/menu are
            # always reachable.
            tl = frame.topLeft()
            tl.setX(max(screen.left(), tl.x()))
            tl.setY(max(screen.top(), tl.y()))
            win.move(tl)
        except Exception:
            pass
    except Exception:
        # Never let the fit/scroll wrap block the interactive window.
        pass


def _finalize_scaled_fit(app, win) -> None:
    """Snap the window to wrap the sounding exactly after native realization.

    Runs after the window is shown, when the *actual* menu-bar / window-frame
    heights are known (the pre-show pass can only estimate them). Native 1:1
    windows grow their viewport to the exact sounding size so transient scroll
    bars do not consume 12-13 pixels. Scaled windows recompute the largest
    uniform scale that fits the work area using the measured chrome.
    """
    try:
        view = win.centralWidget()
        vp = view.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return

        scr = app.primaryScreen().availableGeometry()

        # Measured chrome: everything in the window that is NOT the viewport
        # (menu bar, borders) and the title-bar/frame outside the client area.
        chrome_w = max(0, win.width() - vp.width())
        chrome_h = max(0, win.height() - vp.height())
        frame_w = max(0, win.frameGeometry().width() - win.width())
        frame_h = max(0, win.frameGeometry().height() - win.height())

        if isinstance(view, _api._FixedSoundingScrollArea):
            nat = view._natural_size
            new_w = nat.width() + chrome_w
            new_h = nat.height() + chrome_h
            if new_w + frame_w <= scr.width() and new_h + frame_h <= scr.height():
                win.resize(new_w, new_h)
        elif isinstance(view, _api._ScaledSoundingView):
            nat = view._natural
            avail_vp_w = scr.width() - frame_w - chrome_w
            avail_vp_h = scr.height() - frame_h - chrome_h
            if avail_vp_w <= 1 or avail_vp_h <= 1:
                return

            scale = min(
                avail_vp_w / nat.width(),
                avail_vp_h / nat.height(),
                1.0,
            )
            vp_w = int(round(nat.width() * scale))
            vp_h = int(round(nat.height() * scale))
            new_w = vp_w + chrome_w
            new_h = vp_h + chrome_h

            if abs(new_w - win.width()) > 2 or abs(new_h - win.height()) > 2:
                win.resize(new_w, new_h)
            view._refit()
        else:
            return

        # Re-centre on the work area, keeping the title bar reachable.
        frame = win.frameGeometry()
        frame.moveCenter(scr.center())
        tl = frame.topLeft()
        tl.setX(max(scr.left(), tl.x()))
        tl.setY(max(scr.top(), tl.y()))
        win.move(tl)
    except Exception:
        pass
