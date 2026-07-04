"""Property test for Custom_Panel truncation of over-long configurations.

Feature: sharppy-modernization, Property 24: Custom_Panel truncates
configurations longer than 12 items.

**Validates: Requirements 10.8**

Requirement 10.8 -- a configuration specifying more than 12 items renders only
the first 12 items plus a "more items not shown" indicator.

The property draws configurations with more than :data:`CustomPanel.MAX_ITEMS`
items and asserts that:

* :meth:`CustomPanel.visible_items` returns exactly the first 12 configured
  items (same objects, same order);
* :meth:`CustomPanel.has_overflow` reports the overflow; and
* the panel actually draws exactly 12 value rows plus a single overflow note
  naming the hidden-item count.
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
from sharpmod.viz.custom_panel import CustomPanel, PanelItem


class _RecordingPainter(custom_panel.QtGui.QPainter):
    """A ``QPainter`` that records every ``drawText(rect, flags, text)`` call."""

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


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    original = custom_panel.QtGui.QPainter
    custom_panel.QtGui.QPainter = _RecordingPainter
    try:
        yield app
    finally:
        custom_panel.QtGui.QPainter = original


_LABEL_X = 8


def _draw_texts(panel: CustomPanel):
    _RecordingPainter.calls.clear()
    panel.plotData()
    left_col = [t for (x, _, t) in _RecordingPainter.calls if x == _LABEL_X]
    right_col = [t for (x, _, t) in _RecordingPainter.calls if x != _LABEL_X]
    return left_col, right_col


# Arbitrary-length configurations: shorter than, equal to, and longer than the
# MAX_ITEMS limit so the "overflow iff length > 12" contract is exercised in
# both directions (Requirement 10.8).
_arbitrary_length = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6),
    min_size=1,
    max_size=40,
    unique=True,
)


@settings(max_examples=150)
@given(names=_arbitrary_length)
def test_property_24_truncates_over_twelve(qt_app, names):
    """At most 12 items render; overflow indicator appears iff length > 12."""
    items = [PanelItem(param=n) for n in names]
    over_limit = len(items) > CustomPanel.MAX_ITEMS
    expected_visible = min(len(items), CustomPanel.MAX_ITEMS)

    panel = CustomPanel()
    panel.resize(320, 320)
    panel.configure(items)

    # Logic-level contract (10.8): at most MAX_ITEMS are visible, and they are
    # exactly the first MAX_ITEMS (in order) when the configuration is over the
    # limit.
    visible = panel.visible_items()
    assert len(visible) == expected_visible
    assert len(visible) <= CustomPanel.MAX_ITEMS
    assert visible == items[: CustomPanel.MAX_ITEMS]
    assert panel.has_overflow() is over_limit

    # Render-level contract: exactly ``expected_visible`` value rows are drawn.
    left_col, right_col = _draw_texts(panel)
    assert len(right_col) == expected_visible

    # The "more items not shown" indicator is present iff the config is over the
    # limit, and it names the exact hidden-item count.
    hidden = len(names) - CustomPanel.MAX_ITEMS
    note = f"+{hidden} more items not shown"
    if over_limit:
        assert note in left_col
    else:
        assert not any("more items not shown" in t for t in left_col)

    panel.deleteLater()
