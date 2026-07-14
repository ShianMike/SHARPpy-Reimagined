"""Maximum Parcel Level display regressions."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtWidgets
from qtpy.QtCore import QRect

from sharpmod.viz.index_board import IndexBoard
from sharpmod.viz.skew import parcel_level_markers


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_parcel_level_markers_include_mpl():
    pcl = SimpleNamespace(
        lclpres=900.0,
        lfcpres=800.0,
        elpres=250.0,
        mplpres=180.0,
    )

    assert parcel_level_markers(pcl) == [
        ("LCL", 900.0),
        ("LFC", 800.0),
        ("EL", 250.0),
        ("MPL", 180.0),
    ]


class _Painter:
    def setFont(self, *_args):
        pass

    def setPen(self, *_args):
        pass

    def drawLine(self, *_args):
        pass


def test_index_board_parcel_table_includes_mpl_height(qt_app):
    board = IndexBoard()
    board.pcl_types = ["SFC"]
    board.sp = SimpleNamespace(
        sfcpcl=SimpleNamespace(
            bplus=1500.0,
            bminus=-25.0,
            lclhght=950.0,
            li5=-4.0,
            lfchght=1400.0,
            elhght=12000.0,
            mplhght=14500.0,
        )
    )
    calls = []
    board._text = lambda _qp, _rect, text, *_args, **_kwargs: calls.append(text)

    board._col_conv(_Painter(), QRect(0, 0, 800, 480), 20)

    assert calls[:8] == ["PCL", "CAPE", "CINH", "LCL", "LI", "LFC", "EL", "MPL"]
    assert calls[8:16] == ["SFC", "1500", "-25", "950", "-4", "1400", "12000", "14500"]

    board.deleteLater()
