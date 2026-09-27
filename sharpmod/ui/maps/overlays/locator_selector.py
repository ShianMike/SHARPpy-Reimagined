"""LocatorOverlaySelector overlay controls."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
from time import monotonic

from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.providers import radar_mosaic, radar_site, spc_outlook
from sharpmod.ui.features.gui_workers import (
    _HrrrFieldWorker,
    _RadarMosaicWorker,
    _RadarSiteWorker,
    _SpcOutlookWorker,
    _StormReportsWorker,
)
from sharpmod.ui.picker.layout import dependent_panel
from sharpmod.ui.features.gui_threading import retain_worker_until_finished
from sharpmod.maps.map_overlays import format_age
from sharpmod.ui.styles.theme import OBJ_HINT, OBJ_PLAIN, OBJ_SECTION_LABEL, SPACE
from sharpmod.ui.maps.overlays.controllers import (
    LOCATOR_SETTINGS_KEY,
    _product_item_text
)


class LocatorOverlaySelector(QObject):
    """Pick which overlays travel onto a sounding's locator inset.

    A thin control over :mod:`sharpmod.maps.locator_overlay`, which owns the rules.
    Nothing here decides what may coexist; it presents the families, asks that
    module to reduce the selection, and then makes the boxes agree with the
    answer. Keeping the judgement in one place is what stops this control and
    the ``--locator-overlay`` command line drifting apart.

    Two behaviours are worth stating, because both are the control telling the
    truth rather than quietly disagreeing with what will be drawn:

    * Choosing radar clears the outlook and the field, since radar displaces
      them. The boxes move, so the control never claims to be showing something
      that has been displaced.
    * Storm reports stay disabled, with a tooltip saying why, until the outlook
      is selected. They are read against it -- the question is whether what was
      forecast happened -- and a scatter of markers with nothing behind them
      cannot answer that.
    """

    #: Emitted whenever the effective selection changes.
    selectionChanged = Signal()

    #: Human labels, in the order :data:`locator_overlay.FAMILIES` gives.
    LABELS = {
        "risk": "SPC risk areas",
        "reports": "Storm reports",
        "hrrr": "HRRR model field",
        "radar-site": "Radar (nearest site)",
        "radar-mosaic": "Radar (national mosaic)",
    }

    #: Combo entry meaning "whatever hazard the map is showing". Kept as the
    #: default because the inset is context for the map the sounding came from,
    #: so following it is the answer that needs no decision.
    FOLLOW_MAP_LABEL = "Match the map"

    def __init__(self, *, parent=None, settings=None, show_heading=True):
        super().__init__(parent)
        from sharpmod.maps import locator_overlay

        self._rules = locator_overlay
        self._settings = settings
        self._applying = False
        self._boxes: dict = {}

        # OBJ_PLAIN for the same reason as the overlay controllers above: an
        # unnamed container repaints the window surface over the card holding it.
        self._widget = QWidget()
        self._widget.setObjectName(OBJ_PLAIN)
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(0, 0, 0, 0)
        # One switch-to-switch gap for the whole card: the overlay controllers
        # above use `xs` internally and the card's own layout uses it between
        # them, so this group matching it makes every row in the card sit on the
        # same rhythm. At `xxs` these five were visibly tighter than the six above.
        layout.setSpacing(SPACE["xs"])
        if show_heading:
            caption = QLabel("Show on the sounding's locator")
            caption.setObjectName(OBJ_SECTION_LABEL)
            caption.setToolTip(
                "Overlays drawn on the small map beside the hodograph. The risk "
                "areas and a model field can be shown together; radar replaces "
                "them."
            )
            layout.addWidget(caption)
            layout.addSpacing(SPACE["xxs"])

        # Built before the loop so ``_sync`` and :meth:`hazard` never depend on
        # the risk family's position in FAMILIES; the loop only places it.
        hazard_combo = self._build_hazard_combo()
        for family in self._rules.FAMILIES:
            box = QCheckBox(self.LABELS.get(family, family))
            box.setProperty("locator_family", family)
            box.toggled.connect(self._on_toggled)
            layout.addWidget(box)
            self._boxes[family] = box
            if family == self._rules.FAMILY_RISK:
                layout.addWidget(hazard_combo)

        self._restore()
        self._sync()

    def _build_hazard_combo(self):
        """Choose which outlook the inset draws, independently of the map.

        The inset had no hazard control at all: the selection it produced named
        only the family, so it could only ever draw whichever outlook something
        else had chosen. Naming a hazard here makes it an explicit request, which
        outranks the map for exactly that reason.
        """
        self._hazard = QComboBox()
        self._hazard.addItem(self.FOLLOW_MAP_LABEL, None)
        for spec in spc_outlook.PRODUCTS.values():
            self._hazard.addItem(_product_item_text(spec, None), spec.key)
        self._hazard.setCurrentIndex(0)
        self._hazard.setToolTip(
            "Which outlook the locator inset draws. \u201c"
            f"{self.FOLLOW_MAP_LABEL}\u201d follows the hazard selected for the "
            "map; naming one here pins the inset to it instead. Hazard "
            "probabilities are issued for Days 1 and 2, so a sounding outside "
            "those days falls back to the categorical outlook."
        )
        self._hazard.currentIndexChanged.connect(self._on_hazard_changed)
        return self._hazard

    def hazard(self):
        """Return the pinned outlook product, or ``None`` to follow the map."""
        return self._hazard.currentData()

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self):
        """Return the widget a tab should mount."""
        return self._widget

    def selection(self):
        """Return the reduced selection, as the rules module resolves it."""
        hazard = self.hazard()
        chosen = [
            self._rules.Selection(
                family,
                hazard if family == self._rules.FAMILY_RISK else None,
            )
            for family in self._rules.FAMILIES
            if self._boxes[family].isChecked()
        ]
        return self._rules.enforce_exclusivity(tuple(chosen))

    def spec(self) -> str:
        """Return the selection in the form the command line accepts."""
        return ",".join(item.spec() for item in self.selection()) or "none"

    def set_spec(self, text: str | None) -> None:
        """Adopt a specification string, ignoring anything unrecognised."""
        try:
            selections = self._rules.parse(text)
        except Exception:  # noqa: BLE001 - a stored string is not trusted
            selections = ()
        wanted = {item.family for item in selections}
        hazard = next(
            (
                item.product
                for item in selections
                if item.family == self._rules.FAMILY_RISK
            ),
            None,
        )
        self._applying = True
        try:
            for family, box in self._boxes.items():
                box.setChecked(family in wanted)
            # An unknown product falls back to following the map rather than
            # silently pinning the inset to something that cannot be drawn.
            index = self._hazard.findData(hazard)
            self._hazard.setCurrentIndex(max(0, index))
        finally:
            self._applying = False
        self._sync()

    def remember(self) -> None:
        """Persist the *choice*, never whether it was switched on.

        The same policy the field and radar choices follow: a launch should still
        reach for no network until asked, while a user who always wants the risk
        areas finds them already selected.
        """
        if self._settings is None:
            return
        with suppress(Exception):
            self._settings.setValue(LOCATOR_SETTINGS_KEY, self.spec())

    # -- internals ----------------------------------------------------------- #
    def _restore(self) -> None:
        if self._settings is None:
            return
        try:
            stored = self._settings.value(LOCATOR_SETTINGS_KEY, "")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return
        self.set_spec(str(stored or "") or None)

    def _on_toggled(self, _checked) -> None:
        if self._applying:
            return
        self._sync()
        self.selectionChanged.emit()

    def _on_hazard_changed(self, _index) -> None:
        if self._applying:
            return
        self._sync()
        self.selectionChanged.emit()

    def _sync(self) -> None:
        """Make the boxes agree with what the rules will actually draw."""
        effective = {item.family for item in self.selection()}
        # Which outlook to draw only means something once one is being drawn, the
        # same collapse the map's own outlook card uses.
        self._hazard.setVisible(self._rules.FAMILY_RISK in effective)
        self._applying = True
        try:
            for family, box in self._boxes.items():
                if box.isChecked() and family not in effective:
                    # Displaced by an exclusive choice, or its prerequisite is
                    # not selected. Either way it will not be drawn, so the box
                    # must not go on claiming otherwise.
                    box.setChecked(False)
                missing = self._rules.missing_prerequisite(family, self.selection())
                box.setEnabled(missing is None)
                box.setToolTip(
                    ""
                    if missing is None
                    else f"Select {self.LABELS.get(missing, missing)} first; "
                    "storm reports are read against the outlook that "
                    "anticipated them."
                )
        finally:
            self._applying = False
