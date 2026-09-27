"""_AvailabilityWorker worker implementations."""

from __future__ import annotations

from datetime import datetime
from qtpy.QtCore import QThread
from qtpy.QtCore import Signal
from sharpmod.ui.features.gui_common import _uwyo_decoder_classes
from types import SimpleNamespace
from sharpmod.ui.features.gui_workers import (
    AVAIL_AVAILABLE,
    AVAIL_INSUFFICIENT,
    AVAIL_UNAVAILABLE,
    AVAIL_UNKNOWN,
    PROVIDER_AUTO,
    _classify_availability,
    _declined_large_download,
    _decoder_for_station,
    _station_label,
    _uses_uwyo_probe
)
from sharpmod.ui.features import gui_workers as _api


class _AvailabilityWorker(QThread):
    """Probe UWyo for a station/time and classify the result off the UI thread.

    Emits :attr:`checked` with status text plus the decoded profile on success.
    The full fetch is required to know whether a sounding is usable, so the
    picker retains that result and reuses it when Generate is pressed.
    """

    #: (query, when, status, message, station_label, fetched). ``fetched`` is
    #: the already-decoded profile plus station metadata on a usable result,
    #: otherwise ``None``. Keeping it lets Generate reuse the preflight request
    #: instead of downloading and decoding the same sounding twice.
    checked = Signal(str, object, str, str, str, object)

    def __init__(self, station_query: str, when_utc: datetime, token: int,
                 parent=None, station: dict | None = None,
                 provider: str = PROVIDER_AUTO):
        super().__init__(parent)
        self._query = station_query
        self._when = when_utc
        self.token = token
        self._station = station
        self._provider = str(provider or PROVIDER_AUTO).strip().lower()

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        if _uses_uwyo_probe(self._provider):
            self._probe_uwyo()
        else:
            self._probe_provider(self._provider)

    def _station_label_from_record(self) -> str:
        """Best label available before a provider has resolved the station."""
        record = self._station or {}
        return _station_label(
            str(record.get("id", "") or self._query),
            str(record.get("name", "")),
        )

    def _probe_provider(self, key: str) -> None:
        """Grade one non-UWyo source through the provider layer.

        UWyo keeps its own path because its decoder raises a richer set of
        typed errors. Every other source shares the provider taxonomy, which
        already separates "nothing archived" from "unreadable".
        """
        label = self._station_label_from_record()
        try:
            from sharpmod.providers.observations import (
                ObservedParseError,
                ObservedRetrievalError,
                ObservedStationError,
                ObservedUnavailableError,
            )
        except Exception:  # noqa: BLE001 - import/freezer boundary
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (providers)", label, None)
            return

        try:
            provider = _api._availability_provider(key)
        except Exception:  # noqa: BLE001
            provider = None
        if provider is None:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              f"Unavailable ({key} unsupported)", label, None)
            return

        if self.isInterruptionRequested():
            return
        try:
            result = provider.fetch(self._query, self._when)
        except ObservedStationError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", label, None)
            return
        except ObservedUnavailableError as exc:
            if _declined_large_download(exc):
                self.checked.emit(
                    self._query, self._when, AVAIL_UNKNOWN,
                    "Not checked (downloads on Generate)", label, None)
            else:
                self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                                  "Unavailable (no sounding)", label, None)
            return
        except ObservedRetrievalError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (service unreachable)", label, None)
            return
        except ObservedParseError:
            self.checked.emit(self._query, self._when, AVAIL_INSUFFICIENT,
                              "Limited (data unreadable)", label, None)
            return
        except Exception:  # noqa: BLE001 - never crash the UI thread
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (unexpected error)", label, None)
            return

        if self.isInterruptionRequested():
            return
        metadata = dict(getattr(result, "metadata", {}) or {})
        label = _station_label(
            str(result.station_id),
            str(metadata.get("station_name", "") or ""),
        ) or label
        status, message = _classify_availability(result.profile)
        fetched = None
        if status == AVAIL_AVAILABLE:
            delivered_valid = getattr(result, "valid", self._when)
            fetched = SimpleNamespace(
                profile=result.profile,
                station_id=str(result.station_id),
                station_name=str(metadata.get("station_name", "") or ""),
                provider=str(result.provider),
                valid=delivered_valid,
            )
        self.checked.emit(
            self._query, self._when, status, message, label, fetched
        )

    def _probe_uwyo(self) -> None:
        try:
            StationLookupError, UWyo_Decoder, UWyoError = _uwyo_decoder_classes()
        except Exception:  # noqa: BLE001
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (decoder)", "", None)
            return

        # Typed errors let us distinguish "nothing archived" (red) from
        # "corrupt/unparseable" (gray). Missing imports degrade gracefully.
        try:
            from sharpmod.io.uwyo_decoder import (
                RetrievalError,
                SoundingParseError,
                StationTimeUnavailableError,
            )
        except Exception:  # noqa: BLE001 - fall back to base-class handling
            RetrievalError = SoundingParseError = StationTimeUnavailableError = ()

        # Resolve the station first so its index + city can be reported in
        # every outcome (available, unavailable, or insufficient). A live
        # station record (with its real UWyo ``src``) is used when available so
        # relocated / re-indexed stations resolve and fetch correctly.
        try:
            decoder, seeded_query = _decoder_for_station(self._station)
            meta = decoder.resolve_station(seeded_query or self._query)
        except StationLookupError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", "", None)
            return
        except Exception:  # noqa: BLE001
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", "", None)
            return

        label = _station_label(meta.id, meta.name)

        if self.isInterruptionRequested():
            return
        try:
            prof = decoder.fetch(meta.id, self._when)
        except SoundingParseError:
            self.checked.emit(self._query, self._when, AVAIL_INSUFFICIENT,
                              "Limited (data unreadable)", label, None)
            return
        except StationTimeUnavailableError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (no sounding)", label, None)
            return
        except RetrievalError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (service unreachable)", label, None)
            return
        except UWyoError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (fetch failed)", label, None)
            return
        except Exception:  # noqa: BLE001 - never crash the UI thread
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (unexpected error)", label, None)
            return

        if self.isInterruptionRequested():
            return
        status, message = _classify_availability(prof)
        fetched = None
        if status == AVAIL_AVAILABLE:
            fetched = SimpleNamespace(
                profile=prof,
                station_id=str(meta.id),
                station_name=str(meta.name),
                provider="uwyo",
            )
        self.checked.emit(
            self._query, self._when, status, message, label, fetched
        )
