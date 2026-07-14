"""Mounted-product refresh regressions."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sharpmod.viz import SPCWindow as spc_window


class _Recorder:
    def __init__(self, method):
        self.calls = []
        setattr(self, method, lambda *args: self.calls.append(args))


def test_refresh_mounted_products_uses_current_profile(monkeypatch):
    derived = object()
    monkeypatch.setattr(spc_window, "_derived_profile", lambda _prof: derived)
    board = _Recorder("setData")
    stream = _Recorder("setProf")
    redraws = []
    sound = SimpleNamespace(
        clearData=lambda: redraws.append("clear"),
        plotData=lambda: redraws.append("plot"),
        update=lambda: redraws.append("update"),
    )
    sw = SimpleNamespace(
        default_prof="focused",
        index_board=board,
        streamwiseness=stream,
        sound=sound,
    )

    spc_window._refresh_mounted_products(sw)

    assert board.calls == [("focused", derived)]
    assert stream.calls == [("focused",)]
    assert sound._sharpmod_derived_profile is derived
    assert redraws == ["clear", "plot", "update"]


def test_refresh_mounted_products_tolerates_unmounted_widgets(monkeypatch):
    monkeypatch.setattr(spc_window, "_derived_profile", lambda prof: prof)
    sw = SimpleNamespace(default_prof="focused")

    spc_window._refresh_mounted_products(sw)
