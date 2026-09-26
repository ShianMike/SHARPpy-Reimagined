"""IGRA v2 station lookup, archive retrieval, and sounding-profile decoding.

Network access occurs on demand, while ``igra2`` owns the shared station/archive cache
and public API; this decoder converts retrieved records into SHARPpy-compatible profile
data."""

from __future__ import annotations

from sharpmod.io.igra2 import _CA_FILE

from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Callable
from typing import Sequence
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen
import io
import json
import math
import os
import socket
import ssl
import zipfile
from sharpmod.io import igra2 as _api


class IGRA_Decoder:
    """Retrieve and decode IGRA v2 soundings for one station at a time.

    Construction performs no network I/O. :meth:`resolve_station` needs the
    station list; :meth:`fetch` needs the station list and one per-station
    archive, both served through :class:`IGRACache`.
    """

    #: Hard per-request timeout in seconds.
    FETCH_TIMEOUT = 120

    #: Station list refresh interval. The upstream file is rewritten daily.
    STATION_LIST_MAX_AGE_SECONDS = 24 * 3600.0

    #: Year-to-date archive refresh interval. Also rewritten daily, but a
    #: sounding usually appears within two days of being taken, so a shorter
    #: window matters here than for the station list.
    Y2D_MAX_AGE_SECONDS = 6 * 3600.0

    #: Period-of-record refresh interval. This is a historical archive; the
    #: only reason to re-fetch is a periodic reprocessing upstream.
    POR_MAX_AGE_SECONDS = 30 * 24 * 3600.0

    #: How far from the requested time a sounding may sit and still answer the
    #: request. IGRA nominal hours are usually synoptic, but special releases
    #: at 16-21Z are common, and asking for 18Z should find a 19Z ascent.
    MATCH_TOLERANCE = timedelta(hours=3)

    #: Refuse an archive larger than this. A full period-of-record ZIP for a
    #: long-lived station is around 80 MB, so the ceiling has to clear that
    #: while still catching a runaway response.
    DEFAULT_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024

    def __init__(
        self,
        cache: _api.IGRACache | None = None,
        *,
        http_get: Callable[[str], bytes] | None = None,
        http_head: Callable[[str], int | None] | None = None,
        max_archive_bytes: int | None = None,
        allow_full_record: bool = True,
        now: Callable[[], datetime] | None = None,
    ):
        self._cache = cache if cache is not None else _api.IGRACache()
        self._http_get_override = http_get
        self._http_head_override = http_head
        if max_archive_bytes is None:
            max_archive_bytes = int(
                float(
                    os.environ.get("SHARPMOD_IGRA_MAX_ARCHIVE_MB", "256")
                )
                * 1024 * 1024
            )
        self.max_archive_bytes = max(0, int(max_archive_bytes))
        self.allow_full_record = bool(allow_full_record)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stations: tuple[_api.IGRAStation, ...] | None = None

    # -- HTTP ------------------------------------------------------------- #
    def _context(self):
        return ssl.create_default_context(cafile=_CA_FILE)

    def _http_get(self, url: str) -> bytes:
        """GET ``url`` over verified HTTPS, refusing an oversized body."""
        if self._http_get_override is not None:
            return self._http_get_override(url)
        request = Request(
            url, headers={"User-Agent": "SHARPpy-Reimagined/igra2"}
        )
        try:
            with urlopen(
                request, timeout=self.FETCH_TIMEOUT, context=self._context()
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) > self.max_archive_bytes:
                            raise _api.IGRAArchiveTooLargeError(
                                f"IGRA archive is {int(declared)} bytes, over "
                                f"the {self.max_archive_bytes}-byte ceiling: "
                                f"{url}"
                            )
                    except ValueError:
                        pass
                payload = response.read(self.max_archive_bytes + 1)
        except HTTPError as exc:
            if exc.code == 404:
                raise _api.IGRAStationTimeUnavailableError(
                    f"IGRA has no archive at {url}"
                ) from exc
            raise _api.IGRARetrievalError(
                f"IGRA request failed with HTTP {exc.code}: {url}"
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise _api.IGRARetrievalError(
                f"IGRA request timed out after {self.FETCH_TIMEOUT}s: {url}"
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise _api.IGRARetrievalError(
                f"IGRA archive could not be reached ({reason}): {url}"
            ) from exc
        except OSError as exc:
            raise _api.IGRARetrievalError(
                f"IGRA archive could not be reached ({exc}): {url}"
            ) from exc
        if len(payload) > self.max_archive_bytes:
            raise _api.IGRAArchiveTooLargeError(
                f"IGRA archive exceeded the {self.max_archive_bytes}-byte "
                f"ceiling: {url}"
            )
        return payload

    def _http_exists(self, url: str) -> bool:
        """True when ``url`` can be retrieved, probed without a body."""
        if self._http_head_override is not None:
            status = self._http_head_override(url)
            return status is not None and 200 <= int(status) < 300
        request = Request(
            url,
            headers={"User-Agent": "SHARPpy-Reimagined/igra2"},
            method="HEAD",
        )
        try:
            with urlopen(
                request, timeout=self.FETCH_TIMEOUT, context=self._context()
            ) as response:
                return 200 <= int(response.status) < 300
        except HTTPError:
            return False
        except (URLError, OSError, ValueError):
            return False

    # -- station list ----------------------------------------------------- #
    def stations(self) -> tuple[_api.IGRAStation, ...]:
        """Return every IGRA station, from cache when it is fresh."""
        if self._stations is not None:
            return self._stations
        payload = self._cache.load(
            "igra2-station-list.txt",
            lambda: self._http_get(_api.STATION_LIST_URL),
            max_age_seconds=self.STATION_LIST_MAX_AGE_SECONDS,
        )
        self._stations = _api.parse_station_list(
            payload.decode("utf-8", errors="replace")
        )
        return self._stations

    def resolve_station(self, query) -> _api.IGRAStation:
        """Resolve ``query`` to exactly one station.

        Accepts a full IGRA id (``USM00072357``), a bare WMO number
        (``72357``), an ICAO callsign, or a name substring, tried in that
        order. The WMO form is what makes the existing station map usable
        against this archive without a second catalogue.
        """
        requested = str(query or "").strip()
        if not requested:
            raise _api.IGRAStationLookupError("no station query given")
        stations = self.stations()
        normalized = requested.upper()

        exact = [item for item in stations if item.id == normalized]
        if len(exact) == 1:
            return exact[0]

        if normalized.isdigit():
            padded = normalized.zfill(5)
            by_wmo = [item for item in stations if item.wmo_id == padded]
            if len(by_wmo) == 1:
                return by_wmo[0]
            if len(by_wmo) > 1:
                raise _api.IGRAStationLookupError(
                    self._ambiguous(requested, by_wmo)
                )

        if normalized.isalnum() and 3 <= len(normalized) <= 4:
            tail = normalized[-4:]
            by_icao = [item for item in stations if item.icao_id == tail]
            if len(by_icao) == 1:
                return by_icao[0]
            if len(by_icao) > 1:
                raise _api.IGRAStationLookupError(
                    self._ambiguous(requested, by_icao)
                )

        folded = requested.casefold()
        by_name = [
            item for item in stations if folded in item.name.casefold()
        ]
        if len(by_name) == 1:
            return by_name[0]
        if len(by_name) > 1:
            raise _api.IGRAStationLookupError(self._ambiguous(requested, by_name))
        raise _api.IGRAStationLookupError(
            f"station query {requested!r} matched no IGRA station"
        )

    @staticmethod
    def _ambiguous(query: str, matches: Sequence[_api.IGRAStation]) -> str:
        listed = ", ".join(
            f"{item.id} ({item.name})" for item in sorted(
                matches, key=lambda item: item.id
            )[:8]
        )
        suffix = "" if len(matches) <= 8 else f", and {len(matches) - 8} more"
        return (
            f"station query {query!r} matched {len(matches)} IGRA stations: "
            f"{listed}{suffix}"
        )

    # -- archive selection ------------------------------------------------- #
    def _y2d_url(self, station_id: str) -> str | None:
        """Resolve the year-to-date archive URL for ``station_id``.

        The filename embeds the first year the year-to-date window covers, and
        that year moves: once the window rolls over, the previous name stops
        existing. Candidate years are probed newest first and the winner is
        remembered, because the answer is the same for every station.
        """
        current = self._now().year
        remembered = self._remembered_y2d_year()
        candidates = []
        for year in (remembered, current, current - 1):
            if year and year not in candidates:
                candidates.append(year)
        for year in candidates:
            url = f"{_api.DATA_Y2D_URL}/{station_id}-data-beg{year}.txt.zip"
            if self._http_exists(url):
                self._remember_y2d_year(year)
                return url
        return None

    def _remembered_y2d_year(self) -> int | None:
        age = self._cache.age_seconds("y2d-begin-year.json")
        if age > self.STATION_LIST_MAX_AGE_SECONDS:
            return None
        payload = self._cache.read("y2d-begin-year.json")
        if not payload:
            return None
        try:
            value = json.loads(payload.decode("utf-8")).get("year")
            return int(value)
        except (ValueError, AttributeError, TypeError):
            return None

    def _remember_y2d_year(self, year: int) -> None:
        try:
            self._cache.write(
                "y2d-begin-year.json",
                json.dumps({"year": int(year)}).encode("utf-8"),
            )
        except OSError:
            _api._LOGGER.debug("igra2.y2d_year_unsaved", exc_info=True)

    def _choose_archive(
        self, station: _api.IGRAStation, when_utc: datetime
    ) -> _api._ArchiveChoice:
        """Pick the smallest archive that can contain ``when_utc``.

        The year-to-date file is a few megabytes; the period of record can be
        eighty. Preferring the former keeps an everyday request cheap, and the
        latter is only reached for a date the former cannot cover.
        """
        y2d_url = self._y2d_url(station.id)
        if y2d_url is not None:
            begin_year = self._remembered_y2d_year() or self._now().year
            if when_utc.year >= begin_year:
                return _api._ArchiveChoice(
                    url=y2d_url,
                    kind="y2d",
                    cache_name=f"{station.id}-y2d.zip",
                    max_age_seconds=self.Y2D_MAX_AGE_SECONDS,
                )
        if not self.allow_full_record:
            raise _api.IGRAFullRecordRequiredError(
                f"{station.id} needs the full IGRA period-of-record archive "
                f"for {when_utc:%Y-%m-%d}, which is not enabled for this "
                f"request"
            )
        return _api._ArchiveChoice(
            url=f"{_api.DATA_POR_URL}/{station.id}-data.txt.zip",
            kind="por",
            cache_name=f"{station.id}-por.zip",
            max_age_seconds=self.POR_MAX_AGE_SECONDS,
        )

    def _archive_text(self, choice: _api._ArchiveChoice) -> str:
        """Return the decoded sounding file for one cached archive."""
        payload = self._cache.load(
            choice.cache_name,
            lambda: self._http_get(choice.url),
            max_age_seconds=choice.max_age_seconds,
        )
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = [
                    name for name in archive.namelist()
                    if not name.endswith("/")
                ]
                if not names:
                    raise _api.IGRASoundingParseError(
                        f"IGRA archive {choice.url} contained no files"
                    )
                raw = archive.read(names[0])
        except zipfile.BadZipFile as exc:
            # A truncated download would otherwise be cached and keep failing.
            try:
                self._cache.path_for(choice.cache_name).unlink()
            except OSError:
                pass
            raise _api.IGRASoundingParseError(
                f"IGRA archive {choice.url} is not a readable ZIP: {exc}"
            ) from exc
        return raw.decode("utf-8", errors="replace")

    # -- fetch ------------------------------------------------------------- #
    def fetch(
        self,
        station_id,
        when_utc: datetime,
        *,
        tolerance: timedelta | None = None,
    ) -> _api.IGRASounding:
        """Return the sounding for ``station_id`` nearest ``when_utc``.

        An exact nominal-hour match is preferred. Failing that, the nearest
        sounding within ``tolerance`` is used, which is how a request for a
        synoptic hour finds a special release an hour or two away.
        """
        station = self.resolve_station(station_id)
        when = _api._as_utc(when_utc)
        window = self.MATCH_TOLERANCE if tolerance is None else tolerance

        if not station.covers_year(when.year):
            raise _api.IGRAStationTimeUnavailableError(
                f"{station.id} has IGRA soundings for "
                f"{station.first_year}-{station.last_year}, not {when.year}"
            )

        choice = self._choose_archive(station, when)
        text = self._archive_text(choice)

        best: _api.IGRAHeader | None = None
        best_delta: timedelta | None = None
        for header in _api.iter_headers(text):
            valid = header.nominal_valid
            if valid is None:
                continue
            delta = abs(valid - when)
            if delta > window:
                continue
            if best_delta is None or delta < best_delta:
                best, best_delta = header, delta
                if delta == timedelta(0):
                    break

        if best is None:
            raise _api.IGRAStationTimeUnavailableError(
                f"IGRA has no {station.id} sounding within "
                f"{_api._describe(window)} of {when:%Y-%m-%d %H:%M} UTC "
                f"(searched the {choice.kind} archive)"
            )

        sounding = replace(
            _api.parse_sounding(text, best),
            archive_url=choice.url,
            archive_kind=choice.kind,
        )
        # The header position wins when it has one, because a mobile platform
        # reports where it actually was for this ascent. The station list is
        # the fallback for a header that omits it.
        if not (math.isfinite(sounding.lat) and math.isfinite(sounding.lon)):
            sounding = replace(
                sounding, lat=float(station.lat), lon=float(station.lon)
            )
        _api._LOGGER.info(
            "igra2.fetched station=%s valid=%s levels=%d archive=%s "
            "offset=%s dewpoint_from_rh=%d",
            sounding.station_id, sounding.valid.isoformat(),
            sounding.n_levels, choice.kind, _api._describe(best_delta or
                                                     timedelta(0)),
            sounding.dewpoint_from_rh_levels,
        )
        return sounding
