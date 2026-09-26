"""Month calendar widget shared by picker date controls.

It provides project-specific date navigation and selection signals while the picker
retains ownership of chosen dates, model cycles, and forecast-hour state."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtGui import QPen
from qtpy.QtWidgets import QCalendarWidget


class MonthCalendar(QCalendarWidget):
    """Date-picker popup that shows only the month on display.

    The default popup has two problems under the application style sheet. The
    generic ``QTableView::item`` padding rule also matches the calendar's cells,
    because a ``QCalendarWidget`` is a ``QTableView`` internally; that leaves too
    little room for a two-digit day, and the item delegate elides the number to
    an ellipsis. Separately, the leading and trailing cells show the neighbouring
    months' days, which is noise in a control whose only job is to choose a date
    within the month on show, and invites clicking a day that silently jumps the
    view to another month.

    Painting the cells directly solves both: the number is drawn centred at the
    cell's full width with no delegate and no elision, and days outside the
    shown month are left blank. Weekend tinting and the disabled colour are read
    back from the widget's own formats and palette, so the popup still follows
    the active theme and any configured date range.
    """

    def _weekday_colour(self, date, palette) -> QColor:
        """Return the configured tint for ``date``'s weekday.

        ``weekdayTextFormat`` takes a ``Qt.DayOfWeek``, while ``QDate.dayOfWeek``
        returns a plain int, and the bindings are not consistent about coercing
        between them. A failure here must not be able to make the popup
        unpaintable, so an unusable format falls back to the ordinary text
        colour.
        """
        try:
            day = Qt.DayOfWeek(date.dayOfWeek())
            brush = self.weekdayTextFormat(day).foreground()
            if brush.style() != Qt.NoBrush:
                return brush.color()
        except (TypeError, ValueError):
            pass
        return palette.text().color()

    def paintCell(self, painter, rect, date) -> None:  # noqa: N802 (Qt override)
        palette = self.palette()
        in_month = (date.month() == self.monthShown()
                    and date.year() == self.yearShown())
        selected = in_month and date == self.selectedDate()

        painter.save()
        try:
            if selected:
                painter.fillRect(rect, palette.highlight())
            else:
                painter.fillRect(rect, palette.base())
            if not in_month:
                return  # adjacent month: background only, no number

            in_range = self.minimumDate() <= date <= self.maximumDate()
            if selected:
                colour = palette.highlightedText().color()
            elif not in_range:
                colour = palette.color(palette.ColorGroup.Disabled,
                                       palette.ColorRole.Text)
            else:
                colour = self._weekday_colour(date, palette)
            painter.setPen(QPen(colour))
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignCenter, str(date.day()))
        finally:
            painter.restore()
