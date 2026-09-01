"""Which overlay hazard a newly opened sounding inherits from the picker.

Two source tabs own an SPC overlay: the Station Map and the Forecast Model tab.
The sounding viewer asks the picker which hazard to put on the hodograph's
locator inset, and the answer has to come from the tab the user is actually
working in.

Ranking the two controllers in a fixed order looked right for as long as only
one of them was ever switched on. With both enabled, the first in the list won:
a sounding fetched from the Forecast Model tab while showing the wind
probability came back categorical. These tests pin the tab in front as the
decider, and pin the tab titles the mapping is keyed on.
"""

from __future__ import annotations

import pytest

from sharpmod import gui_picker
from sharpmod.gui_viewer import _controller_overlay_product


class _Tabs:
    """The slice of the tab surface the selection actually touches."""

    def __init__(self, titles, current: int = 0):
        self._titles = list(titles)
        self._current = current

    def count(self) -> int:
        return len(self._titles)

    def currentIndex(self) -> int:  # noqa: N802 - Qt naming
        return self._current

    def tabText(self, index: int) -> str:  # noqa: N802 - Qt naming
        return self._titles[index]

    def select(self, title: str) -> None:
        self._current = self._titles.index(title)


class _Controller:
    def __init__(self, product: str, enabled: bool):
        self._product = product
        self._enabled = enabled

    def product(self) -> str:
        return self._product

    def is_enabled(self) -> bool:
        return self._enabled


class _Owner:
    """Minimal stand-in that reuses the picker's real selection methods."""

    _active_overlay_controller = \
        gui_picker.PickerWindow._active_overlay_controller
    selected_overlay_product = \
        gui_picker.PickerWindow.selected_overlay_product

    def __init__(self, tabs, map_outlook=None, model_outlook=None):
        self._tabs = tabs
        if map_outlook is not None:
            self._map_outlook = map_outlook
        if model_outlook is not None:
            self._model_outlook = model_outlook


TITLES = ("Station Map", "Station List", "Forecast Model",
          "Reanalysis (ERA5)", "Open File")


def _owner(current: str, map_state=("cat", True), model_state=("wind", True)):
    tabs = _Tabs(TITLES)
    tabs.select(current)
    return _Owner(
        tabs,
        map_outlook=_Controller(*map_state) if map_state else None,
        model_outlook=_Controller(*model_state) if model_state else None,
    )


def test_the_forecast_model_tab_wins_while_it_is_in_front():
    """The reported bug: wind selected, categorical drawn."""
    owner = _owner("Forecast Model")
    assert owner.selected_overlay_product() == "wind"


def test_the_station_map_tab_wins_while_it_is_in_front():
    owner = _owner("Station Map")
    assert owner.selected_overlay_product() == "cat"


@pytest.mark.parametrize("product", ["cat", "torn", "wind", "hail", "prob"])
def test_every_hazard_reaches_the_viewer(product):
    owner = _owner("Forecast Model", model_state=(product, True))
    assert owner.selected_overlay_product() == product
    # The viewer reads through its own duck-typed accessor, so pin that too.
    assert _controller_overlay_product(owner) == product


def test_the_tab_in_front_wins_even_with_its_own_overlay_switched_off():
    """Its combo still records the hazard that tab is working on.

    The inset is not gated on the picker map drawing the overlay, so the choice
    should follow the tab regardless of its checkbox.
    """
    owner = _owner("Forecast Model",
                   map_state=("cat", True), model_state=("hail", False))
    assert owner.selected_overlay_product() == "hail"


def test_a_tab_without_an_overlay_prefers_an_enabled_one():
    owner = _owner("Open File",
                   map_state=("cat", False), model_state=("torn", True))
    assert owner.selected_overlay_product() == "torn"


def test_a_tab_without_an_overlay_falls_back_to_a_configured_one():
    owner = _owner("Station List",
                   map_state=("cat", False), model_state=("hail", False))
    assert owner.selected_overlay_product() == "cat"


def test_a_lazily_built_tab_that_does_not_exist_yet_falls_back():
    """The Forecast Model tab is built on first visit, so it can be absent."""
    owner = _owner("Forecast Model", model_state=None)
    assert owner.selected_overlay_product() == "cat"


def test_no_controllers_means_no_preference():
    owner = _owner("Station Map", map_state=None, model_state=None)
    assert owner.selected_overlay_product() is None
    assert _controller_overlay_product(owner) is None


def test_a_missing_tab_surface_is_tolerated():
    """Entry points drive this with partial stand-ins."""
    owner = _Owner(None, map_outlook=_Controller("torn", True))
    assert owner.selected_overlay_product() == "torn"


def test_an_unknown_tab_title_falls_back():
    tabs = _Tabs(("Something New",))
    owner = _Owner(tabs, map_outlook=_Controller("cat", True))
    assert owner._active_overlay_controller() is None
    assert owner.selected_overlay_product() == "cat"


# --------------------------------------------------------------------------- #
# The mapping is keyed by tab title, so a rename would silently disable it.
# --------------------------------------------------------------------------- #
def test_the_mapped_tab_titles_exist_and_own_their_controllers(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog",
        lambda *_args: None)

    picker = gui_picker.PickerWindow()
    for timer in ("_avail_timer", "_catalog_timer"):
        clock = getattr(picker, timer, None)
        if clock is not None:
            clock.stop()
    try:
        titles = {picker._tabs.tabText(index)
                  for index in range(picker._tabs.count())}
        assert set(gui_picker.TAB_OVERLAY_CONTROLLERS) <= titles, (
            "a mapped tab has been renamed, which would silently drop the "
            "overlay choice back to the fixed-order fallback")

        picker._select_tab("Station Map")
        assert picker._active_overlay_controller() is picker._map_outlook

        picker._select_tab("Forecast Model")
        assert picker._active_overlay_controller() is picker._model_outlook
    finally:
        picker.close()
        picker.deleteLater()


def test_the_real_picker_follows_the_tab_in_front(
        qt_app, tmp_path, monkeypatch):
    """End to end on a live window, with both overlays switched on."""
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog",
        lambda *_args: None)

    picker = gui_picker.PickerWindow()
    for timer in ("_avail_timer", "_catalog_timer"):
        clock = getattr(picker, timer, None)
        if clock is not None:
            clock.stop()
    try:
        picker._select_tab("Forecast Model")
        picker._map_outlook.set_enabled(True)
        picker._map_outlook.set_product("cat")
        picker._model_outlook.set_enabled(True)
        picker._model_outlook.set_product("wind")

        assert picker.selected_overlay_product() == "wind"
        assert _controller_overlay_product(picker) == "wind"

        picker._select_tab("Station Map")
        assert picker.selected_overlay_product() == "cat"

        picker._select_tab("Forecast Model")
        assert picker.selected_overlay_product() == "wind"
    finally:
        picker.close()
        picker.deleteLater()
