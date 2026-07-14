"""Multiple-sounding viewer regressions."""

from __future__ import annotations

from types import SimpleNamespace

from sharpmod import gui, gui_picker


class _Viewer:
    def __init__(self):
        self.added = []
        self.titles = []
        self.shown = 0
        self.raised = 0
        self.activated = 0
        self.spc_widget = SimpleNamespace(prof_collections=["collection-1"])

    def isVisible(self):
        return True

    def addProfileCollection(self, collection, **kwargs):
        self.added.append((collection, kwargs))
        self.spc_widget.prof_collections.append(collection)

    def setWindowTitle(self, title):
        self.titles.append(title)

    def showNormal(self):
        self.shown += 1

    def raise_(self):
        self.raised += 1

    def activateWindow(self):
        self.activated += 1


def test_show_sounding_adds_to_active_viewer(monkeypatch):
    viewer = _Viewer()
    picker = SimpleNamespace(
        _viewers=[viewer],
        _combine_soundings_enabled=lambda: True,
        _prune_closed_viewers=lambda: None,
    )
    monkeypatch.setattr(
        gui_picker,
        "compose_interactive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must reuse the active viewer")),
    )

    result = gui.PickerWindow._show_sounding(
        picker, "collection-2", "KOUN", title="Second")

    assert result is viewer
    assert viewer.added == [(
        "collection-2",
        {"focus": True, "check_integrity": False},
    )]
    assert picker._viewers == [viewer]
    assert viewer.titles[-1] == "SHARPpy Reimagined — 2 Soundings"
    assert (viewer.shown, viewer.raised, viewer.activated) == (1, 1, 1)


def test_disabled_combine_mode_composes_a_new_viewer(monkeypatch):
    old_viewer = _Viewer()
    new_viewer = SimpleNamespace()
    picker = SimpleNamespace(
        _viewers=[old_viewer],
        _combine_soundings_enabled=lambda: False,
        _prune_closed_viewers=lambda: None,
        _config=lambda: "config",
    )
    monkeypatch.setattr(
        gui_picker,
        "compose_interactive",
        lambda config, collection, controller, **kwargs: new_viewer,
    )

    # Stop after composition; the rest of the method requires a real QObject.
    new_viewer.setWindowTitle = lambda _title: None
    new_viewer.destroyed = SimpleNamespace(connect=lambda _callback: None)
    result = gui.PickerWindow._show_sounding(
        picker, "collection-2", "KOUN", title="Second")

    assert result is new_viewer
    assert picker._viewers == [old_viewer, new_viewer]
