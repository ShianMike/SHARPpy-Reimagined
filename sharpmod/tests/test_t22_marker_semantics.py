"""T22 station, report, and loaded-profile marker contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QPointF

from sharpmod import gui_maps, gui_picker, storm_reports


UTC = timezone.utc
WHEN = datetime(2026, 9, 21, 0, tzinfo=UTC)


def _coincident_report_layer():
    reports = (
        storm_reports.StormReport(
            storm_reports.HAZARDS["H"], WHEN, 35.22, -97.44, 1.25
        ),
        storm_reports.StormReport(
            storm_reports.HAZARDS["T"], WHEN, 35.22, -97.44, None
        ),
    )
    return storm_reports.layer_from_reports(
        reports, span_deg=10.0, around=WHEN
    )


def test_station_report_and_profile_overlaps_are_all_reachable(qt_app):
    widget = gui_maps.StationMapWidget(
        [{"id": "KOUN", "name": "Norman", "lat": 35.22, "lon": -97.44}]
    )
    widget.resize(720, 480)
    widget.set_extent(-102.0, -92.0, 31.0, 40.0, pad=0)
    widget.set_overlay(storm_reports.OVERLAY_KEY, _coincident_report_layer())
    widget.set_loaded_profile_points(
        (
            {
                "id": "observed-profile",
                "label": "KOUN observed",
                "lat": 35.22,
                "lon": -97.44,
                "kind": "observed",
                "active": True,
                "valid_time": WHEN,
            },
            {
                "id": "model-profile",
                "label": "HRRR KOUN",
                "lat": 35.22,
                "lon": -97.44,
                "kind": "model",
                "valid_time": WHEN,
            },
        )
    )
    try:
        point = widget._to_px(-97.44, 35.22, widget._proj())
        choices = widget.marker_choices_at(point.x(), point.y())

        assert {choice["kind"] for choice in choices} == {
            "station",
            "report",
            "profile",
        }
        assert sum(choice["kind"] == "report" for choice in choices) == 2
        assert sum(choice["kind"] == "profile" for choice in choices) == 2
        assert sum(choice["kind"] == "station" for choice in choices) == 1
        assert len({choice["id"] for choice in choices}) == len(choices)
    finally:
        widget.close()


def test_overlap_chooser_lists_every_marker_and_can_select_station(
    qt_app, monkeypatch
):
    widget = gui_maps.StationMapWidget(
        [{"id": "KOUN", "name": "Norman", "lat": 35.22, "lon": -97.44}]
    )
    widget.resize(720, 480)
    widget.set_extent(-102.0, -92.0, 31.0, 40.0, pad=0)
    widget.set_overlay(storm_reports.OVERLAY_KEY, _coincident_report_layer())
    widget.set_loaded_profile_points(
        ({"id": "profile", "label": "KOUN observed", "lat": 35.22,
          "lon": -97.44, "kind": "observed"},)
    )
    opened = []

    class Action:
        def __init__(self, label):
            self.label = label

        def setToolTip(self, _text):  # noqa: N802 - Qt API
            pass

        def setStatusTip(self, _text):  # noqa: N802 - Qt API
            pass

    class Menu:
        def __init__(self, _parent):
            self.actions = []
            opened.append(self)

        def setTitle(self, title):  # noqa: N802 - Qt API
            self.title = title

        def addAction(self, label):  # noqa: N802 - Qt API
            action = Action(label)
            self.actions.append(action)
            return action

        def exec_(self, _where):
            return next(
                action for action in self.actions
                if action.label.startswith("Station KOUN")
            )

    from sharpmod.ui.maps import station_interactions

    monkeypatch.setattr(station_interactions, "QMenu", Menu)
    selected = []
    widget.stationSelected.connect(selected.append)
    try:
        point = widget._to_px(-97.44, 35.22, widget._proj())
        event = SimpleNamespace(globalPos=lambda: QPoint(20, 30))

        handled = widget._describe_marker_overlay_at(
            point, event, include_station=True
        )

        assert handled is True
        assert opened[0].title == "Choose marker (4)"
        assert len(opened[0].actions) == 4
        assert selected == ["KOUN"]
    finally:
        widget.close()


def test_progressive_station_labels_keep_protected_items_visible(qt_app):
    stations = [
        {"id": "KSEL", "name": "Selected", "lat": 10.0, "lon": -150.0},
        {"id": "KHOV", "name": "Hovered", "lat": 11.0, "lon": -149.0},
        {"id": "KPIN", "name": "Pinned", "lat": 12.0, "lon": -148.0},
        {"id": "KORD", "name": "Ordinary", "lat": 13.0, "lon": -147.0},
    ]
    widget = gui_maps.StationMapWidget(stations)
    widget.resize(900, 600)
    widget.set_selected("KSEL")
    widget._hover_id = "KHOV"
    widget._inspection = {"kind": "station", "station_id": "KPIN"}
    try:
        widget.set_extent(-180.0, -100.0, -10.0, 30.0, pad=0)
        wide = {item[3]: item for item in widget.station_labels()}

        assert set(wide) == {"KSEL", "KHOV", "KPIN"}
        assert all(item[4] for item in wide.values())

        widget.set_extent(-152.0, -145.0, 7.0, 15.0, pad=0)
        local = {item[3]: item for item in widget.station_labels()}

        assert set(local) == {"KSEL", "KHOV", "KPIN", "KORD"}
        assert all(" " in item[2] for item in local.values())
    finally:
        widget.close()


def test_clustered_protected_station_labels_are_repositioned(qt_app):
    from qtpy.QtWidgets import QApplication

    from sharpmod.gui_theme import apply_theme

    stations = [
        {"id": "KSEL", "name": "Selected Airport", "lat": 35.20,
         "lon": -97.40},
        {"id": "KHOV", "name": "Hovered Airport", "lat": 35.22,
         "lon": -97.38},
        {"id": "KPIN", "name": "Pinned Airport", "lat": 35.24,
         "lon": -97.36},
    ]
    widget = gui_maps.StationMapWidget(stations)
    widget.resize(900, 600)
    widget.set_extent(-100.0, -94.0, 32.0, 38.0, pad=0)
    widget.set_selected("KSEL")
    widget._hover_id = "KHOV"
    widget._inspection = {"kind": "station", "station_id": "KPIN"}
    try:
        apply_theme(QApplication.instance(), color_style="standard",
                    text_scale=200)
        labels = widget.station_labels()
        assert {item[3] for item in labels} == {"KSEL", "KHOV", "KPIN"}
        widths = gui_maps._estimate_label_widths(item[2] for item in labels)
        height = gui_maps._estimated_label_height(15.0)
        rects = [
            (x + 6.0, y - height + 1.0, widths[label] + 6.0, height)
            for x, y, label, *_rest in labels
        ]
        for index, (x, y, width, rect_height) in enumerate(rects):
            for ox, oy, other_width, other_height in rects[index + 1:]:
                assert (
                    x + width + 3.0 <= ox
                    or ox + other_width + 3.0 <= x
                    or y + rect_height + 3.0 <= oy
                    or oy + other_height + 3.0 <= y
                )
    finally:
        apply_theme(QApplication.instance(), color_style="standard",
                    text_scale=100)
        widget.close()


def test_station_text_avoids_the_full_town_label_rectangle(qt_app, monkeypatch):
    """Long station/town strings may not overlap merely because anchors differ."""
    widget = gui_maps.StationMapWidget([
        {"id": "KORD", "name": "Ordinary Airport", "lat": 35.0, "lon": -97.0},
    ])
    widget.resize(900, 600)
    widget.set_extent(-100.0, -93.0, 32.0, 39.0, pad=0)
    monkeypatch.setattr(
        widget, "cached_place_labels",
        lambda: ((100.0, 100.0, "A Very Long Municipality Name"),),
    )
    monkeypatch.setattr(widget, "_to_px", lambda *_args: QPointF(210.0, 100.0))
    try:
        assert widget.station_labels() == ()
    finally:
        widget.close()


def test_loaded_profile_key_uses_distinct_source_shapes(qt_app):
    widget = gui_maps.PointMapWidget()
    widget.set_loaded_profile_points(
        (
            {"id": "o", "label": "Observed", "lat": 35, "lon": -97,
             "kind": "observed"},
            {"id": "m", "label": "Model", "lat": 36, "lon": -98,
             "kind": "model"},
            {"id": "i", "label": "Imported", "lat": 37, "lon": -99,
             "kind": "imported"},
        )
    )
    try:
        key = widget.profile_marker_key()

        assert [row[0] for row in key] == [
            "Observed",
            "Model/reanalysis",
            "Imported",
        ]
        assert {row[3] for row in key} == {"triangle", "square", "diamond"}
    finally:
        widget.close()


def test_picker_payload_uses_real_collection_metadata_and_focus():
    class Collection:
        def __init__(self, **metadata):
            self.metadata = metadata

        def getMeta(self, key):  # noqa: N802 - ProfileCollection API
            return self.metadata.get(key)

        def getCurrentDate(self):  # noqa: N802 - ProfileCollection API
            return WHEN

    observed = Collection(
        loc="KOUN", lat=35.22, lon=-97.44, observed=True, source="RAOB"
    )
    model = Collection(
        loc="Norman", lat=35.22, lon=-97.44, observed=False, model="HRRR"
    )
    owner = SimpleNamespace(
        _viewers=(
            SimpleNamespace(
                spc_widget=SimpleNamespace(
                    prof_collections=(observed, model), pc_idx=1
                )
            ),
        )
    )
    owner._profile_collection_meta = (
        lambda collection, key, default=None:
        gui_picker.PickerWindow._profile_collection_meta(collection, key, default)
    )

    payload = gui_picker.PickerWindow._loaded_profile_marker_payload(owner)
    by_kind = {item["kind"]: item for item in payload}

    assert by_kind["observed"]["active"] is False
    assert by_kind["observed"]["source"] == "RAOB"
    assert by_kind["model"]["active"] is True
    assert by_kind["model"]["source"] == "HRRR"


def test_viewer_focus_changes_refresh_loaded_profile_markers(qt_app):
    refreshes = []

    class Widget:
        def updateProfs(self):  # noqa: N802 - SPCWidget API
            return "updated"

    class Destroyed:
        def connect(self, callback):
            self.callback = callback

    widget = Widget()
    viewer = SimpleNamespace(spc_widget=widget, destroyed=Destroyed())
    owner = SimpleNamespace(
        _refresh_loaded_profile_markers=lambda: refreshes.append("refreshed"),
        _viewer_closed_refresh=lambda: None,
    )

    gui_picker.PickerWindow._watch_loaded_profile_viewer(owner, viewer)
    assert widget.updateProfs() == "updated"
    qt_app.processEvents()

    assert refreshes == ["refreshed"]
