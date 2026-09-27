"""Full Skew-T and hodograph comparison views built on the vendored renderers.

The simplified difference canvas in :mod:`sharpmod.ui.features.gui_visual_comparison` answers
"how far apart are these two profiles". It deliberately draws its own traces, and
it cannot answer "what does this sounding actually look like": it has no parcel
path, no wind barbs, no hodograph, and none of the annotation the operational
chart carries.

This module supplies that view without a second implementation of the science.
Each comparison slot hosts the same ``plotSkewT``/``plotHodo`` widget the main
window uses, fed the exact-valid-time profile the comparison already resolved, so
an on-screen curve and an exported curve come from one renderer rather than from
two drawing routines that can drift apart.

Three properties are load-bearing and are easy to break by accident:

* **Colour identity.** The vendored renderers consume ``background_colors``
  *positionally* while walking the collections they did not make active, and the
  stock palette holds three entries, so a fourth overlay silently reuses the
  first colour. Callers here hand each renderer a palette built in that same walk
  order, which is what keeps a colour attached to a profile instead of to a slot
  position.
* **Read-only use.** Editing, parcel selection, and the cursor menus stay in the
  main window. ``plotSkewT.resizeEvent`` rebuilds the plot but not its draggable
  coordinates (pinned by ``test_gui_edit_feedback``), so a resizable *editable*
  copy would hand back stale hit-targets. Nothing here enables dragging.
* **No nested density override.** ``render._target_density_pixmaps`` is a
  process-global ``QPixmap`` swap that refuses to nest. The export entry point
  below therefore never enters it; it renders through ``grab_widget_pixmap``,
  which allocates the native class and is safe both inside and outside an
  enclosing density context.
"""

from __future__ import annotations

from datetime import datetime
import math

from qtpy.QtCore import QPointF, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sharpmod.viz.colors import semantic_palette
from sharpmod.analysis.comparison import rendered_profile
from sharpmod.ui.features.gui_theme import current_theme, mono_font, ui_font
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE

#: Chart modes offered by the comparison workspace. ``CHART_DIFFERENCE`` keeps the
#: simplified per-slot difference canvas, which stays the better view for reading
#: small departures and is therefore retained rather than replaced.
CHART_DIFFERENCE = "difference"
CHART_SKEWT = "skewt"
CHART_HODOGRAPH = "hodograph"

CHART_MODES = (
    (CHART_DIFFERENCE, "Difference axes"),
    (CHART_SKEWT, "Skew-T"),
    (CHART_HODOGRAPH, "Hodograph"),
)

#: The modes backed by a vendored renderer. ``CHART_DIFFERENCE`` is served by the
#: simplified canvas in :mod:`sharpmod.ui.features.gui_visual_comparison`, not from here.
RENDERED_MODES = (CHART_SKEWT, CHART_HODOGRAPH)

#: How companion profiles are shown. Side by side gives every slot its own chart
#: with the reference drawn behind it; overlaid stacks every assigned profile onto
#: one chart. Both arrangements use the same colour identity and the same legend.
ARRANGEMENT_SIDE_BY_SIDE = "side_by_side"
ARRANGEMENT_OVERLAID = "overlaid"

ARRANGEMENTS = (
    (ARRANGEMENT_SIDE_BY_SIDE, "Side by side"),
    (ARRANGEMENT_OVERLAID, "Overlaid"),
)

#: Semantic roles used for per-profile identity, in slot order. Eight entries for
#: at most four slots, so the palette can never wrap onto itself the way the
#: vendored three-colour default does.
_SLOT_ROLES = (
    "blue",
    "orange",
    "magenta",
    "cyan",
    "yellow",
    "green",
    "corfidi",
    "amber_l2",
)

#: The reference profile reads as context rather than as another candidate, so it
#: gets one deliberately neutral colour that is never issued to a slot.
_REFERENCE_ROLE = "marker_gray"

#: Enough for a vendored chart to stay legible in a four-slot grid without pushing
#: the compare dock past the ceiling the tab bar already constrains. Detail beyond
#: this comes from two-panel mode or from an export at a chosen size.
_CHART_MINIMUM = (260, 220)

_MODE_NAMES = dict(CHART_MODES)

_RENDERER_CLASSES = None
_RENDERER_ERROR = None
_SHARPTAB = None


def _renderer_classes():
    """Import the vendored renderers once, remembering any failure.

    Imported lazily so a broken or absent SHARPpy degrades this one view into an
    explanatory message instead of preventing the analysis workspace from loading.
    """
    global _RENDERER_CLASSES, _RENDERER_ERROR
    if _RENDERER_CLASSES is None and _RENDERER_ERROR is None:
        try:
            from sharppy.viz.hodo import plotHodo
            from sharppy.viz.skew import plotSkewT
        except Exception as exc:  # noqa: BLE001 - reported, never raised onward
            _RENDERER_ERROR = str(exc) or exc.__class__.__name__
        else:
            _RENDERER_CLASSES = {
                CHART_SKEWT: plotSkewT,
                CHART_HODOGRAPH: plotHodo,
            }
    return _RENDERER_CLASSES, _RENDERER_ERROR


def _sharptab():
    """Return the established interpolation helpers, or ``None``."""
    global _SHARPTAB
    if _SHARPTAB is None:
        try:
            import sharppy.sharptab as tab
        except Exception:  # noqa: BLE001 - readout degrades, chart still draws
            _SHARPTAB = False
        else:
            _SHARPTAB = tab
    return _SHARPTAB or None


def _colour_text(value, fallback_role):
    """Return one hex colour string from a QColor, a name, or a fallback role."""
    try:
        colour = QColor(value)
    except (TypeError, ValueError):
        colour = None
    if colour is None or not colour.isValid():
        theme = current_theme()
        palette = semantic_palette(theme.surface_sunken, theme.text_primary)
        colour = QColor(palette[fallback_role])
    return colour.name()


def slot_colours(theme=None):
    """Return the identity-stable slot colours, resolved for the live theme."""
    theme = theme or current_theme()
    palette = semantic_palette(theme.surface_sunken, theme.text_primary)
    return tuple(palette[role] for role in _SLOT_ROLES)


def reference_colour(theme=None):
    """Return the single colour reserved for the reference profile."""
    theme = theme or current_theme()
    palette = semantic_palette(theme.surface_sunken, theme.text_primary)
    return palette[_REFERENCE_ROLE]


def slot_colour(slot_number, theme=None):
    """Return one slot's colour, keyed by slot number rather than draw order."""
    colours = slot_colours(theme)
    index = max(1, int(slot_number)) - 1
    return colours[index % len(colours)]


def overlay_palette(sources, active_index, colours):
    """Order ``colours`` to match the renderer's own background walk.

    ``plotSkewT.plotData`` and ``plotHodo.plotData`` both iterate their
    collections in index order, skip the active one, and take the next entry of
    ``background_colors`` for each survivor -- and ``drawTitles`` repeats that
    walk to colour the stacked titles. Building the palette in exactly that order
    is what makes a colour mean "this profile" in the chart, in the titles, and in
    the legend at once, instead of meaning "the third thing drawn".
    """
    return [
        colours[index]
        for index in range(len(sources))
        if index != int(active_index)
    ]


class ChartProfileSource:
    """The minimal profile collection the vendored renderers accept.

    ``plotSkewT``/``plotHodo`` read a *collection* and pick the drawn profile with
    ``getHighlightedProf()``. Wrapping the single exact-valid-time profile the
    comparison already resolved guarantees the drawn curve is the same data the
    difference numbers came from, rather than whatever index the live collection
    happens to be sitting on when the chart repaints.

    ``getCurrentProfs`` deliberately returns an empty mapping: that pass exists to
    draw unhighlighted ensemble members, and returning the one profile there would
    make the renderer draw it twice, once in the ensemble colour.
    """

    def __init__(
        self,
        profile,
        label,
        valid_time,
        *,
        meta=None,
        modified=False,
        member_name="",
    ):
        self._profile = profile
        self._label = str(label or "")
        self._valid_time = valid_time
        self._meta = dict(meta or {})
        self._modified = bool(modified)
        self._member_name = str(member_name or "")

    # -- what setActiveCollection and plotData read ------------------------- #

    def getHighlightedProf(self):  # noqa: N802 - vendored spelling
        return self._profile

    def getCurrentProfs(self):  # noqa: N802 - vendored spelling
        return {}

    def getCurrentDate(self):  # noqa: N802 - vendored spelling
        return self._valid_time

    def getAnalogDate(self):  # noqa: N802 - vendored spelling
        return self._valid_time

    def getHighlightedMemberName(self):  # noqa: N802 - vendored spelling
        return self._member_name

    def isModified(self):  # noqa: N802 - vendored spelling
        return self._modified

    def isInterpolated(self):  # noqa: N802 - vendored spelling
        return False

    def isEnsemble(self):  # noqa: N802 - vendored spelling
        return bool(self._member_name)

    def setMeta(self, key, value):  # noqa: N802 - vendored spelling
        self._meta[str(key)] = value

    def getMeta(self, key, index=False):  # noqa: N802 - vendored spelling
        """Return metadata with types the vendored title formatter can use.

        The stock ``getPlotTitle`` calls ``.strftime`` on ``run`` and concatenates
        ``model`` as a string without guarding either, so a missing value there is
        a crash rather than a blank. Falling back to the valid time and to an empty
        string keeps the title honest and keeps the chart drawable.
        """
        key = str(key)
        value = self._meta.get(key)
        if key in {"run", "base_time"}:
            return value if isinstance(value, datetime) else self._valid_time
        if key == "loc":
            return str(value) if value else (self._label or "Profile")
        if key == "model":
            return str(value or "")
        if key == "observed":
            return bool(value)
        return value

    # -- what this module itself needs -------------------------------------- #

    @property
    def label(self):
        return self._label

    @property
    def profile(self):
        return self._profile


def source_for_panel(panel, valid_time):
    """Adapt one comparison panel into a renderer-ready collection.

    Returns ``None`` when the slot has no profile at the reference valid time, so
    an unavailable slot renders its stated reason instead of an empty chart that
    looks like real data.
    """
    if panel is None or getattr(panel, "profile", None) is None:
        return None
    collection = getattr(panel, "collection", None)
    meta = {}
    for key in ("loc", "run", "model", "observed", "base_time", "lat", "lon"):
        meta[key] = _collection_meta(collection, key)
    profile = panel.profile
    if collection is not None:
        # The panel's profile came straight out of the collection's store, which
        # holds plain ``Profile`` objects with no ``vtmp``/``wetbulb``/``srwind``.
        # Ask for the same upgrade the collection performs for its own highlighted
        # profile, at the reference index rather than the collection's current one.
        # It is a copy of the same level data, so the drawn curve is still the
        # curve the differences were measured from.
        upgraded = rendered_profile(collection, valid_time)
        if upgraded is not None:
            profile = upgraded
    return ChartProfileSource(
        profile,
        getattr(panel, "label", ""),
        valid_time if valid_time is not None else _collection_date(collection),
        meta=meta,
        modified=bool(getattr(panel, "modified", False)),
    )


def _collection_meta(collection, key):
    if collection is None:
        return None
    try:
        return collection.getMeta(key)
    except (AttributeError, KeyError, TypeError, ValueError):
        return getattr(collection, "_meta", {}).get(key)


def _collection_date(collection):
    if collection is None:
        return None
    try:
        return collection.getCurrentDate()
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


class _Swatch(QWidget):
    """A painted colour chip, so the legend survives any chrome stylesheet."""

    def __init__(self, colour, parent=None):
        super().__init__(parent)
        self._colour = QColor(colour)
        self.setFixedSize(12, 12)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_colour(self, colour):
        self._colour = QColor(colour)
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.setPen(QPen(QColor(current_theme().border), 1.0))
        qp.setBrush(self._colour)
        qp.drawRoundedRect(QRectF(0.5, 0.5, 11.0, 11.0), 2.0, 2.0)
        qp.end()


class _ElidedLabel(QLabel):
    """A label that shortens with an ellipsis instead of being cut off.

    A slot heading has to keep saying which slot it is and whether it is the
    reference. A plain ``QLabel`` in a narrow slot simply loses its tail, so
    "Slot 1: KOUN . HRRR . 25 Jun 0600Z . reference" arrived as
    "... . referen" with no sign that anything was missing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""

    def set_full_text(self, text):
        self._full = str(text or "")
        self.setToolTip(self._full)
        self.setAccessibleName(self._full)
        self.setText(self._full)
        self.update()

    def full_text(self):
        return self._full

    def paintEvent(self, _event):  # noqa: N802
        qp = QPainter(self)
        qp.setFont(self.font())
        metrics = qp.fontMetrics()
        qp.setPen(QColor(current_theme().text_primary))
        qp.drawText(
            self.rect(),
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(self._full, Qt.ElideRight, self.width()),
        )
        qp.end()


class _LegendStrip(QWidget):
    """A one-line legend that keeps its colour chips even when text elides.

    A word-wrapped rich-text label was tried first and failed in the real
    200%-text capture: it grew to three lines, took the height the chart needed,
    and painted over the next slot's heading. This paints one bounded line, so it
    can never grow into a neighbour, and it draws the chips first, so the
    colour-to-profile mapping survives even when the names are shortened. The
    untruncated text stays on the tooltip and the accessible name.
    """

    _CHIP = 9.0
    _GAP = 4.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = ()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def set_entries(self, entries):
        self._entries = tuple(
            (tuple(colours), str(text or "")) for colours, text in entries
        )
        plain = self.plain_text()
        self.setToolTip(plain)
        self.setAccessibleName(plain)
        self.updateGeometry()
        self.update()

    def entries(self):
        return self._entries

    def chip_colours(self):
        """Return every chip colour, in order, for focused UI tests."""
        return [
            QColor(colour).name()
            for colours, _text in self._entries
            for colour in colours
        ]

    def plain_text(self):
        return " · ".join(text for _colours, text in self._entries)

    def sizeHint(self):  # noqa: N802
        metrics = self.fontMetrics()
        return QSize(120, metrics.height() + 4)

    def minimumSizeHint(self):  # noqa: N802
        metrics = self.fontMetrics()
        return QSize(40, metrics.height() + 4)

    def paintEvent(self, _event):  # noqa: N802
        if not self._entries:
            return
        theme = current_theme()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.setFont(self.font())
        metrics = qp.fontMetrics()
        baseline_top = (self.height() - metrics.height()) / 2.0
        chip_top = (self.height() - self._CHIP) / 2.0
        x = 0.0
        limit = float(self.width())
        for index, (colours, text) in enumerate(self._entries):
            if index:
                separator = " · "
                width = metrics.horizontalAdvance(separator)
                if x + width >= limit:
                    break
                qp.setPen(QColor(theme.text_tertiary))
                qp.drawText(
                    QRectF(x, baseline_top, width, metrics.height()),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    separator,
                )
                x += width
            for colour in colours:
                if x + self._CHIP >= limit:
                    break
                qp.setPen(QPen(QColor(theme.border), 1.0))
                qp.setBrush(QColor(colour))
                qp.drawRect(QRectF(x, chip_top, self._CHIP, self._CHIP))
                x += self._CHIP + 1.0
            x += self._GAP - 1.0
            remaining = max(0.0, limit - x)
            if remaining <= 0.0:
                break
            qp.setPen(QColor(theme.text_tertiary))
            qp.setBrush(Qt.NoBrush)
            qp.drawText(
                QRectF(x, baseline_top, remaining, metrics.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                metrics.elidedText(text, Qt.ElideRight, int(remaining)),
            )
            x += min(remaining, metrics.horizontalAdvance(text))
        qp.end()


class _CursorOverlay(QWidget):
    """A transparent sheet that draws the shared height cursor over a chart.

    The renderer blits its own cached bitmap in ``paintEvent``, so a line painted
    by the parent would be hidden underneath it. An overlay keeps the cursor out
    of the renderer's cache, which means moving the cursor never invalidates the
    rendered chart.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self._y = None
        self._caption = ""

    def set_line(self, y, caption=""):
        if y is None and self._y is None and not self._caption:
            return
        self._y = y
        self._caption = str(caption or "")
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        if self._y is None:
            return
        theme = current_theme()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(theme.accent), 1.2, Qt.DashLine)
        qp.setPen(pen)
        y = float(self._y)
        qp.drawLine(QPointF(0.0, y), QPointF(float(self.width()), y))
        if self._caption:
            qp.setFont(mono_font("small"))
            metrics = qp.fontMetrics()
            width = metrics.horizontalAdvance(self._caption) + 8
            height = metrics.height() + 2
            top = min(max(0.0, y + 2.0), max(0.0, self.height() - height))
            box = QRectF(2.0, top, width, height)
            backdrop = QColor(theme.surface_sunken)
            backdrop.setAlpha(210)
            qp.setPen(Qt.NoPen)
            qp.setBrush(backdrop)
            qp.drawRoundedRect(box, 2.0, 2.0)
            qp.setPen(QColor(theme.accent))
            qp.drawText(box, Qt.AlignCenter, self._caption)
        qp.end()




class ComparisonChartGrid(QWidget):
    """The two- or four-slot grid of established scientific charts."""

    cursorHeightChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisComparisonChartGrid")
        self._mode = CHART_SKEWT
        self._arrangement = ARRANGEMENT_SIDE_BY_SIDE
        self._linked = True
        self._panels = ()
        self._layout_count = 2
        self._valid_time = None
        self._reference_index = 0
        self._views = []
        self._active_count = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACE["xs"])
        self._grid.setVerticalSpacing(SPACE["xs"])
        self.setAccessibleName("Scientific chart comparison")
        self._sync_description()

    # -- configuration ------------------------------------------------------ #

    def mode(self):
        return self._mode

    def set_mode(self, mode):
        if mode == self._mode or mode not in RENDERED_MODES:
            return
        self._mode = mode
        for view in self._views:
            view.set_mode(mode)
        self._rebuild()

    def arrangement(self):
        return self._arrangement

    def set_arrangement(self, arrangement):
        if arrangement == self._arrangement:
            return
        self._arrangement = (
            ARRANGEMENT_OVERLAID
            if arrangement == ARRANGEMENT_OVERLAID
            else ARRANGEMENT_SIDE_BY_SIDE
        )
        self._rebuild()

    def linked(self):
        """Whether every slot is held on one shared pan/zoom."""
        return self._linked

    def set_linked(self, linked):
        self._linked = bool(linked)
        self._sync_description()
        if self._linked:
            self._propagate_axes()

    def views(self):
        """Return the per-slot chart views, for focused UI tests."""
        return tuple(self._views)

    # -- data --------------------------------------------------------------- #

    def set_panels(self, panels, layout_count, *, valid_time=None, reference_index=0):
        self._panels = tuple(panels)
        self._layout_count = 2 if int(layout_count) != 4 else 4
        self._valid_time = valid_time
        self._reference_index = int(reference_index)
        self._rebuild()

    def _visible_panels(self):
        return self._panels[: self._layout_count]

    def _rebuild(self):
        panels = self._visible_panels()
        colours = slot_colours()
        sources = [source_for_panel(panel, self._valid_time) for panel in panels]
        reference_source = None
        for panel, source in zip(panels, sources):
            if getattr(panel, "reference", False) and source is not None:
                reference_source = source
                break

        wanted = 1 if self._arrangement == ARRANGEMENT_OVERLAID else len(panels)
        self._resize_views(max(1, wanted))

        if self._arrangement == ARRANGEMENT_OVERLAID:
            self._load_overlaid(panels, sources, colours)
        else:
            self._load_side_by_side(panels, sources, colours, reference_source)
        self._balance_rows()
        if self._linked:
            self._propagate_axes()
        self._sync_description()

    def _balance_rows(self):
        """Give the height to the rows that actually hold a chart.

        With equal row stretch, a two-sounding comparison in four-panel mode spent
        half its height -- and half of every exported image -- on two rows carrying
        one sentence each.
        """
        # The active views, not the ones Qt has realized. ``isVisible`` is false
        # for every child until the whole ancestor chain is shown, so reading it
        # here made the balance a no-op whenever panels arrived before the tab was
        # first displayed -- which is the normal order in the interactive workspace.
        active = self._views[: max(1, self._active_count)]
        columns = 1 if len(active) == 1 else 2
        rows = {}
        for index, view in enumerate(active):
            row = index // columns
            rows[row] = rows.get(row, False) or view.has_chart()
        for row in range(2):
            self._grid.setRowStretch(row, 1 if rows.get(row) else 0)

    def _load_overlaid(self, panels, sources, colours):
        """One chart carrying every assigned profile, reference drawn as context."""
        view = self._views[0]
        present = [
            (panel, source)
            for panel, source in zip(panels, sources)
            if source is not None
        ]
        if not present:
            view.set_panel(None, (), 0, colours)
            return
        active = 0
        for index, (panel, _source) in enumerate(present):
            if getattr(panel, "reference", False):
                active = index
                break
        ordered = [source for _panel, source in present]
        # Colour by slot number so a profile keeps its colour when the slot count
        # changes or another slot becomes the reference.
        palette = [
            slot_colour(getattr(panel, "slot_number", index + 1))
            for index, (panel, _source) in enumerate(present)
        ]
        palette[active] = reference_colour()
        view.set_panel(
            present[active][0],
            ordered,
            active,
            palette,
            arrangement=ARRANGEMENT_OVERLAID,
        )

    def _load_side_by_side(self, panels, sources, colours, reference_source):
        for index, view in enumerate(self._views):
            if index >= len(panels):
                view.set_panel(None, (), 0, colours)
                continue
            panel = panels[index]
            source = sources[index]
            if source is None:
                view.set_panel(panel, (), 0, colours)
                continue
            ordered = [source]
            palette = [slot_colour(getattr(panel, "slot_number", index + 1))]
            if reference_source is not None and reference_source is not source:
                # The reference behind every candidate is what makes side-by-side
                # panels comparable without the reader holding a shape in memory.
                ordered.append(reference_source)
                palette.append(reference_colour())
            view.set_panel(
                panel, ordered, 0, palette, arrangement=ARRANGEMENT_SIDE_BY_SIDE
            )

    def _resize_views(self, count):
        self._active_count = int(count)
        while len(self._views) < count:
            view = ComparisonChartView(self._mode, self)
            self._views.append(view)
        for index, view in enumerate(self._views):
            self._grid.removeWidget(view)
            if index < count:
                columns = 1 if count == 1 else 2
                self._grid.addWidget(view, index // columns, index % columns)
                view.setVisible(True)
            else:
                view.setVisible(False)
        for column in range(2):
            self._grid.setColumnStretch(column, 1)
        for row in range(2):
            self._grid.setRowStretch(row, 1)

    # -- synchronization ---------------------------------------------------- #

    def set_cursor_height(self, msl_m):
        """Move the shared cursor in every slot at once."""
        for view in self._views:
            view.set_cursor_height(msl_m)

    def _propagate_axes(self):
        """Hold every slot on the first slot's pan/zoom while linked."""
        if not self._views:
            return
        state = None
        for view in self._views:
            state = view.axis_state()
            if state is not None:
                break
        if state is None:
            return
        for view in self._views[1:]:
            view.apply_axis_state(state)

    def _sync_description(self):
        mode = _MODE_NAMES.get(self._mode, self._mode)
        arrangement = dict(ARRANGEMENTS).get(self._arrangement, self._arrangement)
        axes = (
            "Every slot shares one vertical scale and one shared height cursor."
            if self._linked
            else "Each slot keeps its own independent scale; positions are not "
            "comparable between slots."
        )
        self.setAccessibleDescription(
            f"{mode} comparison, {arrangement.lower()}. {axes} "
            "Charts are drawn by the same renderer the main sounding window uses, "
            "from the profile at the exact reference valid time."
        )


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def render_comparison_pixmap(
    panels,
    *,
    layout_count=2,
    mode=CHART_SKEWT,
    arrangement=ARRANGEMENT_SIDE_BY_SIDE,
    width=1200,
    height=800,
    valid_time=None,
    reference_index=0,
    cursor_height=None,
    scale=1.0,
):
    """Render the comparison charts at a caller-chosen size, offscreen.

    The point of the explicit ``width``/``height`` is that an export must not have
    to borrow whatever size the interactive splitter happens to be at, and must
    not resize the live widget to get a bigger picture. A detached grid is built,
    sized, drawn, and thrown away, so the on-screen view is untouched.

    No calculation is repeated: the panels handed in already carry the resolved
    profiles and differences, and the drawing is done by the same vendored
    renderers the window uses.

    ``scale`` multiplies physical pixels through ``render.grab_widget_pixmap``,
    which allocates the native pixmap class. This function deliberately does not
    enter ``render._target_density_pixmaps``, because that override is
    process-global and refuses to nest, so an export triggered from inside one
    would otherwise fail.
    """
    from qtpy.QtWidgets import QApplication

    from sharpmod.rendering import cli as render

    if QApplication.instance() is None:
        raise RuntimeError("rendering a comparison chart needs a QApplication")

    grid = ComparisonChartGrid()
    try:
        grid.setAttribute(Qt.WA_DontShowOnScreen, True)
        grid.set_mode(mode if mode in RENDERED_MODES else CHART_SKEWT)
        grid.set_arrangement(arrangement)
        grid.resize(int(width), int(height))
        grid.show()
        grid.set_panels(
            panels,
            layout_count,
            valid_time=valid_time,
            reference_index=reference_index,
        )
        # Two passes: the first gives the layout its real geometry, the second
        # lets each renderer re-plot into a bitmap that matches that geometry.
        for _pass in range(2):
            layout = grid.layout()
            if layout is not None:
                layout.activate()
            QApplication.processEvents()
        if cursor_height is not None:
            grid.set_cursor_height(cursor_height)
            QApplication.processEvents()
        return render.grab_widget_pixmap(grid, float(scale))
    finally:
        grid.hide()
        grid.setParent(None)
        grid.deleteLater()


def comparison_png(panels, **kwargs):
    """Return the comparison charts as PNG bytes at a chosen size."""
    from qtpy.QtCore import QBuffer, QByteArray, QIODevice

    pixmap = render_comparison_pixmap(panels, **kwargs)
    if pixmap is None or pixmap.isNull():
        raise RuntimeError("the comparison chart produced an empty image")
    # The QByteArray has to be held in a local: QBuffer keeps a bare pointer to
    # it, so passing a temporary here is an access violation rather than an
    # exception. This is the same shape as gui_animation.pixmap_png.
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.WriteOnly) or not pixmap.save(buffer, "PNG"):
        raise RuntimeError("the comparison chart could not be encoded as PNG")
    buffer.close()
    return bytes(data)


__all__ = [
    "ARRANGEMENTS",
    "ARRANGEMENT_OVERLAID",
    "ARRANGEMENT_SIDE_BY_SIDE",
    "CHART_DIFFERENCE",
    "CHART_HODOGRAPH",
    "CHART_MODES",
    "CHART_SKEWT",
    "RENDERED_MODES",
    "ChartProfileSource",
    "ComparisonChartGrid",
    "ComparisonChartView",
    "comparison_png",
    "overlay_palette",
    "reference_colour",
    "render_comparison_pixmap",
    "slot_colour",
    "slot_colours",
    "source_for_panel",
]


from sharpmod.ui.comparison_chart_view import ComparisonChartView  # noqa: E402
