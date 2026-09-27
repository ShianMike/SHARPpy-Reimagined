"""PickerAvailability methods for PickerWindow."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from sharpmod.ui.features.gui_workers import AVAIL_AVAILABLE
from sharpmod.ui.features.gui_workers import AVAIL_CHECKING
from sharpmod.ui.features.gui_workers import AVAIL_UNKNOWN
from sharpmod.ui.features.gui_workers import _AVAIL_LABELS
from sharpmod.ui.features.gui_workers import _station_label
from sharpmod.ui.picker.selection import refresh_selection_feedback
from sharpmod import gui_picker as _picker_api
from sharpmod.ui.features.gui_workers import _AvailabilityIndicator


class PickerAvailabilityMixin:
    """Picker behavior grouped by responsibility."""

    def _station_label_for(self, sid: str | None) -> str:
        """Look up a station's index + city label from the loaded catalogue."""
        if not sid:
            return ""
        st = next((s for s in self._all_stations if s["id"] == sid), None)
        return _station_label(sid, st["name"] if st else "")

    def _queue_availability(
        self, sid: str | None, when: datetime, indicator: "_AvailabilityIndicator"
    ) -> None:
        """Debounce, then probe ``sid`` at ``when`` and update ``indicator``."""
        # An older worker must not repaint a new selection (or an empty one)
        # while the next check is still waiting for the debounce timer.
        self._avail_latest.pop(id(indicator), None)
        if (self._avail_request is not None
                and self._avail_request[2] is indicator):
            self._avail_request = None
            self._avail_timer.stop()
        if not sid:
            indicator.set_status(AVAIL_UNKNOWN)
            return
        cached = self._observed_profile_cache.get(self._observed_cache_key(sid, when))
        if cached is not None:
            _fetched, message, station_label = cached
            indicator.set_status(AVAIL_AVAILABLE, message, station_label)
            return
        indicator.set_status(
            AVAIL_CHECKING, _AVAIL_LABELS[AVAIL_CHECKING], self._station_label_for(sid)
        )
        self._avail_request = (sid, when, indicator, self._station(sid))
        self._avail_timer.start()

    def _run_pending_availability(self) -> None:
        if not self._avail_request:
            return
        sid, when, indicator, station = self._avail_request
        self._avail_request = None
        self._avail_token += 1
        token = self._avail_token
        self._avail_pending[token] = indicator
        self._avail_latest[id(indicator)] = token

        worker = _picker_api._AvailabilityWorker(
            sid,
            when,
            token,
            parent=self,
            station=station,
            provider=self._observed_source(),
        )
        worker.checked.connect(self._on_availability_checked)
        worker.finished.connect(worker.deleteLater)
        self._avail_workers.append(worker)
        worker.start()

    def _observed_cache_key(self, sid: str, when: datetime):
        """Key the preflight cache by source as well as station and time.

        Without the source in the key, switching archives would be answered by
        whichever one was probed first -- the station and time are identical, so
        the entry would still hit.
        """
        if when.tzinfo is not None:
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        return (
            self._observed_source(),
            str(sid).strip().casefold(),
            when.replace(microsecond=0),
        )

    def _on_availability_checked(
        self,
        _sid: str,
        _when,
        status: str,
        message: str,
        station_label: str,
        fetched=None,
    ) -> None:
        worker = self.sender()
        token = getattr(worker, "token", None)
        indicator = self._avail_pending.pop(token, None)
        if worker in self._avail_workers:
            self._avail_workers.remove(worker)
        if indicator is None:
            return
        # Discard results superseded by a newer check for the same indicator.
        if self._avail_latest.get(id(indicator)) != token:
            return
        indicator.set_status(status, message, station_label)
        key = self._observed_cache_key(_sid, _when)
        if status == AVAIL_AVAILABLE and fetched is not None:
            self._observed_profile_cache[key] = (fetched, message, station_label)
            while len(self._observed_profile_cache) > 4:
                self._observed_profile_cache.pop(
                    next(iter(self._observed_profile_cache))
                )
        else:
            self._observed_profile_cache.pop(key, None)

    def _refresh_station_catalog(self, when: datetime) -> None:
        """Debounce, then fetch the stations UWyo reported at ``when`` (UTC).

        No-ops when the catalogue already matches ``when`` so switching between
        tabs (which share one station set) does not re-fetch needlessly.
        """
        if when == self._catalog_when:
            return
        self._catalog_request = when
        self._catalog_timer.start()

    def _run_pending_catalog(self) -> None:
        when = self._catalog_request
        self._catalog_request = None
        if when is None:
            return
        self._catalog_token += 1
        token = self._catalog_token
        try:
            self.statusBar().showMessage(
                f"Loading stations for {when:%Y-%m-%d %H}Z from UWyo\u2026"
            )
        except Exception:
            pass
        worker = _picker_api._StationListWorker(when, token, parent=self)
        worker.loaded.connect(self._on_station_list_loaded)
        worker.failed.connect(self._on_station_list_failed)
        worker.finished.connect(worker.deleteLater)
        self._catalog_worker = worker
        worker.start()

    def _on_station_list_loaded(self, when, stations) -> None:
        worker = self.sender()
        if getattr(worker, "token", None) != self._catalog_token:
            return  # a newer request superseded this one
        self._all_stations = list(stations)
        self._catalog_when = when

        # Repaint the map and re-run the live filter against the new set.
        if hasattr(self, "_map"):
            self._map.set_stations(self._all_stations)
            if self._map_selected_id and self._station(self._map_selected_id) is None:
                # The previously selected station isn't reported at this time.
                self._map_selected_id = None
                self._map_sel_lbl.setText("No station selected")
                self._map_gen_btn.setEnabled(False)
                self._map_avail.set_status(AVAIL_UNKNOWN)
            refresh_selection_feedback(self, "map")
        if hasattr(self, "_uwyo_search"):
            self._filter_stations(self._uwyo_search.text())

        try:
            self.statusBar().showMessage(
                f"{len(self._all_stations)} stations available for {when:%Y-%m-%d %H}Z"
            )
        except Exception:
            pass

    def _on_station_list_failed(self, when, message: str) -> None:
        worker = self.sender()
        if getattr(worker, "token", None) != self._catalog_token:
            return
        # Keep the current (bundled or previous) catalogue; just note it.
        try:
            self.statusBar().showMessage(
                f"Using offline station list \u2014 could not load "
                f"{when:%Y-%m-%d %H}Z ({message})"
            )
        except Exception:
            pass
