"""Explicit native search over existing selector identities, not editable labels."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import weakref

from qtpy.QtCore import QObject, QPersistentModelIndex, Qt
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QToolButton, QVBoxLayout,
)

from sharpmod.ui.features.gui_commands import search_text
from sharpmod.ui.features.gui_common import make_status_label, set_status_label
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE


@dataclass(frozen=True)
class Choice:
    key: str
    label: str
    details: str = ""
    aliases: str = ""
    index: object = None

    def matches(self, query):
        blob = search_text(f"{self.key} {self.label} {self.details} {self.aliases}")
        compact = blob.replace(" ", "")
        return all(token in blob or token in compact for token in search_text(query).split())


# Presentation synonyms only. Scientific keys, computations and resolver
# aliases remain in their existing registries.
_ABBREVIATIONS = {
    "hrrr": "high resolution rapid refresh",
    "rap": "rapid refresh",
    "nam": "north american mesoscale",
    "gfs": "global forecast system",
    "gefs": "global ensemble forecast system",
    "ifs": "integrated forecasting system",
    "aifs": "artificial intelligence forecasting system",
    "cfs": "climate forecast system",
    "rrfs": "rapid refresh forecast system",
    "gdps": "global deterministic prediction system",
    "rdps": "regional deterministic prediction system",
    "cape": "convective available potential energy",
    "cin": "convective inhibition",
    "ml": "mixed layer",
    "mu": "most unstable",
    "sb": "surface based",
    "srh": "storm relative helicity",
    "lcl": "lifted condensation level",
    "lfc": "level free convection",
    "el": "equilibrium level",
    "scp": "supercell composite",
    "stp": "significant tornado parameter",
    "pwat": "precipitable water",
    "rh": "relative humidity",
    "ehi": "energy helicity index",
    "uh": "updraft helicity",
}


def _expansions(key):
    words = search_text(key)
    return " ".join(value for abbreviation, value in _ABBREVIATIONS.items()
                    if abbreviation in words.split() or words.startswith(abbreviation)
                    or (abbreviation in {"cape", "cin"} and abbreviation in words))


@lru_cache(maxsize=1)
def _registry_metadata():
    # Only invoked on explicit search, never the picker import/first-paint path.
    from sharpmod.analysis.box_analysis import PARAMETERS
    from sharpmod.providers.hrrr_products import available_products
    from sharpmod.tools.model_extract import available_models, model_aliases

    metadata = {}
    for parameter in PARAMETERS:
        metadata[("parameters", parameter.key)] = (parameter.group, _expansions(parameter.key))
    for product in available_products():
        metadata[("products", product.key)] = (f"{product.category} · {product.palette.units}",
                                              f"{product.chip} {product.description} {_expansions(product.key)}")
    for config in available_models():
        aliases = " ".join(alias for alias, key in model_aliases().items() if key == config.key)
        metadata[("models", config.key)] = (f"{config.domain} · {config.notes}",
                                            f"{aliases} {_expansions(config.key)}")
    return metadata


def combo_choices(combo):
    metadata = _registry_metadata()
    keys = {str(combo.itemData(row)) for row in range(combo.count())}
    group = getattr(combo, "_sharpmod_selector_bucket", None)
    if group is None:
        scores = {bucket: sum((bucket, key) in metadata for key in keys)
                  for bucket in ("parameters", "models", "products")}
        group = max(scores, key=scores.get) if max(scores.values()) else ""
    choices = []
    for row in range(combo.count()):
        index = combo.model().index(row, combo.modelColumn(), combo.rootModelIndex())
        if str(index.data(Qt.AccessibleDescriptionRole) or "") == "separator":
            continue
        data, label = combo.itemData(row), combo.itemText(row)
        key = str(data) if isinstance(data, (str, int, float, bool)) else label
        details, aliases = metadata.get((group, key), (str(combo.itemData(row, Qt.ToolTipRole) or ""), ""))
        choices.append(Choice(key, label, details, aliases, QPersistentModelIndex(index)))
    bucket = group or "selector-" + hashlib.sha256(
        "\n".join(choice.key for choice in choices).encode("utf-8")
    ).hexdigest()[:16]
    return tuple(choices), bucket


def product_choices():
    from sharpmod.providers.hrrr_products import available_products

    return tuple(Choice(spec.key, spec.label,
                        f"{spec.category} · {spec.palette.units}",
                        f"{spec.chip} {spec.description} {_expansions(spec.key)}")
                 for spec in available_products())


class SelectorSearch(QDialog):
    def __init__(self, combo, *, settings=None):
        super().__init__(combo.window())
        self._combo_ref = weakref.ref(combo)
        self._catalog = getattr(combo, "_sharpmod_search_catalog", None)
        if self._catalog is None:
            self.choices, self.bucket = combo_choices(combo)
        else:
            provider, _select_ref, self.bucket = self._catalog
            self.choices = provider()
        if settings is None:
            from sharpmod.ui.features.gui_settings import _build_settings

            settings = getattr(combo.window(), "_settings", None) or _build_settings()
        self._settings = settings
        self._favorite_key = f"selectors/favorites/{self.bucket}"
        saved = settings.value(self._favorite_key, [], list) or []
        self._favorites = {str(value) for value in saved}
        self.setObjectName("selectorSearch")
        name = combo.accessibleName() or "choices"
        self.setWindowTitle(f"Search {name}")
        self.resize(min(780, max(360, combo.window().width() - 80)), 550)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])
        hint = QLabel("Search a full name, abbreviation, or alias. Choose a result to apply; Cancel changes nothing.")
        hint.setObjectName(OBJ_HINT)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit(self)
        self.search.setAccessibleName(f"Search {name}")
        self.search.setPlaceholderText("Name, abbreviation, or alias…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.only_favorites = QCheckBox("Favorites only", self)
        layout.addWidget(self.only_favorites)
        self.results = QListWidget(self)
        self.results.setAccessibleName(f"Matching {name}")
        self.results.setWordWrap(True)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)
        self.favorite = QToolButton(self)
        self.favorite.setText("Add to favorites")
        self.favorite.setAccessibleName("Toggle selected choice favorite")
        layout.addWidget(self.favorite)
        self.status = make_status_label(parent=self)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        self.apply_button = buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("Use selected")
        self.apply_button.setAutoDefault(False)
        self.apply_button.setDefault(False)
        buttons.accepted.connect(self.use_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.filter_choices)
        self.search.returnPressed.connect(self.use_selected)
        self.only_favorites.toggled.connect(self.filter_choices)
        self.results.itemActivated.connect(self.use_selected)
        self.results.currentItemChanged.connect(self._selection_status)
        self.favorite.clicked.connect(self.toggle_favorite)
        self.filter_choices()

    def _available(self, choice):
        combo = self._combo_ref()
        try:
            if combo is None or not combo.isEnabled():
                return False
            if self._catalog is not None:
                return self._catalog[1]() is not None
            return (choice.index.isValid() and choice.index.model() is combo.model()
                    and choice.index.parent() == combo.rootModelIndex()
                    and choice.index.column() == combo.modelColumn()
                    and bool(choice.index.flags() & Qt.ItemIsEnabled))
        except RuntimeError:
            return False

    def filter_choices(self, *_args):
        current = self.results.currentItem()
        previous = current.data(Qt.UserRole).key if current is not None else None
        self.results.blockSignals(True)
        try:
            self.results.clear()
            selected = None
            for choice in self.choices:
                if not choice.matches(self.search.text()):
                    continue
                if self.only_favorites.isChecked() and choice.key not in self._favorites:
                    continue
                text = f"{'★ ' if choice.key in self._favorites else ''}{choice.label}"
                if choice.details:
                    text += f"\n{choice.details}"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, choice)
                item.setToolTip(f"{choice.label}\n{choice.key}\n{choice.details}")
                self.results.addItem(item)
                if choice.key == previous:
                    selected = item
            if selected is not None:
                self.results.setCurrentItem(selected)
            elif self.results.count():
                self.results.setCurrentRow(0)
        finally:
            self.results.blockSignals(False)
        self._selection_status()

    def _selection_status(self, *_args):
        item = self.results.currentItem()
        self.favorite.setEnabled(item is not None)
        choice = item.data(Qt.UserRole) if item is not None else None
        self.apply_button.setEnabled(choice is not None and self._available(choice))
        self.favorite.setText("Remove from favorites" if choice is not None and choice.key in self._favorites
                              else "Add to favorites")
        set_status_label(self.status, f"Selected: {choice.label} · {choice.key}" if choice is not None else
                         "No matching choice. Try a shorter name or turn off Favorites only.")

    def toggle_favorite(self):
        item = self.results.currentItem()
        if item is None:
            return
        key = item.data(Qt.UserRole).key
        if key in self._favorites:
            self._favorites.remove(key)
        else:
            self._favorites.add(key)
        self._settings.setValue(self._favorite_key, sorted(self._favorites))
        self._settings.sync()
        self.filter_choices()

    def use_selected(self, *_args):
        item = self.results.currentItem()
        if item is None:
            return
        choice = item.data(Qt.UserRole)
        if not self._available(choice):
            self.apply_button.setEnabled(False)
            set_status_label(self.status, "Choices changed or this field is unavailable. Cancel and search again.", level="warning")
            return
        combo = self._combo_ref()
        if self._catalog is not None:
            select = self._catalog[1]()
            self.accept()
            select(choice.key)
        else:
            row = choice.index.row()
            self.accept()
            combo.setCurrentIndex(row)


class _SelectorController(QObject):
    def __init__(self, combo):
        super().__init__(combo)
        self._combo_ref = weakref.ref(combo)
        self.dialog = None

    def open(self, *_args):
        combo = self._combo_ref()
        if combo is None or not combo.isEnabled() or not combo.count():
            return
        # Delete the previous closed dialog, keeping this interaction bounded.
        if self.dialog is not None:
            try:
                self.dialog.deleteLater()
            except RuntimeError:
                pass
        self.dialog = SelectorSearch(combo)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.search.setFocus()


def configure_combo_search(combo):
    if getattr(combo, "_sharpmod_selector_search", None) is not None:
        return combo._sharpmod_selector_search
    controller = _SelectorController(combo)
    action = QAction("Search choices…", combo)
    action.setShortcut("Ctrl+Space")
    action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
    action.triggered.connect(controller.open)
    combo.addAction(action)
    if not combo.isEditable():
        combo.setContextMenuPolicy(Qt.ActionsContextMenu)
    combo.setToolTip(f"{combo.toolTip()}\nSearch choices: Ctrl+Space or right-click.".strip())
    combo.setAccessibleDescription(f"{combo.accessibleDescription()} Search choices with Ctrl+Space.".strip())
    combo._sharpmod_selector_search = controller
    return controller


def selector_search_button(combo, *, name=None):
    if name:
        combo.setAccessibleName(name)
    controller = configure_combo_search(combo)
    button = QToolButton(combo.parentWidget())
    button.setText("Search…")
    button.setAccessibleName(f"Search {combo.accessibleName() or 'choices'}")
    button.setToolTip("Search names, abbreviations, aliases and favorites (Ctrl+Space)")
    button.clicked.connect(controller.open)
    return button


def bind_product_search(combo, select):
    combo._sharpmod_search_catalog = (product_choices, weakref.WeakMethod(select), "products")
    return selector_search_button(combo, name="forecast field")


def selector_row(combo, *, name=None):
    row = QHBoxLayout()
    row.addWidget(combo, 1)
    row.addWidget(selector_search_button(combo, name=name))
    return row
