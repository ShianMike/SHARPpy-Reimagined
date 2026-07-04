"""Property test for Custom_Panel order preservation and unresolved rendering.

Feature: sharppy-modernization, Property 23: Custom_Panel preserves order and
renders unresolved items visibly.

**Validates: Requirements 10.1, 10.3**

Requirement 10.1 -- given an ordered configuration of up to 12 items, the panel
renders those items *in the configured order*.

Requirement 10.3 -- a configured name that does not resolve to an available
Profile parameter renders an *unresolved-parameter indicator*
(:data:`UNRESOLVED_STR`) in that item's position, and all remaining resolved
items are still rendered (the unresolved item is never dropped silently).

The panel paints its rows onto a backing pixmap via ``QPainter``. To observe
*what* text is drawn and *in what order* without brittle pixel inspection, the
module-scoped fixture swaps in a ``QPainter`` subclass that records every
``drawText`` call (its rect x/y and the text). Because each row draws its label
in the left column and its value in the right column with an increasing ``y``,
the recorded call order reflects the configured item order.
"""

from __future__ import annotations

import os

# Ensure headless Qt before qtpy imports a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from qtpy import QtWidgets

from sharpmod.viz import custom_panel
from sharpmod.viz.custom_panel import CustomPanel, PanelItem, UNRESOLVED_STR


# ---------------------------------------------------------------------------
# Recording QPainter
# ---------------------------------------------------------------------------

class _RecordingPainter(custom_panel.QtGui.QPainter):
    """A ``QPainter`` that records every ``drawText(rect, flags, text)`` call.

    Records ``(x, y, text)`` tuples on the class-level :data:`calls` list while
    still performing the real paint so the widget behaves normally.
    """

    calls: list[tuple[int, int, str]] = []

    def drawText(self, *args, **kwargs):  # noqa: N802 (Qt naming)
        rect = None
        text = None
        for a in args:
            if isinstance(a, str):
                text = a
            elif hasattr(a, "x") and hasattr(a, "y") and not isinstance(a, (int, float)):
                rect = a
        if text is not None and rect is not None:
            _RecordingPainter.calls.append((rect.x(), rect.y(), text))
        return super().drawText(*args, **kwargs)


class _ProfStub:
    """Resolves every attribute to a value except a designated missing name."""

    def __init__(self, unresolved_name: str):
        object.__setattr__(self, "_unresolved_name", unresolved_name)

    def __getattr__(self, name):
        if name == object.__getattribute__(self, "_unresolved_name"):
            raise AttributeError(name)
        return 42.0


@pytest.fixture(scope="module")
def qt_app():
    """Offscreen QApplication with the recording painter installed."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    original = custom_panel.QtGui.QPainter
    custom_panel.QtGui.QPainter = _RecordingPainter
    try:
        yield app
    finally:
        custom_panel.QtGui.QPainter = original


# Column x-origin of a label draw (self.lpad); the title and overflow note also
# start here, so they are filtered out by text.
_LABEL_X = 8


def _capture(panel: CustomPanel):
    """Return ``(labels, values)`` texts in draw order for the current config."""
    _RecordingPainter.calls.clear()
    panel.plotData()
    left_col = [(y, t) for (x, y, t) in _RecordingPainter.calls if x == _LABEL_X]
    right_col = [(y, t) for (x, y, t) in _RecordingPainter.calls if x != _LABEL_X]
    # Drop the title ("Custom") which is the first left-column draw.
    labels = [t for (_, t) in left_col if t != panel.title]
    values = [t for (_, t) in right_col]
    return labels, values


# ---------------------------------------------------------------------------
# Property 23
# ---------------------------------------------------------------------------

_names = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6),
    min_size=1,
    max_size=CustomPanel.MAX_ITEMS,
    unique=True,
)


@settings(max_examples=150)
@given(data=st.data())
def test_property_23_order_and_unresolved(qt_app, data):
    """Configured items render in order; an unresolved item shows "??" in place."""
    names = data.draw(_names)
    missing_idx = data.draw(st.integers(min_value=0, max_value=len(names) - 1))
    missing_name = names[missing_idx]

    items = [PanelItem(param=n) for n in names]
    panel = CustomPanel()
    panel.resize(320, 260)
    panel.configure(items)
    panel.setProf(_ProfStub(missing_name))

    labels, values = _capture(panel)

    # 10.1: every configured item is rendered, in the configured order. A label
    # may resolve from the parameter registry (or fall back to the raw name), so
    # compare against the panel's own resolved + elided label for each item, in
    # configuration order.
    column_w = (panel.width() - panel.lpad - panel.rpad) // 2
    expected_labels = [
        panel._elide(panel._resolve_label_unit(it)[0], column_w, panel.label_font)
        for it in items
    ]
    assert labels == expected_labels, "labels must render in configured order without drops"

    # One value per item, in order (no item dropped).
    assert len(values) == len(names)

    # 10.3: the unresolved item shows the unresolved indicator in its position.
    assert values[missing_idx] == UNRESOLVED_STR

    # 10.3: all *other* items are still rendered as resolved values (not "??").
    for i, v in enumerate(values):
        if i != missing_idx:
            assert v != UNRESOLVED_STR, f"resolved item {i} was dropped/misrendered"

    panel.deleteLater()
