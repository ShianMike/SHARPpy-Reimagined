"""Hodograph zoom, annotations, and vector patch implementations."""

from __future__ import annotations

import os

from sharpmod.render_patches.palette import _semantic_qcolor

HODO_0_500_COLOR = os.environ.get("HODO_0_500_COLOR", "#FF00FF")
HODO_ZOOM_KTS = float(os.environ.get("HODO_ZOOM_KTS", "160"))


def _install_hodo_0500():
    """Add a distinct 0-500 m band to the hodograph height coloring.

    The vendored ``plotHodo.draw_hodo`` colors segments at 0-1/1-3/3-6/6-9/9-12
    km. This overrides it to insert a 500 m boundary so the innermost 0-500 m of
    the hodograph is drawn in :data:`HODO_0_500_COLOR`, then the usual config
    band colors. Falls back to the original on any error. Idempotent.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod
        import sharppy.sharptab as _tab
        import numpy as _np
        _cls = _hodo_mod.plotHodo
        if getattr(_cls, "_sharpmod_0500", False):
            return
        _QtGui = _hodo_mod.QtGui
        _QtCore = _hodo_mod.QtCore
        try:
            from qtpy.QtGui import QPainterPath as _QPP
        except Exception:
            _QPP = _QtGui.QPainterPath
        _orig = _cls.draw_hodo

        def draw_hodo(self, qp, prof, colors, width=2):
            try:
                try:
                    mask = _np.maximum(_np.maximum(prof.u.mask, prof.v.mask),
                                       prof.hght.mask)
                    z = _tab.interp.to_agl(prof, prof.hght)[~mask]
                    u = prof.u[~mask]; v = prof.v[~mask]
                except Exception:
                    z = _tab.interp.to_agl(prof, prof.hght)
                    u = prof.u; v = prof.v
                xx, yy = self.uv_to_pix(u, v)
                # Insert a single 500 m boundary into the vendored band edges
                # (0-3/3-6/6-9/9-12 km). This yields five segments whose colors
                # line up 1:1 with ``[0-500 m] + colors`` -- the 0-500 m band is
                # magenta and the remaining bands keep their configured colors
                # (500 m-3 km, 3-6, 6-9, 9-12). The previous version inserted a
                # 1000 m edge too, which shifted every band's color and ran the
                # colors list out of range.
                seg_bnds = _np.maximum(
                    [0., 500., 3000., 6000., 9000., 12000.], z.min())
                c0500 = _semantic_qcolor(
                    self, "hodo_0500", override=HODO_0_500_COLOR)
                hcolors = [c0500] + list(colors)   # 0-500 m + the 4 bands
                seg_x = [_tab.interp.generic_interp_hght(b, z, xx)
                         for b in seg_bnds if b <= z.max()]
                seg_y = [_tab.interp.generic_interp_hght(b, z, yy)
                         for b in seg_bnds if b <= z.max()]
                seg_idxs = _np.searchsorted(z, seg_bnds)
                for idx in range(len(seg_x) - 1):
                    pen = _QtGui.QPen(hcolors[idx], width)
                    pen.setStyle(_QtCore.Qt.SolidLine)
                    qp.setPen(pen)
                    path = _QPP()
                    path.moveTo(seg_x[idx], seg_y[idx])
                    for z_idx in range(seg_idxs[idx], seg_idxs[idx + 1]):
                        path.lineTo(xx[z_idx], yy[z_idx])
                    path.lineTo(seg_x[idx + 1], seg_y[idx + 1])
                    qp.drawPath(path)
                if z.max() < max(seg_bnds):
                    idx = len(seg_x) - 1
                    pen = _QtGui.QPen(hcolors[idx], width)
                    pen.setStyle(_QtCore.Qt.SolidLine)
                    qp.setPen(pen)
                    path = _QPP()
                    path.moveTo(seg_x[idx], seg_y[idx])
                    for z_idx in range(seg_idxs[idx], len(xx)):
                        path.lineTo(xx[z_idx], yy[z_idx])
                    qp.drawPath(path)
            except Exception:
                _orig(self, qp, prof, colors, width=width)

        _cls.draw_hodo = draw_hodo
        _cls._sharpmod_0500 = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_hodo_zoom():
    """Use a 20%-tighter default hodograph viewport.

    The vendored ``backgroundHodo`` scales uniformly from ``hodomag`` -- the
    wind magnitude (in the active units) spanning the full widget width. The
    prior 200-kt view is reduced by 20% to :data:`HODO_ZOOM_KTS` (160 kt),
    increasing visual magnification by 25%. The metric default is scaled
    proportionally and kept within the vendored ``max_zoom``. Applied by
    wrapping ``backgroundHodo.__init__`` (initial draw) and
    ``plotHodo.setPreferences`` (units / preference changes) so the zoom
    survives both. Falls back to the vendored behavior on any error. Idempotent.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod
        import sharppy.sharptab as _tab
        import numpy as _np

        _bg = _hodo_mod.backgroundHodo
        _plot = _hodo_mod.plotHodo
        if getattr(_bg, "_sharpmod_zoom", False):
            return

        _kts = float(HODO_ZOOM_KTS)
        # Metric equivalent, rounded to the vendored 5 m/s ring increment and
        # clamped to the vendored metric max_zoom (100 m/s).
        _ms = min(round(_tab.utils.KTS2MS(_kts) / 5.0) * 5.0, 100.0)

        def _apply_zoom(self):
            """Override hodomag for the active units and recompute scale/rings."""
            try:
                if getattr(self, "wind_units", "knots") == "m/s":
                    self.hodomag = _ms
                    self.max_zoom = max(getattr(self, "max_zoom", 0.0), _ms)
                    conv = _tab.utils.KTS2MS
                else:
                    self.hodomag = _kts
                    self.max_zoom = max(getattr(self, "max_zoom", 0.0), _kts)
                    conv = lambda s: s
                self.scale = (self.brx - self.tlx) / self.hodomag
                max_uv = int(conv(_np.hypot(*self.pix_to_uv(self.brx, self.bry))))
                self.rings = range(self.ring_increment,
                                   max_uv + self.ring_increment,
                                   self.ring_increment)
            except Exception:
                pass

        _orig_init = _bg.__init__

        def __init__(self, **kwargs):
            _orig_init(self, **kwargs)
            _apply_zoom(self)
            # Rebuild the background pixmap with the zoomed-out scale.
            try:
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
                self.backgroundBitMap = self.plotBitMap.copy()
            except Exception:
                pass

        _orig_prefs = _plot.setPreferences

        def setPreferences(self, update_gui=True, **kwargs):
            _orig_prefs(self, update_gui=False, **kwargs)
            _apply_zoom(self)
            try:
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
                self.backgroundBitMap = self.plotBitMap.copy()
            except Exception:
                pass
            if update_gui:
                try:
                    self.clearData()
                    self.plotData()
                    self.update()
                    self.parentWidget().setFocus()
                except Exception:
                    pass

        _bg.__init__ = __init__
        _plot.setPreferences = setPreferences
        _bg._sharpmod_zoom = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_hodo_mean_wind_center():
    """Default the hodograph to its LCL-to-EL mean-wind center.

    Vendored SHARPpy exposes ``Mean Wind`` in the hodograph context menu but
    initializes every widget in ``Normal`` mode, centered on the zero-wind
    origin. The mean vector is unavailable until a profile collection becomes
    active, so this patch selects the mode at construction and applies it after
    profile activation. It also reapplies the selected non-normal center after
    resizes, preference changes, and wheel zooms, all of which rebuild the
    vendored background around the origin.

    A later user choice remains authoritative: selecting ``Normal`` stops the
    automatic mean-wind recentering, while ``Storm Relative`` continues to use
    the active profile's right-moving storm vector.
    """
    try:
        import sharppy.sharptab as _tab
        import sharppy.viz.hodo as _hodo_mod

        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_mean_wind_default", False):
            return

        def _checked_mode(self):
            modes = {
                "centered": "Normal",
                "stormrelative": "Storm Relative",
                "meanwind": "Mean Wind",
            }
            selected = modes.get(getattr(self, "center_loc", ""))
            try:
                for action in self.popupmenu.actions():
                    if action.text() in modes.values():
                        action.setChecked(action.text() == selected)
            except Exception:
                pass

        def _valid_vector(vector):
            try:
                return (
                    vector is not None
                    and len(vector) >= 2
                    and _tab.utils.QC(vector[0])
                    and _tab.utils.QC(vector[1])
                )
            except Exception:
                return False

        def _selected_vector(self):
            mode = getattr(self, "center_loc", "centered")
            if mode == "meanwind":
                return getattr(self, "mean_lcl_el", None)
            if mode == "stormrelative":
                storm_motion = getattr(self, "srwind", None)
                if storm_motion is not None and len(storm_motion) >= 2:
                    return storm_motion[:2]
            return None

        def _apply_selected_center(self):
            vector = _selected_vector(self)
            if not _valid_vector(vector):
                _checked_mode(self)
                return False

            # Rebase first so center_hodo never applies a new scale/size to an
            # offset retained from the previous profile or widget geometry.
            self.centerx = self.wid / 2.0
            self.centery = self.hgt / 2.0
            self.point = (0, 0)
            self.centered = (vector[0], vector[1])
            self.clearData()
            self.center_hodo(self.centered)
            try:
                self.updateDraggables()
            except Exception:
                pass
            self.plotData()
            self.update()
            _checked_mode(self)
            return True

        _orig_init = _plot.__init__

        def __init__(self, **kwargs):
            _orig_init(self, **kwargs)
            self.centered = (0, 0)
            self.center_loc = "meanwind"
            _checked_mode(self)

        _orig_set_active = _plot.setActiveCollection

        def setActiveCollection(self, pc_idx, **kwargs):
            result = _orig_set_active(self, pc_idx, **kwargs)
            _apply_selected_center(self)
            return result

        _orig_resize = _plot.resizeEvent

        def resizeEvent(self, event):
            result = _orig_resize(self, event)
            _apply_selected_center(self)
            return result

        _orig_prefs = _plot.setPreferences

        def setPreferences(self, update_gui=True, **kwargs):
            result = _orig_prefs(self, update_gui=update_gui, **kwargs)
            _apply_selected_center(self)
            return result

        _orig_wheel = _plot.wheelEvent

        def wheelEvent(self, event):
            result = _orig_wheel(self, event)
            _apply_selected_center(self)
            return result

        _plot.__init__ = __init__
        _plot.setActiveCollection = setActiveCollection
        _plot.resizeEvent = resizeEvent
        _plot.setPreferences = setPreferences
        _plot.wheelEvent = wheelEvent
        _plot._sharpmod_mean_wind_default = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_hodo_interpolation_menu():
    """Add focused-profile interpolation controls to the hodograph menu.

    Upstream exposes ``Interpolate Focused Profile`` / ``Reset Interpolation``
    only from the main Profiles menu and the ``I`` shortcut. The hodograph
    already has a right-click menu for cursor/centering/reset controls, so add
    the same focused-profile interpolation action there and route it through
    the owning ``SPCWindow``. Calling the window-level methods keeps the main
    Profiles menu visibility in sync with the current interpolation state.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod

        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_interp_menu", False):
            return

        _QtWidgets = _hodo_mod.QtWidgets

        def _owner(widget):
            candidates = []
            try:
                candidates.append(widget.window())
            except Exception:
                pass

            cur = widget
            for _ in range(12):
                if cur is None:
                    break
                candidates.append(cur)
                try:
                    cur = cur.parentWidget()
                except Exception:
                    break

            for candidate in candidates:
                if candidate is None:
                    continue
                if (hasattr(candidate, "interpProf")
                        and hasattr(candidate, "resetProf")
                        and hasattr(candidate, "spc_widget")):
                    return candidate
            return None

        def _is_interpolated(widget):
            win = _owner(widget)
            try:
                return bool(win.spc_widget.isInterpolated())
            except Exception:
                return False

        def _interpolate(widget):
            win = _owner(widget)
            if win is not None:
                win.interpProf()

        def _reset(widget):
            win = _owner(widget)
            if win is not None:
                win.resetProf()

        def _sync(widget):
            try:
                interp = _is_interpolated(widget)
                widget.sharpmod_hodo_interp_action.setVisible(not interp)
                widget.sharpmod_hodo_reset_interp_action.setVisible(interp)
            except Exception:
                pass

        _orig_init = _plot.__init__

        def __init__(self, **kwargs):
            _orig_init(self, **kwargs)
            if hasattr(self, "sharpmod_hodo_interp_action"):
                return

            try:
                self.popupmenu.addSeparator()

                interp_action = _QtWidgets.QAction(
                    "Interpolate Focused Profile", self)
                interp_action.triggered.connect(lambda: _interpolate(self))
                self.popupmenu.addAction(interp_action)

                reset_action = _QtWidgets.QAction("Reset Interpolation", self)
                reset_action.triggered.connect(lambda: _reset(self))
                reset_action.setVisible(False)
                self.popupmenu.addAction(reset_action)

                self.sharpmod_hodo_interp_action = interp_action
                self.sharpmod_hodo_reset_interp_action = reset_action
            except Exception:
                pass

        _orig_show = _plot.showCursorMenu

        def showCursorMenu(self, pos):
            _sync(self)
            return _orig_show(self, pos)

        _plot.__init__ = __init__
        _plot.showCursorMenu = showCursorMenu
        _plot._sharpmod_interp_menu = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _fit_rect_to_hodo(widget, qtcore, rect, pad=2):
    """Return ``rect`` shifted/shrunk so it stays inside the hodograph frame."""
    left_limit = float(getattr(widget, "tlx", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tly", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad

    if right_limit <= left_limit or bottom_limit <= top_limit:
        return rect

    width = min(float(rect.width()), max(1.0, right_limit - left_limit))
    height = min(float(rect.height()), max(1.0, bottom_limit - top_limit))
    max_left = right_limit - width
    max_top = bottom_limit - height
    left = min(max(float(rect.x()), left_limit), max_left)
    top = min(max(float(rect.y()), top_limit), max_top)
    return qtcore.QRectF(left, top, width, height)


def _place_hodo_annotation_rect(widget, qtcore, marker_rect, width, height,
                                *, occupied=(), gap=5, pad=2):
    """Place a hodo label beside its marker without touching other annotations.

    Right is preferred for the familiar marker-then-value reading order.  Near
    an edge or an occupied annotation, the label automatically tries left,
    below, then above before falling back to the least-overlapping fitted
    candidate.
    """
    marker = qtcore.QRectF(marker_rect)
    width = max(1.0, float(width))
    height = max(1.0, float(height))
    gap = max(0.0, float(gap))
    pad = max(0.0, float(pad))
    center = marker.center()

    candidates = (
        qtcore.QRectF(
            marker.right() + gap, center.y() - height / 2.0,
            width, height),
        qtcore.QRectF(
            marker.left() - gap - width, center.y() - height / 2.0,
            width, height),
        qtcore.QRectF(
            center.x() - width / 2.0, marker.bottom() + gap,
            width, height),
        qtcore.QRectF(
            center.x() - width / 2.0, marker.top() - gap - height,
            width, height),
    )

    left_limit = float(getattr(widget, "tlx", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tly", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad
    exclusions = [marker, *[qtcore.QRectF(rect) for rect in occupied]]

    def inside(rect):
        return (
            rect.left() >= left_limit
            and rect.right() <= right_limit
            and rect.top() >= top_limit
            and rect.bottom() <= bottom_limit
        )

    def collision_area(rect):
        area = 0.0
        for obstacle in exclusions:
            expanded = qtcore.QRectF(obstacle).adjusted(
                -gap, -gap, gap, gap)
            overlap = rect.intersected(expanded)
            if not overlap.isEmpty():
                area += float(overlap.width()) * float(overlap.height())
        return area

    for candidate in candidates:
        if inside(candidate) and collision_area(candidate) == 0:
            return candidate

    fitted = [
        _fit_rect_to_hodo(widget, qtcore, candidate, pad=pad)
        for candidate in candidates
    ]
    return min(
        fitted,
        key=lambda rect: (
            collision_area(rect),
            abs(rect.center().x() - center.x())
            + abs(rect.center().y() - center.y()),
        ),
    )
