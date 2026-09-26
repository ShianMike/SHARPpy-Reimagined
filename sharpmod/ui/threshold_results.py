"""ThresholdResults methods for EnsembleThresholdExplorer."""

from __future__ import annotations

from datetime import datetime
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication
from qtpy.QtWidgets import QTableWidgetItem
from sharpmod.analysis.box_analysis import parameter
from sharpmod.analysis.ensemble_thresholds import DISCLAIMER
from sharpmod.analysis.ensemble_thresholds import fraction_text
from sharpmod.ui.features.gui_common import set_status_label
from sharpmod.ui.features.gui_ensemble_thresholds import (
    _COLUMN_FILTERS,
    _EXCLUSION_TALLY
)


class ThresholdResultsMixin:
    """Focused workspace behavior."""

    def _render_results(self):
        selected = self._selected_result()
        selected_time = selected.valid_time if selected is not None else None
        member_row = self.members.currentRow()
        selected_member = (
            self._visible_members[member_row].member
            if 0 <= member_row < len(self._visible_members)
            else None
        )
        focus = QApplication.focusWidget()
        timeline_scroll = (
            self.timeline.horizontalScrollBar().value(),
            self.timeline.verticalScrollBar().value(),
        )
        member_scroll = (
            self.members.horizontalScrollBar().value(),
            self.members.verticalScrollBar().value(),
        )
        blocked = self.timeline.blockSignals(True)
        try:
            self.timeline.setRowCount(len(self._results))
            for row, result in enumerate(self._results):
                values = (
                    result.valid_time.strftime("%Y-%m-%d %H:%MZ")
                    if isinstance(result.valid_time, datetime)
                    else "Unknown",
                    str(result.requested_member_count),
                    str(result.loaded_member_count),
                    str(result.usable_member_count),
                    str(result.qualifying_member_count),
                    str(result.nonqualifying_member_count),
                    result.agreement_text,
                    result.coverage_text,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, row)
                    tip = self.TIMELINE_COLUMNS[column][1]
                    if column in _COLUMN_FILTERS:
                        tip += " Select this cell to filter the member list to it."
                    item.setToolTip(tip)
                    self.timeline.setItem(row, column, item)
            selected_row = next(
                (
                    row
                    for row, result in enumerate(self._results)
                    if result.valid_time == selected_time
                ),
                0,
            )
            if self._results:
                self.timeline.selectRow(selected_row)
        finally:
            self.timeline.blockSignals(blocked)
        if self._results:
            # Explicitly render after the signal-blocked replacement. This also
            # avoids transient status/member rows for half-populated timeline data.
            self._timeline_selected()
            if selected_member is not None:
                for row, item in enumerate(self._visible_members):
                    if item.member == selected_member:
                        self.members.selectRow(row)
                        break
        else:
            set_status_label(
                self.status,
                "Evaluation cancelled before any member reached a verdict.",
                level="warn",
            )
            self.members.setRowCount(0)
            self.member_count.clear()
            self.retry.setEnabled(False)
        self.export.setEnabled(bool(self._results))
        self._fit_result_tables()
        self.timeline.horizontalScrollBar().setValue(timeline_scroll[0])
        self.timeline.verticalScrollBar().setValue(timeline_scroll[1])
        self.members.horizontalScrollBar().setValue(member_scroll[0])
        self.members.verticalScrollBar().setValue(member_scroll[1])
        if focus is not None and (focus is self or self.isAncestorOf(focus)):
            focus.setFocus(Qt.OtherFocusReason)

    def _selected_result(self):
        row = self.timeline.currentRow()
        if not 0 <= row < len(self._results):
            return None
        return self._results[row]

    def _render_status(self):
        """Describe the selected valid time, with every denominator spelled out.

        The status only ever described the first result, so selecting any other row
        left a sentence about a different valid time above the member list it had
        just replaced.
        """
        result = self._selected_result()
        if result is None:
            return
        if not result.requested_member_count:
            set_status_label(
                self.status,
                f"No requested-member denominator is available. {DISCLAIMER}",
                level="warn",
            )
            return
        parts = [
            f"{result.agreement_text} usable members qualify",
            f"{result.coverage_text} of the requested ensemble was usable",
            f"{fraction_text(result.loaded_member_count, result.requested_member_count)} loaded",
        ]
        excluded = []
        if result.not_loaded_member_count:
            excluded.append(f"{result.not_loaded_member_count} never loaded")
        for state, phrase in _EXCLUSION_TALLY.items():
            count = result.state_counts.get(state, 0)
            if count:
                excluded.append(f"{count} {phrase}")
        withheld = result.not_evaluated_member_count
        if withheld:
            excluded.append(f"{withheld} not evaluated")
        if excluded:
            parts.append("excluded: " + ", ".join(excluded))
        if result.cancelled:
            parts.append(
                f"cancelled after {result.resolved_member_count} of "
                f"{result.requested_member_count} members"
            )
        # A displayed funnel that does not add up is a defect, not a nuance, so it
        # is stated rather than left for a reader to discover by subtracting.
        if not result.reconciles:
            parts.append(
                "counts do not reconcile against the requested ensemble; treat this "
                "result as unreliable"
            )
        text = ". ".join(parts) + f". {DISCLAIMER}"
        partial = (
            result.cancelled
            or not result.reconciles
            or result.usable_member_count < result.requested_member_count
        )
        set_status_label(
            self.status,
            text,
            level="error" if not result.reconciles else "warn" if partial else "info",
        )

    def _refresh_definition_summary(self):
        """Restate the current rule in words, or say why it cannot be read."""
        try:
            definition = self.definition()
        except Exception as exc:  # noqa: BLE001 - live editing boundary
            self.definition_summary.setText(f"Incomplete rule: {exc}")
            return
        self.definition_summary.setText(f"This rule tests: {definition.summary}")

    def _timeline_cell_clicked(self, _row, column):
        """Selecting a count filters the member list to the members behind it."""
        index = _COLUMN_FILTERS.get(int(column))
        if index is not None and index != self.member_filter.currentIndex():
            self.member_filter.setCurrentIndex(index)

    def _timeline_selected(self):
        self._render_status()
        self._render_members()

    def _render_members(self):
        """List the members in the selected valid time, narrowed by the filter."""
        result = self._selected_result()
        if result is None:
            self.members.setRowCount(0)
            self.member_count.clear()
            self._visible_members = ()
            self.retry.setEnabled(False)
            self.open_member.setEnabled(False)
            return
        states = tuple(self.member_filter.currentData() or ())
        shown = result.members_in(*states) if states else result.members
        self._visible_members = tuple(shown)
        metrics = result.definition.metrics
        metric_items = []
        for key in metrics:
            try:
                metric_items.append(parameter(key))
            except Exception:  # noqa: BLE001 - an unknown key still names itself
                metric_items.append(None)
        self.members.clear()
        # A reason column, not a tooltip. The reason a member was excluded is the
        # actionable part, and hiding it behind a hover made it invisible to
        # keyboard readers and to anyone scanning the list.
        self.members.setColumnCount(3 + len(metrics))
        self.members.setHorizontalHeaderLabels(
            (
                "Member",
                "Outcome",
                "Reason",
                *(
                    item.display_label if item is not None else str(key)
                    for key, item in zip(metrics, metric_items)
                ),
            )
        )
        self.members.setRowCount(len(shown))
        for item_row, member in enumerate(shown):
            first = QTableWidgetItem(member.member)
            first.setData(Qt.UserRole, member.member)
            first.setToolTip(f"{member.member}: {member.state_label}")
            self.members.setItem(item_row, 0, first)
            outcome = QTableWidgetItem(member.short_state_label)
            outcome.setToolTip(member.state_label)
            outcome.setData(Qt.UserRole, member.state)
            self.members.setItem(item_row, 1, outcome)
            reason = QTableWidgetItem(member.reason or "")
            reason.setToolTip(member.reason or member.state_label)
            self.members.setItem(item_row, 2, reason)
            for column, (key, metric_item) in enumerate(
                zip(metrics, metric_items), start=3
            ):
                value = member.values.get(key)
                self.members.setItem(
                    item_row,
                    column,
                    QTableWidgetItem(
                        metric_item.format(value)
                        if metric_item is not None
                        else "\u2014" if value is None else f"{value:g}"
                    ),
                )
        label = self.member_filter.currentText()
        self.member_count.setText(
            f"{len(shown)} of {result.requested_member_count} requested members"
            + ("" if not states else f" \u00b7 {label}")
        )
        self.member_count.setToolTip(
            "The filtered list and the counts above are built from the same member "
            "states, so they cannot disagree."
        )
        self.members.setAccessibleDescription(
            f"{label}. {self.member_count.text()}. Each row names the member, its "
            "outcome, the reason it was excluded when it was, and its diagnostic "
            "values. Select a member and choose Open member to show its profile."
        )
        retryable = result.retryable_members
        self.retry.setEnabled(bool(retryable) and callable(self._request_retry))
        if retryable:
            self.retry.setToolTip(
                "Fetch only these members, which produced no profile for this "
                "valid time: " + ", ".join(retryable)
            )
        self._member_selection_changed()
        self._fit_result_tables()

    def _member_selection_changed(self):
        row = self.members.currentRow()
        self.open_member.setEnabled(0 <= row < len(self._visible_members))

    def _member_double_clicked(self, row, _column):
        self._activate_member(row)

    def _open_selected_member(self):
        self._activate_member(self.members.currentRow())

    def _activate_member(self, row):
        """Ask the host to show one member's own profile at this valid time."""
        result = self._selected_result()
        if result is None or not 0 <= row < len(self._visible_members):
            return
        member = self._visible_members[row]
        if not member.loaded:
            set_status_label(
                self.status,
                f"Member {member.member} cannot be opened: {member.state_label}.",
                level="warn",
            )
            return
        self.memberActivated.emit(member.member, result.valid_time)

    def _retry_unavailable(self):
        """Hand the targeted retry to the host, which owns the fetch coordinator."""
        result = self._selected_result()
        if result is None or not callable(self._request_retry):
            return
        members = result.retryable_members
        if not members:
            return
        try:
            started = bool(self._request_retry(self._collection, members))
        except Exception as exc:  # noqa: BLE001 - host boundary
            self._set_error(f"Could not start member retry: {exc}")
            return
        if started:
            self.retry.setEnabled(False)
            set_status_label(
                self.status,
                "Retrying "
                + ", ".join(members)
                + "; already loaded members are not downloaded again. Evaluate "
                "again once they arrive.",
            )
        else:
            set_status_label(
                self.status,
                "Retry is unavailable for this sounding; the member ledger and this "
                "result are unchanged.",
                level="warn",
            )
