"""The locator selection gating what actually reaches a sounding's inset."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sharpmod import gui_viewer

UTC = timezone.utc
VALID = datetime(2026, 5, 1, 18, tzinfo=UTC)


class _Collection:
    def __init__(self, valid=VALID):
        self._meta = {"lat": 35.18, "lon": -97.44}
        self._valid = valid

    def getMeta(self, key):  # noqa: N802 - SHARPpy's spelling
        return self._meta.get(key)

    def setMeta(self, key, value):  # noqa: N802 - SHARPpy's spelling
        self._meta[key] = value

    def getCurrentDate(self):  # noqa: N802 - SHARPpy's spelling
        return self._valid


def _raster(key="hrrr_field"):
    return SimpleNamespace(key=key, opacity=0.75)


class _Controller:
    """Stands in for the picker, which is duck-typed by the viewer."""

    def __init__(self):
        self.field_reads = 0

    def selected_model_field(self):
        self.field_reads += 1
        return _raster()

    def selected_overlay_product(self):
        return "cat"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing here may reach a provider; the worker is what would."""
    started: list[dict] = []

    class _Worker:
        def __init__(self, selections, *, valid_time=None, **_kwargs):
            selections = tuple(selections)
            started.append(
                {
                    "valid": valid_time,
                    "families": tuple(item.family for item in selections),
                    "product": selections[0].product if selections else None,
                }
            )
            self.loaded = SimpleNamespace(connect=lambda slot: None)
            self.finished = SimpleNamespace(connect=lambda slot: None)

        def start(self):
            pass

        def requestInterruption(self):  # noqa: N802 - Qt's spelling
            pass

        def deleteLater(self):  # noqa: N802 - Qt's spelling
            pass

    class _LegacyWorker:
        def __init__(self, *_args, **_kwargs):
            self.loaded = SimpleNamespace(connect=lambda slot: None)
            self.finished = SimpleNamespace(connect=lambda slot: None)

        def start(self):
            pass

        def requestInterruption(self):  # noqa: N802 - Qt's spelling
            pass

        def deleteLater(self):  # noqa: N802 - Qt's spelling
            pass

    monkeypatch.setattr("sharpmod.gui_locator_fetch.LocatorOverlayWorker", _Worker)
    monkeypatch.setattr("sharpmod.gui_workers._SpcOutlookWorker", _LegacyWorker)
    monkeypatch.setattr(gui_viewer, "_repaint_locator_insets", lambda win: None)
    return started


class _Window:
    """A real class, because the fetch takes a weak reference to the window."""

    def __init__(self):
        self._sharpmod_overlay_workers = None

    def findChildren(self, *args, **kwargs):  # noqa: N802 - Qt's spelling
        return []


def _run(spec, controller=None, valid=VALID):
    return gui_viewer.start_locator_overlay_fetch(
        _Window(),
        _Collection(valid),
        controller=controller or _Controller(),
        spec=spec,
    )


# --------------------------------------------------------------------------- #
# selection parsing
# --------------------------------------------------------------------------- #
def test_no_spec_means_the_previous_behaviour():
    """A caller with no control of its own keeps what the picker was drawing."""
    assert gui_viewer._locator_selection(None) is None


def test_an_explicit_empty_selection_is_not_the_same_as_silence():
    assert gui_viewer._locator_selection("none") == {}


def test_an_unusable_spec_selects_nothing_rather_than_raising():
    assert gui_viewer._locator_selection("satellite,orbital") == {}


def test_the_selection_keeps_the_chosen_product():
    selection = gui_viewer._locator_selection("risk:torn")

    assert selection["risk"].product == "torn"


def test_explicit_selection_is_remembered_on_the_collection():
    collection = _Collection()
    gui_viewer.start_locator_overlay_fetch(
        _Window(), collection, controller=_Controller(), spec="risk:torn,hrrr:refc"
    )

    assert collection.getMeta("sharpmod_locator_overlay_spec") == (
        "risk:torn,hrrr:refc"
    )


# --------------------------------------------------------------------------- #
# gating
# --------------------------------------------------------------------------- #
def test_the_field_is_carried_only_when_it_was_asked_for():
    controller = _Controller()
    _run("risk", controller=controller)

    assert controller.field_reads == 0, "an unselected field must not be carried"


def test_selecting_the_field_carries_it():
    controller = _Controller()
    _run("risk,hrrr", controller=controller)

    assert controller.field_reads == 1


def test_omitting_the_spec_still_carries_the_field():
    controller = _Controller()
    _run(None, controller=controller)

    assert controller.field_reads == 1


def test_the_outlook_is_fetched_when_selected(_no_network):
    _run("risk")

    assert len(_no_network) == 1
    assert _no_network[0]["valid"] == VALID


def test_the_outlook_is_not_fetched_when_it_was_not_selected(_no_network):
    _run("hrrr")

    assert _no_network == [], "nothing may reach the network unasked"


def test_selecting_nothing_fetches_nothing(_no_network):
    controller = _Controller()
    _run("none", controller=controller)

    assert _no_network == []
    assert controller.field_reads == 0


def test_the_chosen_hazard_reaches_the_worker(_no_network):
    _run("risk:torn")

    assert _no_network[0]["product"] == "torn"


def test_radar_does_not_quietly_fetch_the_outlook_instead(_no_network):
    """Radar displaces the outlook, so selecting it must not fetch one."""
    controller = _Controller()
    _run("radar-site", controller=controller)

    assert _no_network[0]["families"] == ("radar-site",)
    assert controller.field_reads == 0


# --------------------------------------------------------------------------- #
# the hazard the picker has selected
#
# The locator control offers families only, so the spec it produces is a bare
# "risk". The hazard therefore has to come from the picker's outlook selection,
# and it used to be dropped: `product` was computed by the caller but never
# passed down the branch a real GUI takes, so a sounding opened from a tornado
# probability map drew the categorical outlook on its inset.
# --------------------------------------------------------------------------- #
class _HazardController(_Controller):
    def __init__(self, product):
        super().__init__()
        self._product = product

    def selected_overlay_product(self):
        return self._product


@pytest.mark.parametrize("hazard", ["torn", "wind", "hail", "prob", "cat"])
def test_a_bare_risk_spec_follows_the_selected_hazard(_no_network, hazard):
    """Every hazard, not just one -- and on whatever day VALID falls in."""
    _run("risk", controller=_HazardController(hazard))

    assert _no_network[0]["product"] == hazard


def test_a_bare_risk_spec_still_defaults_to_categorical(_no_network):
    """No stated preference keeps the documented categorical fallback."""
    _run("risk", controller=_HazardController(None))

    assert _no_network[0]["product"] is None, (
        "an unstated hazard must stay unstated here; locator_overlay.fetch owns "
        "the categorical default"
    )


def test_a_hazard_named_in_the_spec_outranks_the_map(_no_network):
    """An explicit spec is a deliberate request and must win."""
    _run("risk:hail", controller=_HazardController("torn"))

    assert _no_network[0]["product"] == "hail"


def _convective_day(offset):
    """A valid time inside the convective day ``offset`` days from now.

    Derived from the live clock rather than a fixed stamp because the outlook day
    a time belongs to is measured against *now*. Anchoring on
    ``convective_day_start`` makes the offset exact at any hour, including either
    side of the 12Z boundary.
    """
    from sharpmod import spc_outlook

    start = spc_outlook.convective_day_start(datetime.now(UTC))
    return start + timedelta(days=offset, hours=9)


@pytest.mark.parametrize("outlook_day, offset", [("Day 1", 0), ("Day 2", 1)])
@pytest.mark.parametrize("hazard", ["torn", "wind", "hail"])
def test_hazard_probabilities_reach_the_inset_on_day_one_and_day_two(
    _no_network, hazard, outlook_day, offset
):
    """The days the hazard probabilities are actually issued for.

    This is the case the bug was reported against: selecting the tornado, wind,
    or hail probability and opening a sounding for today or tomorrow drew the
    categorical outlook on the locator inset instead.
    """
    _run(
        "risk",
        controller=_HazardController(hazard),
        valid=_convective_day(offset),
    )

    assert _no_network[0]["product"] == hazard, (
        f"{hazard} must survive to the worker for {outlook_day}"
    )


def test_a_hazard_spc_does_not_publish_falls_back_to_categorical(
    _no_network, monkeypatch
):
    """Rather than trading a usable categorical outlook for an empty inset.

    Availability is stubbed rather than reached through a Day 3 valid time
    because whether a Day 3 hazard resolves depends on the hour of day;
    :mod:`sharpmod.tests.test_spc_outlook` pins the day rules themselves.
    """
    monkeypatch.setattr(
        "sharpmod.spc_outlook.product_publishes",
        lambda *_args, **_kwargs: False,
    )
    _run("risk", controller=_HazardController("torn"))

    assert _no_network[0]["product"] is None


def test_an_unpublished_hazard_named_in_the_spec_is_still_honoured(
    _no_network, monkeypatch
):
    """The availability guard only governs the substitution, not explicit asks."""
    monkeypatch.setattr(
        "sharpmod.spc_outlook.product_publishes",
        lambda *_args, **_kwargs: False,
    )
    _run("risk:torn", controller=_HazardController("hail"))

    assert _no_network[0]["product"] == "torn"


def test_the_selected_hazard_is_not_persisted_onto_the_collection():
    """Reopening a collection must follow the current map, not the first one."""
    from sharpmod.locator_overlay import SELECTION_META_KEY

    collection = _Collection()
    gui_viewer.start_locator_overlay_fetch(
        _Window(),
        collection,
        controller=_HazardController("torn"),
        spec="risk",
    )

    assert collection.getMeta(SELECTION_META_KEY) == "risk"
