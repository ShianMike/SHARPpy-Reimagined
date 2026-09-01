"""Coverage for the date-picker calendar popup.

The popup previously elided every day number to an ellipsis, because the chrome
style sheet's generic ``QTableView::item`` padding also matches a
``QCalendarWidget``'s day cells and left too little room for two digits. It also
showed the neighbouring months' days. Both are asserted here against the painted
result rather than the widget's configuration, since the configuration was never
what was wrong.
"""

from __future__ import annotations

from collections import Counter

import pytest
from qtpy.QtCore import QDate, QRect
from qtpy.QtGui import QColor, QPainter, QPixmap
from qtpy.QtWidgets import QCalendarWidget, QDateEdit

from sharpmod.gui_common import MonthCalendar, install_month_calendar

#: Large enough for two digits, small enough to have provoked the elision.
CELL = (44, 26)
#: Magenta never appears in a painted cell, so it proves the cell drew its own
#: background rather than leaving the surface untouched.
SENTINEL = "#ff00ff"


@pytest.fixture
def calendar(qt_app):
    edit = QDateEdit()
    edit.setDisplayFormat("yyyy-MM-dd")
    edit.setCalendarPopup(True)
    widget = install_month_calendar(edit)
    widget.setMinimumDate(QDate(2020, 1, 1))
    widget.setMaximumDate(QDate(2030, 12, 31))
    # April 2026 begins on a Wednesday, so the grid also spans 29-31 March and
    # 1-9 May: exactly the cells that must stay blank.
    widget.setCurrentPage(2026, 4)
    widget.setSelectedDate(QDate(2026, 4, 28))
    try:
        yield widget
    finally:
        edit.deleteLater()


def _paint(calendar, date) -> Counter:
    """Return a histogram of the pixels ``paintCell`` produces for ``date``."""
    pixmap = QPixmap(*CELL)
    pixmap.fill(QColor(SENTINEL))
    painter = QPainter(pixmap)
    try:
        calendar.paintCell(painter, QRect(0, 0, *CELL), date)
    finally:
        painter.end()
    image = pixmap.toImage()
    return Counter(
        image.pixel(x, y)
        for y in range(image.height())
        for x in range(image.width())
    )


def _ink(calendar, date) -> float:
    """Fraction of the cell that is not its dominant (background) colour."""
    counts = _paint(calendar, date)
    dominant, dominant_count = counts.most_common(1)[0]
    assert dominant != QColor(SENTINEL).rgb(), \
        f"{date.toString('yyyy-MM-dd')}: the cell background was not painted"
    return 1.0 - dominant_count / sum(counts.values())


def _background(calendar, date) -> int:
    return _paint(calendar, date).most_common(1)[0][0]


def test_the_popup_is_installed(qt_app):
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    widget = install_month_calendar(edit)
    try:
        assert isinstance(widget, MonthCalendar)
        assert edit.calendarWidget() is widget
    finally:
        edit.deleteLater()


def test_every_day_of_the_shown_month_renders(calendar):
    for day in range(1, 31):
        date = QDate(2026, 4, day)
        assert _ink(calendar, date) > 0.01, \
            f"April {day} rendered blank"


def test_two_digit_days_are_not_elided(calendar):
    """An ellipsis covers the same area whatever the number; two glyphs do not."""
    single = _ink(calendar, QDate(2026, 4, 5))
    double = _ink(calendar, QDate(2026, 4, 28))
    assert double > single, \
        "a two-digit day should cover more pixels than a single digit"


@pytest.mark.parametrize("date", [
    QDate(2026, 3, 29), QDate(2026, 3, 30), QDate(2026, 3, 31),
    QDate(2026, 5, 1), QDate(2026, 5, 5), QDate(2026, 5, 9),
])
def test_adjacent_month_days_are_blank(calendar, date):
    assert _ink(calendar, date) == 0.0, \
        f"{date.toString('yyyy-MM-dd')} is outside April and must not draw"


def test_the_selected_day_is_highlighted(calendar):
    calendar.setSelectedDate(QDate(2026, 4, 28))
    selected = _background(calendar, QDate(2026, 4, 28))
    calendar.setSelectedDate(QDate(2026, 4, 15))
    unselected = _background(calendar, QDate(2026, 4, 28))
    assert selected != unselected


def test_a_day_outside_the_allowed_range_still_shows_its_number(calendar):
    """Dimmed rather than hidden, so the month stays readable."""
    calendar.setMaximumDate(QDate(2026, 4, 15))
    assert _ink(calendar, QDate(2026, 4, 20)) > 0.01


def test_paging_moves_which_month_is_drawn(calendar):
    calendar.setCurrentPage(2026, 5)
    assert calendar.monthShown() == 5
    assert _ink(calendar, QDate(2026, 4, 30)) == 0.0
    assert _ink(calendar, QDate(2026, 5, 30)) > 0.01


def test_leap_day_renders(calendar):
    calendar.setCurrentPage(2028, 2)
    assert _ink(calendar, QDate(2028, 2, 29)) > 0.01
    assert _ink(calendar, QDate(2028, 3, 1)) == 0.0


def test_headers_cannot_elide(calendar):
    """The header views are painted by Qt, so they are configured to fit.

    Week numbers are dropped, which also returns their width to the day columns,
    and single-letter weekday names fit whatever the popup ends up being.
    """
    assert calendar.verticalHeaderFormat() == \
        QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
    assert calendar.horizontalHeaderFormat() == \
        QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames


def test_the_whole_popup_paints(calendar):
    calendar.resize(280, 200)
    assert not calendar.grab().isNull()
