"""Fast contracts for the generic locator-overlay background worker."""

from __future__ import annotations

from datetime import datetime, timezone

from sharpmod import locator_overlay
from sharpmod.gui_locator_fetch import LocatorOverlayWorker


def test_worker_fetches_resolved_families_and_emits_layers(qt_app, monkeypatch):
    selections = locator_overlay.parse("risk:torn,hrrr:refc")
    calls = []
    layers = []

    def fetch(selection, **kwargs):
        calls.append((selection.family, selection.product, kwargs))
        return {"family": selection.family}

    monkeypatch.setattr(locator_overlay, "fetch", fetch)
    worker = LocatorOverlayWorker(
        selections,
        lat=35.2,
        lon=-97.4,
        valid_time=datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
        run=datetime(2026, 9, 10, 6, tzinfo=timezone.utc),
        fxx=6,
    )
    worker.loaded.connect(lambda key, layer: layers.append((key, layer)))

    worker.run()

    assert [(family, product) for family, product, _kwargs in calls] == [
        ("risk", "torn"),
        ("hrrr", "refc"),
    ]
    assert [key for key, _layer in layers] == ["spc_outlook", "hrrr_field"]
    assert all(call[2]["should_cancel"]() is False for call in calls)
