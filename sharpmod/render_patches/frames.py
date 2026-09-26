"""Matching frames for vendored chart insets and panel containers."""

from __future__ import annotations

from qtpy import QtCore, QtGui

from sharpmod.viz import colors

#: One source of truth for the reduced plot-box stroke weight.  The Skew-T and
#: every auxiliary plot use it together.
PANEL_FRAME_WIDTH = colors.PLOT_FRAME_WIDTH

#: The bottom products band is a container as well as a visible plot box.  Its
#: upstream ``QWidget`` selector also matches every child plot, which paints a
#: second one-pixel border immediately inside the container border.  Give the
#: frame a stable object name so its rule can target the container alone.
BOTTOM_BAND_OBJECT_NAME = "sharpmod_bottom_band"

#: Vendored ``(module, class)`` pairs that outline themselves in ``draw_frame``.
_FRAMED_INSETS = (
    ("advection", "backgroundAdvection"),
    ("analogues", "backgroundAnalogues"),
    ("ensemble", "backgroundENS"),
    ("fire", "backgroundFire"),
    ("generic", "backgroundGeneric"),
    ("hodo", "backgroundHodo"),
    ("kinematics", "backgroundKinematics"),
    ("ship", "backgroundSHIP"),
    ("skew", "backgroundSkewT"),
    ("slinky", "backgroundSlinky"),
    ("speed", "backgroundSpeed"),
    ("srwinds", "backgroundWinds"),
    ("stp", "backgroundSTP"),
    ("stpef", "backgroundSTPEF"),
    ("thermo", "backgroundText"),
    ("thetae", "backgroundThetae"),
    ("vrot", "backgroundVROT"),
    ("watch", "backgroundWatch"),
    ("winter", "backgroundWinter"),
)


class _MatchingFramePainter:
    """Forward painter calls while matching the first frame pen to the Skew-T.

    Wrapped around a vendored ``draw_frame`` rather than restating it, so the
    frame geometry and any title text the same method paints stay upstream and
    cannot drift from it.  Only the first wide pen is a panel-frame candidate;
    later wide pens belong to data such as STP curves and pass through exactly.

    Three corrections are limited to that frame:

    *The stroke matches the Skew-T.* The candidate pen receives the widget's
    foreground colour and the shared reduced width. Anything that is not a pen --
    ``Qt.NoPen``, a bare ``QColor`` -- passes straight through.

    *Coordinates are pulled inside the widget* when ``bounds`` is given. Every
    framed inset sets ``rpad`` to zero and so ``brx`` to its own ``width()``,
    which puts the right-hand frame line one column past the last paintable pixel
    where it is clipped away entirely -- these panels have only ever had three
    sides. Clamping is deliberately confined to ``draw_frame``: it is the one
    method whose whole job is an outline on the boundary, so pinning a line to
    the edge is what it meant in the first place.

    *A shared divider is painted once.* Grid neighbours touch with zero spacing,
    so one widget's right/bottom edge and the next widget's left/top edge occupy
    adjacent pixels. The latter owns that divider; suppressing the former keeps
    the visually reduced outline one pixel wide.
    """

    __slots__ = (
        "_frame_color", "_frame_pen_active", "_frame_pen_seen", "_max_x",
        "_max_y", "_pen_type", "_qp", "_sides", "_width",
    )

    def __init__(
        self,
        qp,
        pen_type,
        frame_color,
        width=PANEL_FRAME_WIDTH,
        bounds=None,
        sides=None,
    ):
        self._qp = qp
        self._pen_type = pen_type
        self._frame_color = frame_color
        self._width = float(width)
        self._sides = frozenset(
            sides or ("left", "top", "right", "bottom"))
        self._frame_pen_active = False
        self._frame_pen_seen = False
        if bounds is None:
            self._max_x = None
            self._max_y = None
        else:
            width, height = bounds
            self._max_x = max(0, int(width) - 1)
            self._max_y = max(0, int(height) - 1)

    def setPen(self, pen, *args, **kwargs):  # noqa: N802 - Qt API name
        try:
            width = float(pen.widthF())
        except Exception:  # noqa: BLE001 - not a pen, nothing to match
            self._frame_pen_active = False
            return self._qp.setPen(pen, *args, **kwargs)
        # All geometric vendored frames begin with an upstream 2 px pen (the
        # Skew-T first uses a zero-width background pen, which deliberately does
        # not count).
        # Matching only that first wide pen avoids recolouring/thinning curves
        # drawn later by a few overloaded ``draw_frame`` implementations.
        if not self._frame_pen_seen and width >= 2.0:
            matched = self._pen_type(pen)
            matched.setColor(self._frame_color)
            matched.setWidthF(self._width)
            pen = matched
            self._frame_pen_seen = True
            self._frame_pen_active = True
        else:
            self._frame_pen_active = False
        return self._qp.setPen(pen, *args, **kwargs)

    @staticmethod
    def _clamp(value, limit):
        """Pull ``value`` into ``0..limit``, keeping int coordinates ints."""
        if value < 0:
            result = 0
        elif value > limit:
            result = limit
        else:
            return value
        return float(result) if isinstance(value, float) else int(result)

    @staticmethod
    def _pixel_center(value):
        """Place a one-pixel antialiased stroke on a physical pixel center."""
        import math

        return math.floor(float(value)) + 0.5

    def drawLine(self, *args, **kwargs):  # noqa: N802 - Qt API name
        if (
            self._max_x is None
            or not self._frame_pen_active
            or len(args) != 4
            or kwargs
        ):
            return self._qp.drawLine(*args, **kwargs)
        try:
            x1, y1, x2, y2 = args
            if abs(float(x1) - float(x2)) < 1e-9:
                if float(x1) <= 0.0 and "left" not in self._sides:
                    return None
                if float(x1) >= self._max_x and "right" not in self._sides:
                    return None
            elif abs(float(y1) - float(y2)) < 1e-9:
                if float(y1) <= 0.0 and "top" not in self._sides:
                    return None
                if float(y1) >= self._max_y and "bottom" not in self._sides:
                    return None
            return self._qp.drawLine(QtCore.QLineF(
                self._pixel_center(self._clamp(x1, self._max_x)),
                self._pixel_center(self._clamp(y1, self._max_y)),
                self._pixel_center(self._clamp(x2, self._max_x)),
                self._pixel_center(self._clamp(y2, self._max_y)),
            ))
        except (TypeError, ValueError):
            # Points rather than four scalars; nothing to clamp.
            return self._qp.drawLine(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._qp, name)


def _frame_sides_for_widget(widget):
    """Return the frame sides this widget owns in a zero-spacing layout."""
    explicit = getattr(widget, "_sharpmod_frame_sides", None)
    if explicit:
        if isinstance(explicit, str):
            return frozenset((explicit,))
        return frozenset(explicit)

    sides = {"left", "top", "right", "bottom"}
    try:
        parent = widget.parentWidget()
        own = widget.geometry()
        siblings = parent.children() if parent is not None else ()
    except Exception:  # noqa: BLE001 - stand-ins/standalone panels own all sides
        return frozenset(sides)

    def overlaps(a1, a2, b1, b2):
        return min(a2, b2) >= max(a1, b1)

    # Prefer grid cells over pixel geometry. A resize event can fire while Qt is
    # still assigning sibling geometries, but the layout positions are already
    # final; using them prevents an early full-frame cache from surviving the
    # settled zero-spacing layout.
    try:
        layout = parent.layout()
        own_index = layout.indexOf(widget)
        if own_index >= 0 and hasattr(layout, "getItemPosition"):
            row, column, row_span, column_span = layout.getItemPosition(
                own_index)
            for index in range(layout.count()):
                if index == own_index:
                    continue
                item = layout.itemAt(index)
                sibling = item.widget()
                if sibling is None or sibling.isHidden():
                    continue
                other_row, other_col, other_rows, other_cols = \
                    layout.getItemPosition(index)
                if (
                    other_col == column + column_span
                    and min(row + row_span, other_row + other_rows)
                    > max(row, other_row)
                ):
                    sides.discard("right")
                if (
                    other_row == row + row_span
                    and min(column + column_span, other_col + other_cols)
                    > max(column, other_col)
                ):
                    sides.discard("bottom")
            return frozenset(sides)
    except Exception:
        pass

    for sibling in siblings:
        if sibling is widget:
            continue
        try:
            if not sibling.isWidgetType() or sibling.isHidden():
                continue
            other = sibling.geometry()
        except Exception:
            continue
        if (
            other.left() == own.right() + 1
            and overlaps(own.top(), own.bottom(), other.top(), other.bottom())
        ):
            sides.discard("right")
        if (
            other.top() == own.bottom() + 1
            and overlaps(own.left(), own.right(), other.left(), other.right())
        ):
            sides.discard("bottom")
    return frozenset(sides)


def _matching_panel_stylesheet(widget, sheet, frame_color=None):
    """Return ``sheet`` with its plot border matched to the Skew-T palette."""
    import re

    lowered = sheet.lower() if sheet else ""
    if "border-width" not in lowered or "border-color" not in lowered:
        return sheet

    try:
        source = (
            frame_color
            if frame_color is not None
            else getattr(widget, "fg_color", colors.FG_COLOR)
        )
        matched_color = QtGui.QColor(source)
        if not matched_color.isValid():
            matched_color = QtGui.QColor(colors.FG_COLOR)
        color_name = matched_color.name()
    except Exception:  # noqa: BLE001 - keep a valid default on odd vendored data
        color_name = colors.FG_COLOR

    width_text = f"{PANEL_FRAME_WIDTH:g}px"
    frame_sides = getattr(widget, "_sharpmod_frame_sides", None)
    if frame_sides == "left":
        # A right-hand inset inside the shared bottom frame needs only the
        # separator on its left.  Keeping its top/right/bottom QSS edges would
        # paint directly beside the parent's outer edge and look two pixels
        # thick even though both individual strokes are one pixel.
        # The normalizer is called on every resize and theme apply. Remove its
        # previous side override first so the stylesheet remains idempotent.
        sheet = re.sub(
            r"\s*border-left-width\s*:\s*[^;}]+;?",
            "",
            sheet,
            flags=re.IGNORECASE,
        )
        width_declaration = (
            f"border-width: 0px; border-left-width: {width_text}"
        )
    else:
        width_declaration = f"border-width: {width_text}"
    updated = re.sub(
        r"border-width\s*:\s*[^;}]+",
        width_declaration,
        sheet,
        flags=re.IGNORECASE,
    )
    updated = re.sub(
        r"border-color\s*:\s*[^;}]+",
        f"border-color: {color_name}",
        updated,
        flags=re.IGNORECASE,
    )
    return updated


def _match_bottom_band_stylesheet(widget, frame_color=None):
    """Scope and normalize the shared lower-band frame stylesheet.

    Upstream assigns ``QWidget { ... border ... }`` to this container.  Qt
    applies that selector to every QWidget below it, so the nominal one-pixel
    parent outline becomes two adjacent rows at the top and bottom.  Restricting
    the selector to the named QFrame leaves one true outer outline; each child
    then draws only its intentional internal separator.
    """
    import re

    if widget is None:
        return
    try:
        widget.setObjectName(BOTTOM_BAND_OBJECT_NAME)
        sheet = widget.styleSheet()
    except Exception:  # noqa: BLE001 - not the expected QFrame surface
        return
    scoped = re.sub(
        r"\bQWidget\s*\{",
        f"QFrame#{BOTTOM_BAND_OBJECT_NAME} {{",
        sheet,
        count=1,
        flags=re.IGNORECASE,
    )
    updated = _matching_panel_stylesheet(widget, scoped, frame_color)
    if updated != sheet:
        widget.setStyleSheet(updated)


def _match_panel_stylesheet(widget, frame_color=None):
    """Make an existing QSS plot border match the widget's Skew-T palette."""
    try:
        sheet = widget.styleSheet()
    except Exception:  # noqa: BLE001 - not a stylesheet-backed widget
        return
    updated = _matching_panel_stylesheet(widget, sheet, frame_color)
    if updated != sheet:
        widget.setStyleSheet(updated)


def _install_matching_panel_frames():
    """Give every auxiliary plot the Skew-T's reduced foreground outline.

    Registered last so it wraps whatever ``draw_frame`` each class ends up with,
    including the ones other patches in this registry replace outright.

    Fully guarded and idempotent per class: a module that is absent, or a class
    without ``draw_frame``, is skipped rather than aborting the rest.
    """
    import importlib

    frame_classes = []
    for module_name, class_name in _FRAMED_INSETS:
        try:
            module = importlib.import_module(f"sharppy.viz.{module_name}")
            cls = getattr(module, class_name)
            frame_classes.append(cls)

            if not cls.__dict__.get("_sharpmod_matching_frame", False):
                original = cls.draw_frame
                pen_type = module.QtGui.QPen
                color_type = module.QtGui.QColor

                def draw_frame(
                    self,
                    qp,
                    _orig=original,
                    _pen=pen_type,
                    _color=color_type,
                ):
                    try:
                        bounds = (self.width(), self.height())
                    except Exception:  # noqa: BLE001 - no clamp without bounds
                        bounds = None
                    color = _color(getattr(
                        self, "fg_color", colors.FG_COLOR))
                    try:
                        _orig(
                            self,
                            _MatchingFramePainter(
                                qp,
                                _pen,
                                color,
                                bounds=bounds,
                                sides=_frame_sides_for_widget(self),
                            ),
                        )
                    except Exception:  # noqa: BLE001 - never lose a panel
                        _orig(self, qp)

                cls.draw_frame = draw_frame
                cls._sharpmod_matching_frame = True

            # Several table-like insets use QSS rather than four painter lines.
            # Rewrite the declaration *before* Qt applies it. Applying a second
            # stylesheet after ``initUI`` triggers a resize, whose vendored
            # handler calls ``initUI`` again and alternates forever between the
            # legacy and corrected declarations.
            if (
                hasattr(cls, "setStyleSheet")
                and not cls.__dict__.get("_sharpmod_matching_frame_qss", False)
            ):
                original_set_stylesheet = cls.setStyleSheet

                def setStyleSheet(
                    self,
                    sheet,
                    _orig=original_set_stylesheet,
                ):
                    return _orig(
                        self, _matching_panel_stylesheet(self, sheet))

                cls.setStyleSheet = setStyleSheet
                cls._sharpmod_matching_frame_qss = True

            # A colour-scheme reapply can change ``fg_color`` without calling
            # ``initUI``. Wrap concrete plot subclasses so the QSS follows it.
            for candidate in tuple(vars(module).values()):
                if not isinstance(candidate, type) or not issubclass(candidate, cls):
                    continue
                original_prefs = getattr(candidate, "setPreferences", None)
                if not callable(original_prefs) or candidate.__dict__.get(
                    "_sharpmod_matching_frame_prefs", False
                ):
                    continue

                def setPreferences(
                    self, *args, _orig=original_prefs, **kwargs
                ):
                    result = _orig(self, *args, **kwargs)
                    _match_panel_stylesheet(self)
                    return result

                candidate.setPreferences = setPreferences
                candidate._sharpmod_matching_frame_prefs = True
        except Exception:  # noqa: BLE001 - one inset must not end the pass
            continue

    # The combined bottom plot band is a plain QFrame owned by SPCWidget, not a
    # vendored inset class. Its legacy cyan QSS was the most visible colour
    # mismatch in the screenshot, so keep it synchronized on every theme apply.
    try:
        import sharppy.viz.SPCWindow as _spc_window

        spc_cls = _spc_window.SPCWidget
        if not spc_cls.__dict__.get("_sharpmod_matching_frame_config", False):
            original_update = spc_cls.updateConfig

            def updateConfig(self, *args, **kwargs):
                result = original_update(self, *args, **kwargs)
                _match_bottom_band_stylesheet(
                    getattr(self, "text", None),
                    getattr(self, "fg_color", colors.FG_COLOR),
                )
                for frame_cls in tuple(frame_classes):
                    try:
                        children = self.findChildren(frame_cls)
                    except Exception:
                        continue
                    for child in children:
                        _match_panel_stylesheet(child)
                return result

            spc_cls.updateConfig = updateConfig
            spc_cls._sharpmod_matching_frame_config = True
    except Exception:  # noqa: BLE001 - registry reports install failure
        raise
