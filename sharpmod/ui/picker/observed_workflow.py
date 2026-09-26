"""Observed sounding station selection and fetch lifecycle."""

from __future__ import annotations

import os
from datetime import datetime

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QMessageBox

from sharpmod.ui.features.gui_common import APP_NAME, _LOGGER, _most_recent_synoptic, _render
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.ui.picker.cycles import _select_cycle
from sharpmod.ui.picker.layout import set_button_busy as _set_button_busy
from sharpmod.ui.picker.selection import refresh_selection_feedback
from sharpmod.ui.picker.station_catalogue import StationCatalogueItem


class ObservedWorkflowMixin:
    """Maintain UWyo station selection and observed job status."""

    def _uwyo_recheck_availability(self) -> None:
        self._queue_availability(
            self._selected_station_id(), self._selected_when(), self._uwyo_avail
        )

    def _uwyo_refresh_catalog(self) -> None:
        """Refresh the station set when the list tab's cycle changes."""
        self._refresh_station_catalog(self._selected_when())

    def _filter_stations(self, text: str) -> None:
        needle = (text or "").strip()
        query = needle.casefold()
        if query:
            rows = [
                r
                for r in self._all_stations
                if query in r["id"].casefold() or query in r["name"].casefold()
            ]
        else:
            rows = self._all_stations

        selected_before = self._selected_station_id()
        selected_item = None
        sort_column = self._station_list.sortColumn()
        sort_order = self._station_list.header().sortIndicatorOrder()
        self._station_list.setUpdatesEnabled(False)
        signals_were_blocked = self._station_list.blockSignals(True)
        self._station_list.setSortingEnabled(False)
        try:
            self._station_list.clear()
            for station in rows:
                item = StationCatalogueItem((
                    station["id"], station["name"],
                    f"{station['lat']:.2f}°", f"{station['lon']:.2f}°",
                ))
                item.setData(0, Qt.UserRole, station["id"])
                item.setData(2, Qt.UserRole, float(station["lat"]))
                item.setData(3, Qt.UserRole, float(station["lon"]))
                item.setToolTip(
                    1, f"{station['id']} — {station['name']}\n"
                       f"{station['lat']:.2f}°, {station['lon']:.2f}°")
                for column in (2, 3):
                    item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
                self._station_list.addTopLevelItem(item)
                if station["id"] == selected_before:
                    selected_item = item
        finally:
            self._station_list.setSortingEnabled(True)
            self._station_list.sortByColumn(sort_column, sort_order)
            if selected_item is not None:
                self._station_list.setCurrentItem(selected_item)
            self._station_list.blockSignals(signals_were_blocked)
            self._station_list.setUpdatesEnabled(True)

        total = len(self._all_stations)
        shown = len(rows)
        self._count_lbl.setText(
            f"{shown} of {total} stations" if query else f"{total} stations")
        self._uwyo_empty_title.setText(
            "No matching stations" if query else "No stations available")
        self._uwyo_empty_lbl.setText(
            "Try another station ID or name."
            if query else "Choose another observation date or cycle.")
        self._uwyo_clear_search.setVisible(bool(query))
        self._uwyo_results_stack.setCurrentIndex(0 if shown else 1)
        self._uwyo_help_lbl.setVisible(shown > 0)
        self._sync_fetch_enabled()
        if selected_before != self._selected_station_id():
            self._uwyo_recheck_availability()

    def _focus_first_station(self) -> None:
        """Enter in the filter box selects the first match (then Fetch works)."""
        if (self._station_list.topLevelItemCount() > 0
                and self._station_list.currentItem() is None):
            self._station_list.setCurrentItem(self._station_list.topLevelItem(0))
        self._station_list.setFocus()

    def _selected_station_id(self) -> str | None:
        item = self._station_list.currentItem()
        if item is None:
            return None
        sid = item.data(0, Qt.UserRole)
        return str(sid) if sid else None

    def _sync_fetch_enabled(self) -> None:
        if not hasattr(self, "_fetch_btn"):
            return
        busy = self._worker is not None and self._worker.isRunning()
        sid = self._selected_station_id()
        station = self._station(sid) if sid else None
        if station is not None:
            self._uwyo_selected_lbl.setText(
                f"{sid} — {station['name']}\n"
                f"{station['lat']:.2f}°, {station['lon']:.2f}°")
        else:
            self._uwyo_selected_lbl.setText(sid or "Choose a station from the catalogue")
        self._uwyo_avail.setVisible(bool(sid))
        self._fetch_btn.setEnabled(not busy and sid is not None)
        refresh_selection_feedback(self, "uwyo")

    def _set_most_recent(self) -> None:
        d, h = _most_recent_synoptic()
        self._date_edit.setDate(d)
        _select_cycle(self._cycle_combo, h)
        self._update_valid_label()

    def _selected_when(self) -> datetime:
        d = self._date_edit.date()
        h = int(self._cycle_combo.currentData())
        return datetime(d.year(), d.month(), d.day(), h, 0)

    def _update_valid_label(self) -> None:
        when = self._selected_when()
        self._valid_lbl.setText(f"Valid: {when:%a %Y-%m-%d}  {when.hour:02d}Z")
        refresh_selection_feedback(self, "uwyo")

    def _fetch_selected(self) -> None:
        sid = self._selected_station_id()
        if not sid:
            QMessageBox.warning(self, APP_NAME, "Select a station from the list first.")
            return
        self._start_fetch(sid, self._selected_when())

    def _display_prefetched_observation(
        self, fetched, requested_sid: str, when: datetime
    ) -> None:
        """Display the profile already decoded by the availability worker."""
        from sharppy.sharptab.prof_collection import ProfCollection

        # IGRA can satisfy a synoptic-hour request with a nearby special
        # release.  The cached preflight result therefore owns the display
        # time; ``when`` remains only the cache/request key.
        delivered_when = getattr(fetched, "valid", None)
        if not isinstance(delivered_when, datetime):
            delivered_when = when
        station_id = str(getattr(fetched, "station_id", None) or requested_sid)
        provider = str(getattr(fetched, "provider", "uwyo")).upper()
        prof_col = ProfCollection(
            {"": [fetched.profile]},
            [delivered_when],
            observed=True,
            base_time=delivered_when,
            run=delivered_when,
            model=provider,
            loc=station_id,
        )
        self._set_busy(True)
        self.statusBar().showMessage(
            f"Rendering {station_id} from the completed availability check…"
        )
        QApplication.processEvents()
        try:
            title = (
                f"{APP_NAME} — {station_id} {delivered_when:%Y-%m-%d %H}Z [{provider}]"
            )
            self._show_sounding(prof_col, station_id, title=title)
            self.statusBar().showMessage(
                f"Opened {station_id} {delivered_when:%Y-%m-%d %H}Z "
                f"from {provider} "
                "(reused availability download)"
            )
            _LOGGER.info(
                "observed_fetch.displayed_from_preflight station=%s valid=%s",
                station_id,
                delivered_when,
            )
        except Exception as exc:  # noqa: BLE001 - GUI/render boundary
            _LOGGER.exception(
                "observed_fetch.preflight_display_failed station=%s valid=%s",
                station_id,
                delivered_when,
            )
            QMessageBox.critical(
                self,
                APP_NAME,
                f"Fetched, but could not display:\n{exc}",
            )
        finally:
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        _LOGGER.debug(
            "uwyo_fetch.ui_busy busy=%s worker=%s",
            busy,
            id(self._worker) if self._worker else None,
        )
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            _set_button_busy(getattr(self, "_fetch_btn", None), True, "Fetching\u2026")
            _set_button_busy(
                getattr(self, "_map_gen_btn", None), True, "Fetching\u2026"
            )
        else:
            QApplication.restoreOverrideCursor()
            _set_button_busy(getattr(self, "_fetch_btn", None), False, "")
            self._sync_fetch_enabled()
            map_btn = getattr(self, "_map_gen_btn", None)
            _set_button_busy(map_btn, False, "")
            if map_btn is not None:
                map_btn.setEnabled(bool(self._map_selected_id))
        refresh_selection_feedback(self, "map")
        refresh_selection_feedback(self, "uwyo")

    def _observed_current(self, worker):
        token = getattr(self, "_observed_token", None)
        if worker is not self._worker or token is None:
            return False
        if getattr(worker, "_sharpmod_observed_token", None) != token:
            return False
        # Cancel must win over a queued terminal report: a snapshot for this
        # token that left running (cancelled, finished) rejects late reports.
        # No snapshot, or one for another token, keeps the legacy worker-
        # identity verdict already established above.
        snapshot = self._observed_job.snapshot
        return not (
            snapshot is not None
            and snapshot.token == token
            and snapshot.state != "running"
        )

    def _on_fetch_ok(self, npz_path, meta, when) -> None:
        worker = self.sender()
        if not self._observed_current(worker):
            return
        token = self._observed_token
        self.statusBar().showMessage(
            f"Rendering {meta.id} sounding\u2026 (this takes a moment)"
        )
        # Force the status message to paint before the synchronous compose
        # (which briefly blocks the UI thread while the SPC window is built).
        QApplication.processEvents()
        if not self._observed_current(worker):
            return
        try:
            R = _render()
            prof_col, stn_id = R.decode(npz_path)
            provider = str(getattr(meta, "provider", "observed")).upper()
            title = f"{APP_NAME} \u2014 {meta.id} {when:%Y-%m-%d %H}Z [{provider}]"
            self._show_sounding(prof_col, stn_id, title=title)
            self.statusBar().showMessage(
                f"Opened {meta.id} {when:%Y-%m-%d %H}Z from {provider}"
            )
            _LOGGER.info("uwyo_fetch.displayed station=%s valid=%s", meta.id, when)
            self._observed_job.finish(
                token, counts=JobCounts(completed=1, requested=1),
                message=f"Opened {meta.id} {when:%Y-%m-%d %H}Z from {provider}.",
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "uwyo_fetch.display_failed station=%s valid=%s", meta.id, when
            )
            self._observed_job.finish(
                token, counts=JobCounts(failed=1, requested=1),
                outcome="failed", message=f"Fetched, but could not display: {exc}",
                retryable=True,
            )
            self._observed_job_retry = self._observed_token
            QMessageBox.critical(
                self, APP_NAME, f"Fetched, but could not display:\n{exc}"
            )
        finally:
            try:
                os.remove(npz_path)
            except OSError:
                pass
            try:
                os.remove(os.path.splitext(npz_path)[0] + ".json")
            except OSError:
                pass

    def _on_fetch_failed(self, message: str) -> None:
        worker = self.sender()
        if not self._observed_current(worker):
            return
        token = self._observed_token
        _LOGGER.error("uwyo_fetch.failed message=%s", message)
        self.statusBar().showMessage("Fetch failed")
        self._observed_job.finish(
            token, counts=JobCounts(failed=1, requested=1),
            outcome="failed", message=str(message), retryable=True,
        )
        self._observed_job_retry = self._observed_token
        QMessageBox.critical(self, APP_NAME, message)

    def _cancel_observed_fetch(self) -> None:
        worker = self._worker
        if worker is None:
            return
        token = getattr(self, "_observed_token", None)
        if token is not None:
            self._observed_job.request_cancel(token)
        worker.requestInterruption()
        self.statusBar().showMessage("Cancelling observed sounding fetch…")

    def _on_observed_finished(self) -> None:
        worker = self.sender()
        token = getattr(self, "_observed_token", None)
        if worker is not self._worker:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            return
        self._worker = None
        try:
            self._set_busy(False)
        finally:
            if (
                token is not None
                and self._observed_job.snapshot is not None
                and self._observed_job.snapshot.token == token
                and self._observed_job.snapshot.state in {"running", "cancelling"}
            ):
                cancelled = self._observed_job.snapshot.state == "cancelling"
                self._observed_job.finish(
                    token,
                    counts=JobCounts(
                        cancelled=1 if cancelled else 0,
                        unknown=0 if cancelled else 1,
                        requested=1,
                    ),
                    outcome="cancelled" if cancelled else "partial",
                    message=(
                        "Observed sounding fetch cancelled; no new sounding was opened."
                        if cancelled
                        else "Observed worker stopped without a terminal report; no new sounding was opened."
                    ),
                    retryable=True,
                )
                self._observed_job_retry = token
            worker.deleteLater()

    def _retry_observed_job(self) -> None:
        token = getattr(self, "_observed_job_retry", None)
        request = getattr(self, "_observed_request", None)
        if token is None or request is None:
            return
        snapshot = self._observed_job.snapshot
        if snapshot is None or snapshot.token != token or not snapshot.retryable:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        sid, when, _source = request
        self._observed_job_retry = None
        self._start_fetch(sid, when)
