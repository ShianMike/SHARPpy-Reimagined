"""Side-by-side HRRR field panels on one synchronized view.

Reading a forecast is a comparison: reflectivity means one thing over 3000 J/kg
of CAPE and another over 300, and shear only matters where the instability is.
A single map shows one field at a time -- all 23 products share the ``hrrr_field``
overlay key precisely so two opaque colour ramps never stack -- so comparing two
of them meant switching back and forth and holding the first in your head.

This view puts two or four fields side by side instead, each on its own map with
its own selector, and keeps every view locked together so a feature is at the
same screen position in all of them. Panning one pans all; that is the whole
point, because a comparison between panels that are not looking at the same place
is not a comparison.

Why separate maps rather than one widget painting N panels
---------------------------------------------------------
:mod:`sharpmod.ui.features.gui_visual_comparison` does the latter for soundings: one canvas
paints N rectangles and shares one set of axis ranges. That works there because
the panels are inert drawings. Here each panel needs the real map -- its own
basemap cache, projection, raster overlay slot, pan and wheel handling -- and one
:class:`~sharpmod.ui.maps.overlays.controllers.HrrrFieldController` of its own, since a
controller owns exactly one map's single field slot. So these are real sibling
widgets, and the synchronization has to be wired rather than implied.

The feedback loop that shape creates
------------------------------------
``StationMapWidget.restore_view_bounds`` emits ``viewSettled`` *synchronously*,
inside the same call. Fanning a settled view out to the siblings therefore makes
every sibling announce a settled view of its own, straight back into the handler
that is still running -- unbounded recursion on the first pan. :class:`MapPanelsView`
holds a re-entrancy flag for exactly this, and it is the one piece of this module
that is not optional.

A view, not a window
--------------------
This is a plain ``QWidget`` so the picker can host it as a source tab. It was a
separate ``QMainWindow`` first, which was worse in a way that only showed up in
use: a floating window inherits a run and forecast hour from whatever the picker
happened to be showing and has no controls of its own to correct it, so every
panel sat on "not available for that cycle" with nothing on screen to fix. Hosted
as a tab, the run controls sit in the same rail as the panels and the fields
follow them.
"""

from __future__ import annotations

import logging
import weakref

from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.gui_maps import PointMapWidget
from sharpmod.ui.maps.crosshair_overlay import CrosshairOverlay
from sharpmod.ui.maps.overlays.controllers import HrrrFieldController
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    OBJ_CARD,
    OBJ_HINT,
    OBJ_PLAIN,
    SPACE,
)

_LOGGER = logging.getLogger("sharpmod.gui")

#: Panel counts offered, in the order the chooser lists them. Mirrors
#: ``VisualComparisonWidget``'s two/four choice so the two comparison views in the
#: application agree about what a layout is.
PANEL_COUNTS = (2, 4)

#: The most panels this view will ever build. Every panel is constructed once and
#: then shown or hidden, rather than created and destroyed as the count changes:
#: a controller owns timers and a worker fleet, and churning those on a combo box
#: change is how a fetch ends up orphaned against a dead map.
MAX_PANELS = max(PANEL_COUNTS)

#: What each panel starts on. Deliberately four *different* fields following the
#: ingredients a forecaster actually composes -- what is happening, how unstable
#: it is, how sheared, and what the composite makes of the two -- because four
#: copies of composite reflectivity would demonstrate the layout and none of the
#: point of it.
DEFAULT_PANEL_PRODUCTS = (
    "refc",
    "mlcape",
    "shear-0-6km",
    "stp",
)


#: Per-category remembered panel field (T24.2): ``{category: product}``.
#: A forecaster who compares reflectivity against shear should not have to
#: re-pick both every time they try another instability field. Remembering
#: by category keeps the last choice where it was useful without promoting
#: one panel's pick onto another panel's category.
PANEL_CATEGORY_MEMORY_VERSION = 1

#: Named field-panel presets (T24.4): label plus one product per panel.
#: Keys are short slugs; labels never imply an official forecast or warning.
PANEL_PRESETS: dict[str, dict] = {
    "storm-environment": {
        "label": "Storm environment",
        "products": ("refc", "mlcape", "shear-0-6km", "stp"),
        "why": "What is happening, how unstable, how sheared, what the composite makes of the two.",
    },
    "tornado-ingredients": {
        "label": "Tornado ingredients",
        "products": ("refc", "sbcape", "srh-0-1km", "stp"),
        "why": "Reflectivity with surface instability, low-level rotation, and the tornado composite.",
    },
    "hail-ingredients": {
        "label": "Hail ingredients",
        "products": ("refc", "mucape", "shear-0-6km", "lapse-700-500"),
        "why": "Reflectivity with deep instability, deep shear, and mid-level lapse rates.",
    },
    "wind-ingredients": {
        "label": "Damaging-wind ingredients",
        "products": ("refc", "mlcape", "lapse-0-3km", "shear-0-1km"),
        "why": "Reflectivity with instability, low-level lapse rates, and low-level shear.",
    },
    "winter-context": {
        "label": "Surface context",
        "products": ("tmp-2m", "dpt-2m", "hgt-wind-850", "hgt-wind-500"),
        "why": "Temperature, moisture, and low/mid-level flow beside the fields.",
    },
}

#: The default panel preset key.
DEFAULT_PANEL_PRESET_KEY = "storm-environment"


def migrate_panel_preset_choice(stored) -> str:
    """Resolve a stored panel-preset name to a known key (T24.4)."""
    wanted = " ".join(str(stored or "").split()).casefold()
    for key, entry in PANEL_PRESETS.items():
        if key.casefold() == wanted or \
                str(entry.get("label", "")).casefold() == wanted:
            return key
    return DEFAULT_PANEL_PRESET_KEY


def preset_panel_products(key: str) -> tuple[str, ...]:
    """Return the four panel products for a preset key (T24.4)."""
    entry = PANEL_PRESETS.get(migrate_panel_preset_choice(key))
    if entry is None:
        entry = PANEL_PRESETS[DEFAULT_PANEL_PRESET_KEY]
    return tuple(str(item) for item in entry["products"])


def normalise_panel_category_memory(payload) -> dict:
    """Return a validated ``{category: product}`` memory (T24.2)."""
    if not isinstance(payload, dict):
        return {}
    try:
        from sharpmod.providers import hrrr_products
    except ImportError:  # pragma: no cover - catalogue is local
        return {}
    memory: dict[str, str] = {}
    for category, product in payload.items():
        try:
            spec = hrrr_products.get_product(product)
        except Exception:  # noqa: BLE001 - memory never breaks panels
            continue
        if str(getattr(spec, "key", "")) != str(product):
            continue
        category_name = str(category or "").strip()
        if not category_name:
            continue
        if spec.category != category_name:
            continue
        memory[category_name] = str(product)
    return memory


def panel_state(*, count: int = PANEL_COUNTS[0], products=None,
                preset: str = "", category_memory=None,
                maximised: int | None = None) -> dict:
    """Return the portable field-panel arrangement snapshot (T24/session)."""
    try:
        wanted = int(count)
    except (TypeError, ValueError, OverflowError):
        wanted = PANEL_COUNTS[0]
    if wanted not in PANEL_COUNTS:
        wanted = PANEL_COUNTS[0]
    resolved = resolve_panel_products(products)
    try:
        maximised_index = None if maximised is None else int(maximised)
    except (TypeError, ValueError, OverflowError):
        maximised_index = None
    if maximised_index is not None and not 0 <= maximised_index < MAX_PANELS:
        maximised_index = None
    return {
        "version": 1,
        "count": int(wanted),
        "products": list(resolved),
        "preset": migrate_panel_preset_choice(preset) if preset else "",
        "category_memory": normalise_panel_category_memory(category_memory),
        "maximised": maximised_index,
    }


def restore_panel_state(payload) -> dict:
    """Return a validated panel-arrangement snapshot, tolerating old ones."""
    if not isinstance(payload, dict):
        return panel_state()
    try:
        version = int(payload.get("version", 1))
    except (TypeError, ValueError, OverflowError):
        version = 1
    if version != 1:
        return panel_state()
    return panel_state(count=payload.get("count"),
                       products=payload.get("products"),
                       preset=payload.get("preset", ""),
                       category_memory=payload.get("category_memory"),
                       maximised=payload.get("maximised"))


def resolve_panel_products(products=None) -> tuple[str, ...]:
    """Return one field key per panel, filling every gap with the default.

    Lenient on purpose, because its only caller is a restore. A remembered
    arrangement is written by whatever build was installed at the time, so a
    truncated list, a blank entry, a list longer than :data:`MAX_PANELS`, or no
    preference at all has to degrade to a working set of panels rather than stop
    the tab building. Keys are *not* checked against the catalogue here:
    ``HrrrFieldController`` resolves an unknown one to the default already, so
    validating it twice would only put the fallback in two places.
    """
    supplied = tuple(products or ())
    resolved: list[str] = []
    for index in range(MAX_PANELS):
        default = DEFAULT_PANEL_PRODUCTS[index % len(DEFAULT_PANEL_PRODUCTS)]
        key = str(supplied[index] or "").strip() if index < len(supplied) else ""
        resolved.append(key or default)
    return tuple(resolved)




class _FieldSelector(QWidget):
    """Category, then field -- the same two-step choice the overlay rail uses.

    One flat list was tried first and is what this replaces. Every entry had to
    carry its group to be unambiguous, so all 23 rows read ``Wind Shear · Bulk
    Shear: 0-6 km AGL``: the widest part of each row was the part shared with its
    neighbours, and the popup was taller than the window it opened over, which put
    a panel's field list on top of the panels it was there to be compared against.
    Splitting the choice in two makes the group a heading rather than a prefix
    repeated 23 times, and no list is longer than seven rows.
    """

    #: Emitted only for a field the *user* chose, carrying the resolved key.
    productChanged = Signal(str)

    def __init__(self, product: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_PLAIN)
        # Imported here, not at module scope: the catalogue pulls in NumPy, and
        # the picker's import path is kept clear of it so opening the application
        # does not pay for a field nobody has asked for yet.
        from sharpmod.providers import hrrr_products

        self._catalogue = hrrr_products
        wanted = hrrr_products.get_product(product)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["xs"])

        self._category = QComboBox(self)
        self._category.setAccessibleName("Field group")
        self._category.setToolTip("Which group of fields to choose from")
        self._category.setMinimumHeight(CONTROL_H["sm"])
        for name, _members in hrrr_products.products_by_category():
            self._category.addItem(name, name)
        row.addWidget(self._category)

        self._product = QComboBox(self)
        self._product.setAccessibleName("Field")
        self._product.setToolTip("The field this panel draws")
        self._product.setMinimumHeight(CONTROL_H["sm"])
        self._product.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self._product, 1)

        # Populate before connecting, so building the initial list cannot look
        # like a user selection.
        self._category.setCurrentIndex(
            max(0, self._category.findData(wanted.category))
        )
        self._category_memory: dict[str, str] = {}
        self._reload(wanted.key)
        self._category.currentIndexChanged.connect(self._on_category)
        self._product.currentIndexChanged.connect(self._on_product)
        from sharpmod.ui.features.gui_selectors import (
            configure_combo_search, product_choices,
        )

        self._product._sharpmod_search_catalog = (
            product_choices,
            weakref.WeakMethod(self._choose_search_product),
            "products",
        )
        self._search = configure_combo_search(self._product)

    def open_search(self) -> None:
        """Open the full field catalogue from the panel actions menu."""
        self._search.open()

    # -- public API ---------------------------------------------------------- #
    def product(self) -> str:
        return str(self._product.currentData() or self._catalogue.DEFAULT_PRODUCT)

    def _choose_search_product(self, key: str) -> None:
        if key not in self._catalogue.PRODUCTS:
            return
        previous = self.product()
        self.set_product(key)
        if self.product() != previous:
            self._on_product(0)

    def set_product(self, key: str, *, remember_category: bool = False,
                    memory: dict | None = None) -> None:
        """Move to ``key`` without it looking like a user choice.

        With ``remember_category`` the panel answers a category switch with
        its remembered product for that category (T24.2): the category box
        still drives which group is listed, but the field box lands on the
        last product used in that group rather than always its first row.
        """
        spec = self._catalogue.get_product(key)
        if remember_category and isinstance(memory, dict):
            wanted = str(memory.get(spec.category) or "").strip()
            if wanted:
                candidate = self._catalogue.get_product(wanted)
                if str(getattr(candidate, "key", "")) == wanted and \
                        candidate.category == spec.category:
                    spec = candidate
        blocked = self._category.blockSignals(True)
        try:
            index = self._category.findData(spec.category)
            if index >= 0:
                self._category.setCurrentIndex(index)
        finally:
            self._category.blockSignals(blocked)
        self._reload(spec.key)

    # -- internals ----------------------------------------------------------- #
    def _reload(self, prefer: str | None = None) -> None:
        """Refill the field box for the selected group, silently.

        Signals are blocked while the list is rebuilt: Qt emits
        ``currentIndexChanged`` as items come and go, and each of those would
        otherwise look like the user picking a field.
        """
        category = str(self._category.currentData() or "")
        blocked = self._product.blockSignals(True)
        try:
            self._product.clear()
            for spec in self._catalogue.available_products():
                if spec.category != category:
                    continue
                self._product.addItem(spec.label, spec.key)
                self._product.setItemData(
                    self._product.count() - 1,
                    _field_tooltip(spec),
                    Qt.ToolTipRole,
                )
            index = self._product.findData(prefer) if prefer else -1
            self._product.setCurrentIndex(max(0, index))
        finally:
            self._product.blockSignals(blocked)

    def _on_category(self, _index: int) -> None:
        """Move to another group, answering with its remembered field."""
        category = str(self._category.currentData() or "")
        prefer = None
        memory = getattr(self, "_category_memory", None)
        if isinstance(memory, dict):
            wanted = str(memory.get(category) or "").strip()
            if wanted:
                candidate = self._catalogue.get_product(wanted)
                if str(getattr(candidate, "key", "")) == wanted and \
                        candidate.category == category:
                    prefer = wanted
        self._reload(prefer)
        self._on_product(0)

    def _on_product(self, _index: int) -> None:
        key = self._product.currentData()
        if key:
            try:
                spec = self._catalogue.get_product(str(key))
                if str(getattr(spec, "key", "")) == str(key):
                    self._category_memory[str(spec.category)] = str(key)
            except Exception:  # noqa: BLE001 - memory never breaks selection
                pass
            self.productChanged.emit(str(key))

    def category_memory(self) -> dict:
        """Return this selector's remembered ``{category: product}`` (T24.2)."""
        memory = getattr(self, "_category_memory", None)
        return dict(memory) if isinstance(memory, dict) else {}

    def set_category_memory(self, memory) -> None:
        """Replace the remembered per-category products (T24.2/session)."""
        if isinstance(memory, dict):
            self._category_memory = {str(key): str(value)
                                     for key, value in memory.items()}
        else:
            self._category_memory = {}


def _field_tooltip(spec) -> str:
    """Describe one field, and say so where the model cannot deliver it exactly."""
    lines = [spec.label]
    if spec.description:
        lines.append(str(spec.description))
    if getattr(spec, "caveat", ""):
        lines.append(f"Caveat: {spec.caveat}")
    return "\n".join(lines)


class _MapPanel(QFrame):
    """One map, its own field selector, and the status of its fetch."""

    #: Relayed from the map so the view can fan a settled view out to the
    #: siblings. Carries the panel so the handler knows which view not to
    #: overwrite.
    viewSettled = Signal(object)

    #: Emitted only for a field the *user* chose, carrying this panel's position
    #: and the resolved key, so a host can persist the arrangement.
    productChanged = Signal(int, str)
    statusUpdated = Signal()

    #: Relayed from the map. A click chooses where a sounding would be taken;
    #: a double-click asks for it. The click carries the panel, the way
    #: :attr:`viewSettled` does, because which panel was clicked decides which
    #: field a sounding taken here should carry.
    pointSelected = Signal(object, float, float)
    pointActivated = Signal(float, float)

    def __init__(self, index: int, product: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_CARD)
        self._index = int(index)
        # Whether this panel is part of the current layout, tracked here rather
        # than read back from ``isVisible()``. Qt's answer folds in the ancestors:
        # every panel reports invisible while the tab hosting them is in the
        # background, or before it has been shown at all. Gating the view sync on
        # that meant a layout built off-screen fanned its view out to nobody.
        self._active = False
        # Whether the tab hosting this panel is the one on screen. Fetching is
        # gated on *both*, so a panel that exists but is not being looked at costs
        # nothing: the tab is built lazily and would otherwise fire two to four
        # HRRR requests, plus a recurring refresh, for a view nobody switched to.
        self._running = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xs"], SPACE["xs"], SPACE["xs"], SPACE["xs"])
        outer.setSpacing(SPACE["xs"])

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(SPACE["xs"])
        self.title = QLabel(f"{int(index) + 1:02d}", self)
        self.title.setObjectName(OBJ_HINT)
        self.title.setWordWrap(False)
        self.title.setAccessibleName(f"Field panel {int(index) + 1} title")
        header.addWidget(self.title)
        self.selector = _FieldSelector(product, parent=self)
        header.addWidget(self.selector, 1)
        self.actions_btn = QToolButton(self)
        self.actions_btn.setText("⋯")
        self.actions_btn.setToolTip("Search fields, maximise or swap this panel")
        self.actions_btn.setAccessibleName(
            f"Field panel {int(index) + 1} actions")
        self.actions_btn.setMinimumSize(CONTROL_H["sm"], CONTROL_H["sm"])
        self.actions_btn.setPopupMode(QToolButton.InstantPopup)
        self.actions_menu = QMenu(self.actions_btn)
        self.search_action = self.actions_menu.addAction("Search fields…")
        self.search_action.triggered.connect(self.selector.open_search)
        self.maximise_action = self.actions_menu.addAction("Maximise panel")
        self.maximise_action.setCheckable(True)
        self.maximise_action.triggered.connect(self._on_maximise_toggled)
        self.swap_action = self.actions_menu.addAction("Swap field with…")
        self.swap_action.triggered.connect(self._on_swap_requested)
        self.actions_btn.setMenu(self.actions_menu)
        header.addWidget(self.actions_btn)
        outer.addLayout(header)

        # A point map, not a plain station map, because choosing where to take a
        # sounding is half of what these panels are for: the field tells you
        # where to look, and the click has to be available in the same place.
        self.map = PointMapWidget()
        self.map.setParent(self)
        self.map._legend_metadata_in_rail = True
        self.map._crosshair_overlay = CrosshairOverlay(self.map)
        # The point is already named in the top bar and rail; four copies of its
        # coordinates cover map labels at comparison size. Hover and box readouts
        # still appear when they carry new information.
        self.map._compact_readout = True
        # Flat, always. A field raster cannot be placed correctly on a cone, so a
        # curved projection hides map imagery -- which in this view would mean
        # panels that are empty for a reason nothing on screen explains.
        # Presentation (labels, scale units) still follows the picker, but the
        # projection choice itself is not applied: panels stay flat by design.
        self.map.set_projection("flat")
        try:
            from sharpmod.ui.features.gui_maps import presentation_preferences

            self.map.restore_presentation_preferences(
                presentation_preferences(self.map))
        except Exception:  # noqa: BLE001 - presentation never blocks panels
            pass
        self.map.setMinimumSize(220, 160)
        self.map.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(self.map, 1)

        self.status = QLabel("", self)
        self.status.setObjectName(OBJ_HINT)
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.status.installEventFilter(self)
        self.status.setVisible(False)
        recovery = QHBoxLayout()
        recovery.setContentsMargins(0, 0, 0, 0)
        recovery.setSpacing(SPACE["xs"])
        recovery.addWidget(self.status, 1)
        self.retry_btn = QToolButton(self)
        self.retry_btn.setText("Retry")
        self.retry_btn.setToolTip("Retry only this panel's selected field")
        self.retry_btn.setAccessibleName(
            f"Retry field panel {int(index) + 1}")
        self.retry_btn.setVisible(False)
        self.retry_btn.clicked.connect(lambda: self.field.retry())
        recovery.addWidget(self.retry_btn)
        self.cancel_btn = QToolButton(self)
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setToolTip("Stop this panel's current request")
        self.cancel_btn.setAccessibleName(
            f"Cancel field panel {int(index) + 1} request")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(lambda: self.field.cancel())
        recovery.addWidget(self.cancel_btn)
        # The picker places this row in Field setup. Keeping it outside the
        # panel gives the map the height formerly reserved by every footer.
        self.activity_row = QWidget(self)
        self.activity_row.setObjectName(OBJ_PLAIN)
        self.activity_row.setLayout(recovery)

        # One controller per map, which is forced rather than chosen: every
        # product writes the same ``hrrr_field`` overlay key, so two controllers
        # sharing a map would clobber each other on every fetch.
        #
        # Built switched *off*. An enabled controller requests immediately from its
        # constructor, so building this tab would reach the network before anyone
        # had looked at it; :meth:`set_running` turns the visible panels on once
        # the tab is shown.
        self.field = HrrrFieldController(
            self.map, parent=self, enabled=False, product=product
        )
        # Field panels use the header selector instead of the controller's
        # standalone settings widget. Own and hide that widget so it is
        # destroyed with this panel; leaving it parentless retains a full
        # hidden control tree for every panel in long-running sessions.
        unused_controls = self.field.controls_widget()
        unused_controls.setParent(self)
        unused_controls.hide()
        self.field.statusChanged.connect(self._on_status)
        self.selector.productChanged.connect(self._on_product_chosen)
        self.selector.set_product(self.field.product())
        self.refresh_title()
        self.map.viewSettled.connect(self._on_view_settled)
        self.map.pointSelected.connect(self._on_point_selected)
        self.map.pointActivated.connect(self.pointActivated)

    # -- public API ---------------------------------------------------------- #
    def product(self) -> str:
        return self.field.product()

    def set_product(self, key: str, *, remember_category: bool = False) -> None:
        self.field.set_product(str(key))
        self.selector.set_product(
            self.field.product(), remember_category=remember_category,
            memory=self.category_memory())
        self.refresh_title()

    def category_memory(self) -> dict:
        """Return this panel's remembered ``{category: product}`` (T24.2)."""
        try:
            return dict(self.selector.category_memory())
        except (AttributeError, RuntimeError):
            return {}

    def set_category_memory(self, memory) -> None:
        """Replace this panel's remembered per-category products (T24.2)."""
        try:
            self.selector.set_category_memory(memory)
        except (AttributeError, RuntimeError):
            pass

    def panel_label(self) -> str:
        """Return the short readable panel title (T24.2/4)."""
        try:
            from sharpmod.providers import hrrr_products

            return str(hrrr_products.get_product(self.product()).label)
        except Exception:  # noqa: BLE001 - a label never breaks panels
            return str(self.product())

    def is_active(self) -> bool:
        """Whether this panel is part of the current layout."""
        return self._active

    def set_active(self, active: bool) -> None:
        """Show or hide the panel, and stop a hidden one doing any work.

        Disabling the controller matters as much as hiding the widget: a hidden
        panel that kept fetching would spend a request and a worker on a field
        nobody can see, on every run, forecast hour, and pan.
        """
        self._active = bool(active)
        self.setVisible(self._active)
        self._sync_enabled()

    def set_running(self, running: bool) -> None:
        """Say whether the view hosting this panel is on screen."""
        self._running = bool(running)
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        """Fetch only while this panel is both in the layout and on screen."""
        wanted = self._active and self._running
        if self.field.is_enabled() != wanted:
            self.field.set_enabled(wanted)

    def set_forecast_reference(self, run, fxx) -> None:
        self.field.set_forecast_reference(run, fxx)
        try:
            if run is not None and fxx is not None:
                self.map.set_inspection_product(self.field.product(), run=run,
                                                fxx=int(fxx))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def on_view_settled(self) -> None:
        """Let the field retry once the view moves back into HRRR coverage."""
        self.field.on_view_settled()

    def shutdown(self) -> None:
        self.field.shutdown()

    # -- internals ----------------------------------------------------------- #
    def _on_product_chosen(self, key: str) -> None:
        self.field.set_product(str(key))
        # Inspection reads the same field the panel depicts (T18.4); the run
        # and hour travel separately through ``set_forecast_reference``.
        try:
            run = getattr(self.field, "_run", None)
            fxx = getattr(self.field, "_fxx", None)
            if run is not None and fxx is not None:
                self.map.set_inspection_product(self.field.product(), run=run,
                                                fxx=int(fxx))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        self.refresh_title()
        # Report what the controller settled on rather than what was picked: the
        # catalogue is what decides the key, and that is the value worth storing.
        self.productChanged.emit(self._index, self.field.product())

    def refresh_title(self) -> None:
        """Keep the compact panel number and expose its full field name."""
        try:
            name = f"Panel {int(self._index) + 1} · {self.panel_label()}"
            self.title.setAccessibleName(name)
            self.title.setToolTip(name)
        except (AttributeError, RuntimeError):
            pass

    def set_maximised_selected(self, selected: bool) -> None:
        """Mirror the grid maximise state in this panel's actions menu."""
        action = getattr(self, "maximise_action", None)
        if action is None:
            return
        try:
            action.blockSignals(True)
            action.setChecked(bool(selected))
            action.setText("Restore grid" if selected else "Maximise panel")
        finally:
            try:
                action.blockSignals(False)
            except (AttributeError, RuntimeError):
                pass

    def _on_maximise_toggled(self, checked: bool) -> None:
        from sharpmod.ui.maps.panel_view import MapPanelsView
        view = None
        try:
            parent = self.parent()
            view = parent if isinstance(parent, MapPanelsView) else None
            if view is None:
                ancestor = self.parentWidget()
                while ancestor is not None and not isinstance(
                        ancestor, MapPanelsView):
                    ancestor = ancestor.parentWidget()
                view = ancestor
        except (AttributeError, RuntimeError):
            view = None
        if view is None:
            self.set_maximised_selected(False)
            return
        if bool(checked):
            changed = view.set_maximised_panel(int(self._index))
            if not changed:
                self.set_maximised_selected(False)
        else:
            view.restore_maximised()

    def _on_swap_requested(self) -> None:
        from sharpmod.ui.maps.panel_view import MapPanelsView
        """Offer the sibling slots this panel can swap with (T24.2)."""
        from qtpy.QtWidgets import QInputDialog

        view = None
        try:
            ancestor = self.parentWidget()
            while ancestor is not None and not isinstance(
                    ancestor, MapPanelsView):
                ancestor = ancestor.parentWidget()
            view = ancestor
        except (AttributeError, RuntimeError):
            view = None
        if view is None:
            return
        options = []
        for panel in getattr(view, "_panels", ()):
            try:
                position = int(panel.index)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
            if position == int(self._index):
                continue
            try:
                label = panel.panel_label()
            except (AttributeError, RuntimeError):
                label = str(panel.product())
            options.append((f"Panel {position + 1} · {label}", position))
        if not options:
            return
        labels = [text for text, _position in options]
        choice, accepted = QInputDialog.getItem(
            self, "Swap panels",
            "Swap this panel's field with:",
            labels, 0, False)
        if not accepted:
            return
        for text, position in options:
            if text == str(choice):
                view.swap_panels(int(self._index), int(position))
                return

    def _on_status(self, text: str) -> None:
        """Show what a panel cannot otherwise say about its own field.

        The controller describes a delivered field in three lines -- its name, the
        run that answered, and the product's caveat. Two of those are already on
        this panel: the name is in the selector directly above the map, and the
        caveat is the selector's hover text. Repeated under every panel they cost
        three lines of map each and said nothing new, which is what filled the
        space under the panels in a four-up layout.

        So a *successful* fetch reports only the run and age. Anything else --
        fetching, unavailable, failed -- is passed through whole, because that is
        exactly when the words are the only thing explaining an empty panel.
        Loading and missing panels keep their configured slot: the status names
        the field, the run/hour when pinned, and the reason, so a missing panel
        never reshuffles the grid (T24.4).
        """
        state = self.field.layer_state()
        raster = self.field.attached_raster()
        subtitle = str(getattr(raster, "subtitle", "") or "")
        full_detail = str(text or "").strip()
        if state == "available" and raster is not None:
            text = subtitle or text
        elif raster is not None and state in (
                "loading", "offline", "no-data", "failed", "canceled"):
            label = {
                "loading": "Loading replacement",
                "offline": "Offline",
                "no-data": "No data for selected run/hour",
                "failed": "Failed",
                "canceled": "Canceled",
            }[state]
            text = f"{label} · cached · {subtitle}".strip()
        elif raster is not None and state == "outside-domain":
            text = f"Outside · cached · {subtitle}".strip()
        elif state == "no-data":
            selected = self.placeholder_text().replace(" · loading…", "")
            text = f"No data — {selected}"
        elif state == "outside-domain":
            text = "Outside HRRR domain"
        if not str(text or "").strip():
            text = self.placeholder_text()
        self._status_text = str(text or "")
        self._update_status_elision()
        self.status.setToolTip(full_detail or str(text or ""))
        try:
            self.status.setAccessibleDescription(
                "\n".join(part for part in (str(text or ""), full_detail)
                          if part))
        except AttributeError:
            pass
        self.status.setVisible(bool(text))
        self.retry_btn.setVisible(state in (
            "offline", "no-data", "failed", "canceled"))
        self.cancel_btn.setVisible(state == "loading")
        self.statusUpdated.emit()

    def _update_status_elision(self) -> None:
        """Keep errors readable on hover without letting them shrink the map."""
        text = getattr(self, "_status_text", "")
        self.status.setText(text)
        if not self.isVisible():
            return
        width = max(self.status.width(), 1)
        elided = self.status.fontMetrics().elidedText(
            text, Qt.ElideMiddle, width)
        if elided != text and len(text) <= 64:
            return
        self.status.setText(elided)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._update_status_elision()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self.status and event.type() in {
            QEvent.Resize, QEvent.Show, QEvent.ShowToParent,
        }:
            self._update_status_elision()
        return super().eventFilter(watched, event)

    def placeholder_text(self) -> str:
        """Return the stable loading/missing placeholder for this slot (T24.4)."""
        label = self.panel_label()
        try:
            run = getattr(self.field, "_run", None)
            fxx = getattr(self.field, "_fxx", None)
            if run is not None and fxx is not None:
                from sharpmod.providers import hrrr_field

                valid = hrrr_field.field_valid_label(run, int(fxx))
                if valid:
                    return f"{label} · {valid} · loading…"
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        return f"{label} · loading…"

    def accessible_text(self) -> str:
        """Return the spoken panel equivalent (T24.1/T24.4)."""
        parts = [f"Panel {int(self._index) + 1}", self.panel_label()]
        try:
            status = str(self.status.text() or "").strip()
        except (AttributeError, RuntimeError):
            status = ""
        if status:
            parts.append(status)
        else:
            parts.append(self.placeholder_text())
        try:
            crosshair = self.map.crosshair_accessible_text()
        except (AttributeError, RuntimeError):
            crosshair = ""
        if crosshair:
            parts.append(crosshair)
        return "; ".join(part for part in parts if part)

    def _on_view_settled(self) -> None:
        self.viewSettled.emit(self)

    def _on_point_selected(self, lat: float, lon: float) -> None:
        self.pointSelected.emit(self, float(lat), float(lon))

    @property
    def index(self) -> int:
        return self._index




from sharpmod.ui.maps.panel_view import MapPanelsView, _SharedMapFacade  # noqa: E402


__all__ = [
    "DEFAULT_PANEL_PRESET_KEY",
    "DEFAULT_PANEL_PRODUCTS",
    "MAX_PANELS",
    "PANEL_CATEGORY_MEMORY_VERSION",
    "PANEL_COUNTS",
    "PANEL_PRESETS",
    "MapPanelsView",
    "migrate_panel_preset_choice",
    "normalise_panel_category_memory",
    "panel_state",
    "preset_panel_products",
    "resolve_panel_products",
    "restore_panel_state",
]
