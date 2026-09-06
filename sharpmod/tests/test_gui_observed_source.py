"""The observed-sounding source is chosen, remembered, and actually used.

The rail combo used to be a disabled label: it named one hard-coded fallback
chain and was never stored on the window. These tests hold the replacement to
three promises -- the offered list cannot drift from the provider registry, the
choice survives a restart, and the choice reaches both the availability probe
and the fetch rather than being decorative.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from sharpmod import gui_picker, gui_workers, observations  # noqa: E402
from sharpmod.gui_workers import (  # noqa: E402
    AVAIL_AVAILABLE,
    AVAIL_INSUFFICIENT,
    AVAIL_UNAVAILABLE,
    AVAIL_UNKNOWN,
)


WHEN = datetime(2026, 8, 10, 0)


def _close_picker(picker, app):
    picker._catalog_timer.stop()
    picker._avail_timer.stop()
    picker._model_availability_timer.stop()
    picker._shutdown_model_cache()
    picker.close()
    picker.deleteLater()
    app.processEvents()


@pytest.fixture()
def picker(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    window = gui_picker.PickerWindow()
    try:
        yield window
    finally:
        _close_picker(window, qt_app)


# --------------------------------------------------------------------------- #
# The offered list cannot drift from the provider registry
# --------------------------------------------------------------------------- #
def test_offered_sources_match_the_provider_registry_exactly():
    """The rail restates the registry, so the restatement is pinned here.

    ``gui_picker`` cannot import ``observations`` at module scope -- that pulls
    NumPy in before first paint -- so the source list is a literal. This is the
    guard that keeps the literal honest.
    """
    offered = tuple(key for key, _label, _tooltip in gui_picker.OBSERVED_SOURCES)
    expected = ("auto",) + observations.registered_provider_keys()

    assert offered == expected


def test_every_offered_source_is_distinct_and_described():
    keys = [key for key, _label, _tip in gui_picker.OBSERVED_SOURCES]
    labels = [label for _key, label, _tip in gui_picker.OBSERVED_SOURCES]

    assert len(keys) == len(set(keys))
    assert len(labels) == len(set(labels))
    for key, label, tooltip in gui_picker.OBSERVED_SOURCES:
        assert label.strip(), key
        assert len(tooltip.strip()) > 20, key


def test_the_default_source_is_one_of_the_offered_keys():
    keys = {key for key, _label, _tip in gui_picker.OBSERVED_SOURCES}

    assert gui_picker.DEFAULT_OBSERVED_SOURCE in keys
    assert gui_picker.DEFAULT_OBSERVED_SOURCE == "auto"


def test_igra_is_offered_but_never_reachable_by_the_automatic_chain():
    """Selecting IGRA must be deliberate.

    One IGRA station archive is megabytes at best and tens of megabytes for a
    full record, so it must never be reached by a fallback the user did not ask
    for.
    """
    offered = {key for key, _label, _tip in gui_picker.OBSERVED_SOURCES}

    assert "igra2" in offered
    assert "igra2" not in observations.DEFAULT_PROVIDER_ORDER


def test_source_labels_resolve_and_unknown_keys_pass_through():
    assert (
        gui_picker._observed_source_label("igra2")
        == "NOAA IGRA v2 (deep archive)"
    )
    assert gui_picker._observed_source_label("nope") == "nope"


# --------------------------------------------------------------------------- #
# The combo is real
# --------------------------------------------------------------------------- #
def test_the_source_combo_is_enabled_and_offers_every_source(picker):
    combo = picker._map_source

    assert combo.isEnabled()
    assert combo.count() == len(gui_picker.OBSERVED_SOURCES)
    keys = [combo.itemData(index) for index in range(combo.count())]
    assert keys == [key for key, _l, _t in gui_picker.OBSERVED_SOURCES]


def test_each_source_entry_carries_its_tooltip(picker):
    from qtpy.QtCore import Qt

    combo = picker._map_source
    for index, (_key, _label, tooltip) in enumerate(
            gui_picker.OBSERVED_SOURCES):
        assert combo.itemData(index, Qt.ToolTipRole) == tooltip


def test_a_fresh_window_starts_on_the_default_source(picker):
    assert picker._observed_source() == gui_picker.DEFAULT_OBSERVED_SOURCE


def test_selecting_a_source_reports_it(picker):
    index = picker._map_source.findData("igra2")
    picker._map_source.setCurrentIndex(index)

    assert picker._observed_source() == "igra2"


def test_the_source_is_known_before_the_rail_exists(qt_app, tmp_path,
                                                    monkeypatch):
    """A restored session can fetch before the map tab has been built."""
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    window = gui_picker.PickerWindow()
    try:
        del window._map_source
        assert window._observed_source() == gui_picker.DEFAULT_OBSERVED_SOURCE
    finally:
        _close_picker(window, qt_app)


# --------------------------------------------------------------------------- #
# The choice survives a restart
# --------------------------------------------------------------------------- #
def test_choosing_a_source_persists_it(picker):
    picker._map_source.setCurrentIndex(picker._map_source.findData("iem"))

    assert picker._settings.value("observed/provider", "", str) == "iem"


def test_a_persisted_source_is_restored_by_the_next_window(
        qt_app, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.ini"
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(settings_path))

    first = gui_picker.PickerWindow()
    try:
        first._map_source.setCurrentIndex(first._map_source.findData("igra2"))
    finally:
        _close_picker(first, qt_app)

    second = gui_picker.PickerWindow()
    try:
        assert second._observed_source() == "igra2"
        assert second._map_source.currentData() == "igra2"
    finally:
        _close_picker(second, qt_app)


@pytest.mark.parametrize("stored", ["", "   ", "nonsense", "UWYO_OLD", "igra"])
def test_an_unrecognised_persisted_source_degrades_to_the_default(
        picker, stored):
    picker._settings.setValue("observed/provider", stored)

    assert picker._startup_observed_source() == \
        gui_picker.DEFAULT_OBSERVED_SOURCE


def test_a_persisted_source_is_matched_case_insensitively(picker):
    picker._settings.setValue("observed/provider", "IGRA2")

    assert picker._startup_observed_source() == "igra2"


# --------------------------------------------------------------------------- #
# The preflight cache is keyed by source
# --------------------------------------------------------------------------- #
def test_the_preflight_cache_key_includes_the_source(picker):
    key = picker._observed_cache_key("72357", WHEN)

    assert key[0] == gui_picker.DEFAULT_OBSERVED_SOURCE
    assert key[1] == "72357"


def test_switching_source_changes_the_cache_key_for_the_same_request(picker):
    before = picker._observed_cache_key("72357", WHEN)
    picker._map_source.setCurrentIndex(picker._map_source.findData("igra2"))
    after = picker._observed_cache_key("72357", WHEN)

    assert before != after
    assert before[1:] == after[1:], "only the source component should differ"


def test_a_cached_probe_from_one_source_cannot_answer_another(picker,
                                                             monkeypatch):
    """This is the bug the source component exists to prevent.

    Station and time are identical across a source switch, so without the
    source in the key the UWyo entry would still hit and the user would be
    shown a UWyo sounding after asking for IGRA.
    """
    from types import SimpleNamespace

    fetched = SimpleNamespace(
        profile=object(), station_id="72357", provider="uwyo"
    )
    picker._observed_profile_cache[
        picker._observed_cache_key("72357", WHEN)
    ] = (fetched, "Available (80 levels)", "72357 — Norman")

    picker._map_source.setCurrentIndex(picker._map_source.findData("igra2"))

    assert picker._observed_profile_cache.get(
        picker._observed_cache_key("72357", WHEN)
    ) is None


def test_the_cache_key_still_normalises_timezone_and_case(picker):
    aware = datetime(2026, 8, 10, 0, tzinfo=timezone.utc)

    assert picker._observed_cache_key("72357", aware) == \
        picker._observed_cache_key("  72357  ", WHEN)


# --------------------------------------------------------------------------- #
# The choice reaches the workers
# --------------------------------------------------------------------------- #
class _NullSignal:
    def connect(self, _handler):
        pass


def _recording_worker(captured):
    """A stand-in exposing everything the picker touches on a worker."""

    class _Worker:
        checked = _NullSignal()
        finished_ok = _NullSignal()
        failed = _NullSignal()
        finished = _NullSignal()

        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)

        def isRunning(self):
            return False

        def start(self):
            pass

        def deleteLater(self):
            pass

    return _Worker


def test_the_availability_probe_is_given_the_selected_source(picker,
                                                             monkeypatch):
    captured = {}
    monkeypatch.setattr(
        gui_picker, "_AvailabilityWorker", _recording_worker(captured)
    )
    picker._map_source.setCurrentIndex(picker._map_source.findData("igra2"))
    picker._avail_request = ("72357", WHEN, picker._map_avail, None)

    picker._run_pending_availability()

    assert captured.get("provider") == "igra2"


def test_the_fetch_is_given_the_selected_source(picker, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        gui_picker, "_FetchWorker", _recording_worker(captured)
    )
    picker._map_source.setCurrentIndex(picker._map_source.findData("iem"))

    picker._start_fetch("72357", WHEN)

    assert captured.get("provider") == "iem"


def test_the_fetch_status_line_names_the_chosen_archive(picker, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        gui_picker, "_FetchWorker", _recording_worker(captured)
    )
    picker._map_source.setCurrentIndex(picker._map_source.findData("igra2"))

    picker._start_fetch("72357", WHEN)

    assert "IGRA" in picker.statusBar().currentMessage()


# --------------------------------------------------------------------------- #
# Worker routing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("provider", ["auto", "uwyo", "", None])
def test_the_default_sources_keep_the_uwyo_probe(provider):
    assert gui_workers._uses_uwyo_probe(provider) is True


@pytest.mark.parametrize("provider", ["iem", "igra2", "something"])
def test_other_sources_use_the_provider_probe(provider):
    assert gui_workers._uses_uwyo_probe(provider) is False


def test_the_igra_availability_probe_refuses_the_full_record_archive():
    """A probe runs per station click, so it must stay on the small archive."""
    provider = gui_workers._availability_provider("igra2")

    assert isinstance(provider, observations.IGRAObservedProvider)
    assert provider._allow_full_record is False


def test_other_sources_probe_with_the_registered_provider():
    assert isinstance(
        gui_workers._availability_provider("iem"),
        observations.IEMObservedProvider,
    )
    assert gui_workers._availability_provider("nope") is None


def test_a_refused_large_download_is_recognised():
    from sharpmod.io.igra2 import IGRAFullRecordRequiredError

    try:
        try:
            raise IGRAFullRecordRequiredError("needs the full record")
        except IGRAFullRecordRequiredError as cause:
            raise observations.ObservedUnavailableError("declined") from cause
    except observations.ObservedUnavailableError as exc:
        assert gui_workers._declined_large_download(exc) is True


def test_an_ordinary_missing_sounding_is_not_a_refused_download():
    exc = observations.ObservedUnavailableError("nothing archived")

    assert gui_workers._declined_large_download(exc) is False


def _run_provider_probe(monkeypatch, error=None, result=None, station=None):
    class _Stub:
        def fetch(self, _station, _when):
            if error is not None:
                raise error
            return result

    monkeypatch.setattr(
        gui_workers, "_availability_provider", lambda _key: _Stub()
    )
    worker = gui_workers._AvailabilityWorker(
        "72357", WHEN, 1, station=station, provider="igra2"
    )
    emitted = []
    worker.checked.connect(lambda *args: emitted.append(args))
    worker.run()
    assert len(emitted) == 1
    return emitted[0]


def test_a_refused_download_reports_not_checked_rather_than_unavailable(
        qt_app, monkeypatch):
    """Red "no sounding" would be a lie about a date the archive likely has."""
    from sharpmod.io.igra2 import IGRAFullRecordRequiredError

    try:
        raise IGRAFullRecordRequiredError("needs the full record")
    except IGRAFullRecordRequiredError as cause:
        error = observations.ObservedUnavailableError("declined")
        error.__cause__ = cause

    _query, _valid, status, message, _label, fetched = _run_provider_probe(
        monkeypatch, error=error)

    assert status == AVAIL_UNKNOWN
    assert "not checked" in message.casefold()
    assert fetched is None


@pytest.mark.parametrize("error,expected", [
    (observations.ObservedStationError("no such station"), AVAIL_UNAVAILABLE),
    (observations.ObservedUnavailableError("nothing"), AVAIL_UNAVAILABLE),
    (observations.ObservedRetrievalError("offline"), AVAIL_UNAVAILABLE),
    (observations.ObservedParseError("garbled"), AVAIL_INSUFFICIENT),
    (RuntimeError("boom"), AVAIL_UNAVAILABLE),
])
def test_provider_probe_failures_map_onto_availability_statuses(
        qt_app, monkeypatch, error, expected):
    _query, _valid, status, _message, _label, fetched = _run_provider_probe(
        monkeypatch, error=error)

    assert status == expected
    assert fetched is None


def test_a_successful_provider_probe_reports_its_own_provider(qt_app,
                                                              monkeypatch):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        pres=[1000.0 - 50.0 * index for index in range(18)],
        tmpc=[20.0 - index for index in range(18)],
        dwpc=[15.0 - index for index in range(18)],
        wspd=[10.0 + index for index in range(18)],
    )
    result = SimpleNamespace(
        profile=profile,
        station_id="USM00072357",
        provider="igra2",
        metadata={"station_name": "NORMAN/MAX WESTHEIMER A; OK."},
    )

    _query, _valid, status, _message, label, fetched = _run_provider_probe(
        monkeypatch, result=result)

    assert status == AVAIL_AVAILABLE
    assert fetched.provider == "igra2"
    assert fetched.station_id == "USM00072357"
    assert fetched.profile is profile
    assert "USM00072357" in label


def test_an_unsupported_source_is_reported_without_crashing(qt_app,
                                                            monkeypatch):
    monkeypatch.setattr(
        gui_workers, "_availability_provider", lambda _key: None
    )
    worker = gui_workers._AvailabilityWorker(
        "72357", WHEN, 1, provider="mystery"
    )
    emitted = []
    worker.checked.connect(lambda *args: emitted.append(args))

    worker.run()

    assert emitted[0][2] == AVAIL_UNAVAILABLE
    assert "mystery" in emitted[0][3]


def test_the_probe_labels_the_station_from_its_record_on_failure(qt_app,
                                                                 monkeypatch):
    _query, _valid, _status, _message, label, _fetched = _run_provider_probe(
        monkeypatch,
        error=observations.ObservedUnavailableError("nothing"),
        station={"id": "72357", "name": "OUN Norman, OK"},
    )

    assert "72357" in label
    assert "Norman" in label


# --------------------------------------------------------------------------- #
# Fetch provider chains
# --------------------------------------------------------------------------- #
def _chain_types(provider):
    worker = gui_workers._FetchWorker("72357", WHEN, provider=provider)
    _query, providers = worker._resolve_providers()
    return [type(item) for item in providers]


def test_auto_keeps_the_established_uwyo_then_iem_fallback():
    assert _chain_types("auto") == [
        observations.UWyoObservedProvider,
        observations.IEMObservedProvider,
    ]


@pytest.mark.parametrize("provider,expected", [
    ("uwyo", observations.UWyoObservedProvider),
    ("iem", observations.IEMObservedProvider),
    ("igra2", observations.IGRAObservedProvider),
])
def test_a_named_source_is_used_alone(provider, expected):
    """A deliberate choice must not be answered by a different archive."""
    assert _chain_types(provider) == [expected]


def test_the_fetch_query_is_seeded_only_for_uwyo():
    """IGRA resolves a WMO number itself; UWyo needs its catalogue ``src``."""
    uwyo = gui_workers._FetchWorker(
        "72357", WHEN, station={"id": "72357", "name": "Norman", "src": "BUFR"},
        provider="uwyo",
    )
    igra = gui_workers._FetchWorker(
        "72357", WHEN, station={"id": "72357", "name": "Norman", "src": "BUFR"},
        provider="igra2",
    )

    assert uwyo._resolve_providers()[0] == "72357"
    assert igra._resolve_providers()[0] == "72357"


def test_the_fetch_worker_defaults_to_the_automatic_chain():
    worker = gui_workers._FetchWorker("72357", WHEN)

    assert worker._provider == gui_workers.PROVIDER_AUTO
