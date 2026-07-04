"""SHARPpy Reimagined University of Wyoming (UWyo) upper-air sounding decoder.

This is the modernized ``UWyo_Decoder`` for the SHARPpy Reimagined fork (design.md,
"IO: UWyo_Decoder"). It fetches an observed upper-air sounding from the
University of Wyoming archive by station identifier and observation time,
decodes every reported level into the SHARPpy Reimagined core arrays, and builds a
:class:`sharpmod.sharptab.profile.Profile` (Requirements 7.1, 7.2).

Key behaviours (Requirement 7):

* **HTTPS with certificate verification, 30 s timeout.** Remote data is fetched
  through :func:`urllib.request.urlopen` with an :func:`ssl.create_default_context`
  context (server-certificate verification on) and a hard 30 second timeout, via
  the standard-library modern request interface -- no ``urlopen(cafile=...)``
  shim (Requirements 7.1, 7.6, 11.6).
* **Typed, descriptive errors -- never a partial Profile.** Retrieval, station
  lookup, missing station/time, and parse failures each raise a distinct typed
  error and never return a partial or incomplete :class:`Profile`
  (Requirements 7.4, 7.5, 7.6, 7.7):

  ==============================  =====================================
  Error                           Raised when
  ==============================  =====================================
  :class:`StationLookupError`     a station query matches zero or >1 stations (7.4)
  :class:`StationTimeUnavailableError`  the requested station/time is not archived (7.5)
  :class:`RetrievalError`         the service is unreachable / times out (7.6)
  :class:`SoundingParseError`     the response is not a valid sounding (7.7)
  ==============================  =====================================

* **Intermediate sounding representation.** :meth:`UWyo_Decoder.to_intermediate`
  / :meth:`UWyo_Decoder.from_intermediate` expose the same ``.npz``-shaped dict
  the HRRR point-sounding path uses (arrays ``pres, hght, tmpc, dwpc, wdir,
  wspd`` plus metadata), so a decode -> encode -> decode round-trip reproduces
  every reported level (Requirement 7.8).

The UWyo response is the classic fixed-width ``<PRE>`` HTML table; the decode
logic mirrors the legacy ``sharppy.io.uwyo_decoder`` column layout (7-char
fields, columns 0,1,2,3,6,7 -> pres/hght/tmpc/dwpc/wdir/wspd) while producing a
modern SHARPpy Reimagined ``Profile``.
"""

from __future__ import annotations

import math
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np

try:  # certifi is a declared runtime dependency; fall back gracefully.
    import certifi
    _CA_FILE = certifi.where()
except Exception:  # pragma: no cover - certifi always present in practice
    _CA_FILE = None

from sharpmod.sharptab import profile as _profile
from sharpmod.io import uwyo_catalog as _catalog

__all__ = [
    "UWyo_Decoder",
    "StationMeta",
    "StationLookupError",
    "StationTimeUnavailableError",
    "RetrievalError",
    "SoundingParseError",
    "UWYO_STATIONS",
]

#: Conversion factor from metres-per-second to knots. The modern UWyo
#: ``/wsgi/`` TEXT:LIST table reports wind SPEED in m/s (the retired
#: ``/cgi-bin/`` server used knots); the decoder converts to knots so the
#: :class:`Profile` always carries wind speed in knots (Requirement 7.2).
MS_TO_KT = 1.9438444924406046

__fmtname__ = "uwyo"
__classname__ = "UWyo_Decoder"


# --------------------------------------------------------------------------- #
# Typed errors (design.md, "Decoder Errors")
# --------------------------------------------------------------------------- #
class UWyoError(Exception):
    """Base class for every UWyo_Decoder error."""


class StationLookupError(UWyoError):
    """A station query resolved to zero or more than one station (Req 7.4)."""


class StationTimeUnavailableError(UWyoError):
    """The requested station identifier or time is not archived (Req 7.5)."""


class RetrievalError(UWyoError):
    """The UWyo service is unreachable or did not respond in time (Req 7.6)."""


class SoundingParseError(UWyoError):
    """The fetched response is not a parseable sounding (Req 7.7)."""


# --------------------------------------------------------------------------- #
# Station metadata (Req 7.3)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StationMeta:
    """Resolved single-station metadata (Requirement 7.3).

    Attributes
    ----------
    id : str
        UWyo station identifier (WMO number or call sign).
    name : str
        Human-readable station name.
    lat : float
        Latitude in degrees north.
    lon : float
        Longitude in degrees east (negative for west).
    elev_m : float
        Station elevation in metres MSL.
    """

    id: str
    name: str
    lat: float
    lon: float
    elev_m: float


#: A small embedded catalogue of common North-American UWyo upper-air stations.
#: Keyed by UWyo station number; each value is ``(name, lat, lon, elev_m)``.
#: The catalogue backs :meth:`UWyo_Decoder.resolve_station` so a query can be
#: resolved to exactly one station without a network round-trip.
UWYO_STATIONS: dict[str, tuple[str, float, float, float]] = {
    "72558": ("OAX Omaha/Valley, NE", 41.32, -96.37, 350.0),
    "72562": ("LBF North Platte, NE", 41.13, -100.68, 847.0),
    "72249": ("FWD Fort Worth, TX", 32.83, -97.30, 196.0),
    "72469": ("DNR Denver, CO", 39.77, -104.87, 1611.0),
    "72520": ("PIT Pittsburgh, PA", 40.53, -80.23, 360.0),
    "72403": ("IAD Washington Dulles, VA", 38.98, -77.47, 93.0),
    "72451": ("DDC Dodge City, KS", 37.77, -99.97, 790.0),
    "72357": ("OUN Norman, OK", 35.18, -97.44, 345.0),
    "72572": ("SLC Salt Lake City, UT", 40.77, -111.95, 1288.0),
    "74560": ("ILX Lincoln, IL", 40.15, -89.34, 178.0),
    "72694": ("SLE Salem, OR", 44.92, -123.01, 61.0),
    "72797": ("UIL Quillayute, WA", 47.95, -124.55, 62.0),
    "72776": ("TFX Great Falls, MT", 47.46, -111.38, 1130.0),
    "72214": ("TLH Tallahassee, FL", 30.45, -84.30, 18.0),
}


# --------------------------------------------------------------------------- #
# Decoder
# --------------------------------------------------------------------------- #
class UWyo_Decoder:
    """Fetch and decode University of Wyoming upper-air soundings.

    The decoder is intentionally *not* eager: constructing it performs no
    network I/O. Call :meth:`fetch` with a station identifier and observation
    time to retrieve and decode a sounding into a :class:`Profile`, or
    :meth:`resolve_station` to resolve a lookup query to single-station
    metadata.
    """

    #: Hard fetch timeout in seconds (Requirements 7.1, 7.6).
    FETCH_TIMEOUT = 30

    #: Base URL of the modern UWyo ``/wsgi/`` text-list sounding service
    #: (HTTPS, Requirement 11.6). The legacy ``/cgi-bin/sounding`` interface was
    #: retired in 2025; the new endpoint takes ``datetime``/``id``/``src``/
    #: ``type`` query parameters and reports wind SPEED in m/s.
    BASE_URL = "https://weather.uwyo.edu/wsgi/sounding"

    #: Default UWyo data source used when a station's source is unknown.
    DEFAULT_SRC = "FM35"

    #: Per-level missing-value sentinel used in the intermediate representation
    #: (matches the ``.npz`` / legacy decoder convention).
    MISSING = -9999.0

    #: Ordered core per-level field names.
    CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")

    def __init__(self, station_catalog: dict | None = None,
                 full_catalog: bool = False):
        """Create a decoder.

        Parameters
        ----------
        station_catalog : dict, optional
            Override the station catalogue used by :meth:`resolve_station`.
            Maps a station id to ``(name, lat, lon, elev_m)`` or
            ``(name, lat, lon, elev_m, src)``. Takes precedence over
            ``full_catalog``.
        full_catalog : bool, default False
            When True (and no explicit ``station_catalog`` is given), resolve
            against the **full** bundled UWyo catalogue
            (:mod:`sharpmod.io.uwyo_catalog`, hundreds of worldwide stations)
            so every UWyo station is choosable. When False, the small embedded
            :data:`UWYO_STATIONS` seed catalogue is used.
        """
        self._src: dict[str, str] = {}
        if station_catalog is not None:
            self._stations = {}
            for sid, rec in station_catalog.items():
                name, lat, lon, elev = rec[0], rec[1], rec[2], rec[3]
                self._stations[sid] = (name, lat, lon, elev)
                if len(rec) > 4 and rec[4]:
                    self._src[sid] = str(rec[4])
        elif full_catalog:
            self._stations = {}
            for row in _catalog.load_catalog():
                sid = row["id"]
                self._stations[sid] = (row["name"], row["lat"], row["lon"],
                                       float("nan"))
                self._src[sid] = row.get("src", self.DEFAULT_SRC)
        else:
            self._stations = dict(UWYO_STATIONS)

    # -- public API ---------------------------------------------------------- #
    def resolve_station(self, query) -> StationMeta:
        """Resolve ``query`` to exactly one station's metadata (Req 7.3, 7.4).

        The query is matched (case-insensitively) against station ids and
        names: an exact id match wins outright; otherwise a substring match
        against ids and names is used. If the query resolves to zero stations
        or to more than one station, a :class:`StationLookupError` is raised
        identifying the unresolved query and **no** station id is returned
        (Requirement 7.4).

        Parameters
        ----------
        query : str
            A station identifier or (part of) a station name.

        Returns
        -------
        StationMeta
            The single resolved station's id, name, lat, lon and elevation.
        """
        if query is None:
            raise StationLookupError("station query is empty")
        q = str(query).strip()
        if q == "":
            raise StationLookupError("station query is empty")

        # 1. Exact station-id match takes precedence and is unambiguous.
        if q in self._stations:
            return self._meta_for(q)

        ql = q.casefold()

        # 2. Exact (case-insensitive) name match.
        exact_name = [sid for sid, (name, *_rest) in self._stations.items()
                      if name.casefold() == ql]
        if len(exact_name) == 1:
            return self._meta_for(exact_name[0])
        if len(exact_name) > 1:
            raise StationLookupError(
                f"station query {query!r} matches multiple stations: "
                f"{', '.join(sorted(exact_name))}")

        # 3. Substring match against ids and names.
        matches = [
            sid for sid, (name, *_rest) in self._stations.items()
            if ql in sid.casefold() or ql in name.casefold()
        ]
        if len(matches) == 1:
            return self._meta_for(matches[0])
        if len(matches) == 0:
            raise StationLookupError(
                f"station query {query!r} matched no UWyo station")
        raise StationLookupError(
            f"station query {query!r} matched multiple UWyo stations: "
            f"{', '.join(sorted(matches))}")

    @staticmethod
    def list_stations() -> list[dict]:
        """Return every station in the full bundled UWyo catalogue.

        Each record is ``{"id", "name", "lat", "lon", "src"}``. Backed by
        :func:`sharpmod.io.uwyo_catalog.all_stations`, so the complete set of
        UWyo upper-air stations is choosable without a network round-trip
        (Requirement 7.3).
        """
        return _catalog.all_stations()

    @staticmethod
    def search_stations(query, limit: int | None = None) -> list[dict]:
        """Search the full bundled UWyo catalogue by id or name substring.

        Returns matching ``{"id", "name", "lat", "lon", "src"}`` records (an
        exact id match is returned alone). Useful for building a station picker
        over the entire UWyo catalogue.
        """
        return _catalog.search_stations(query, limit=limit)

    def fetch(self, station_id, when_utc: datetime, src: str | None = None):
        """Fetch and decode a UWyo sounding into a :class:`Profile`.

        Fetches the sounding for ``station_id`` at the observation time
        ``when_utc`` (interpreted as UTC) over HTTPS with certificate
        verification and a 30 second timeout, then decodes every reported level
        (Requirements 7.1, 7.2, 11.6).

        Parameters
        ----------
        station_id : str
            UWyo station identifier (e.g. ``"72558"``).
        when_utc : datetime.datetime
            The observation date and hour in UTC.

        Returns
        -------
        Profile
            A profile populated with pressure (hPa), height (m MSL), temperature
            (deg C), dewpoint (deg C), wind direction (deg) and wind speed (kt).

        Raises
        ------
        RetrievalError
            If the service cannot be reached or does not respond within the
            30 s timeout (Requirement 7.6).
        StationTimeUnavailableError
            If the station/time is not available from UWyo (Requirement 7.5).
        SoundingParseError
            If the response cannot be parsed as a valid sounding
            (Requirement 7.7).
        """
        if src is None:
            src = self._src.get(str(station_id), self.DEFAULT_SRC)
        url = self._build_url(station_id, when_utc, src)
        text = self._http_get(url)
        intermediate = self.decode_text(text)
        return self.from_intermediate(intermediate)

    # -- HTTP ---------------------------------------------------------------- #
    def _build_url(self, station_id, when_utc: datetime,
                   src: str | None = None) -> str:
        """Build the modern UWyo ``/wsgi/`` text-list request URL.

        The new server takes a single ``datetime`` (``YYYY-MM-DD HH:00:00``,
        UTC), the station ``id``, the data ``src`` (e.g. ``FM35`` / ``BUFR``),
        and the output ``type``.
        """
        if not isinstance(when_utc, datetime):
            raise StationTimeUnavailableError(
                f"observation time must be a datetime, got {when_utc!r}")
        params = {
            "datetime": when_utc.strftime("%Y-%m-%d %H:00:00"),
            "id": str(station_id),
            "src": src or self.DEFAULT_SRC,
            "type": "TEXT:LIST",
        }
        return "%s?%s" % (self.BASE_URL, urlencode(params))

    def _http_get(self, url: str) -> str:
        """GET ``url`` over verified HTTPS with the 30 s timeout (Req 7.1, 7.6).

        Any connection failure, timeout, or transport error is surfaced as a
        :class:`RetrievalError` (Requirement 7.6).
        """
        context = ssl.create_default_context(cafile=_CA_FILE)
        try:
            with urlopen(url, timeout=self.FETCH_TIMEOUT,
                         context=context) as resp:
                raw = resp.read()
        except (socket.timeout, TimeoutError) as exc:
            raise RetrievalError(
                f"UWyo request timed out after {self.FETCH_TIMEOUT}s: {url}"
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise RetrievalError(
                    f"UWyo request timed out after {self.FETCH_TIMEOUT}s: {url}"
                ) from exc
            raise RetrievalError(
                f"UWyo service could not be reached ({reason}): {url}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise RetrievalError(
                f"UWyo service could not be reached ({exc}): {url}") from exc
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - decode is very forgiving
            raise SoundingParseError(
                f"UWyo response could not be decoded as text: {exc}") from exc

    # -- decode -------------------------------------------------------------- #
    def decode_text(self, text: str) -> dict:
        """Decode a raw UWyo HTML response into the intermediate representation.

        Parses the fixed-width ``<PRE>`` sounding table and the ``<H2>`` title /
        station-latitude lines, returning the ``.npz``-shaped intermediate dict
        (see :meth:`to_intermediate`). Distinguishes an *unavailable*
        station/time (no data block, Requirement 7.5) from an *unparseable*
        response (Requirement 7.7).

        Parameters
        ----------
        text : str
            The raw HTML text returned by the UWyo service.

        Returns
        -------
        dict
            The intermediate sounding representation.
        """
        if text is None or text.strip() == "":
            raise SoundingParseError("UWyo response was empty")

        lines = text.split("\n")

        # Locate the first data <PRE> block. On the modern /wsgi/ server the
        # block is terminated by a bare "</PRE>"; the legacy server / test
        # fixture terminates it with "</PRE><H3>". Both are matched below.
        pre_idx = -1
        for i, line in enumerate(lines):
            if line.strip() == "<PRE>":
                pre_idx = i
                break

        if pre_idx == -1:
            # No data block present. If the service signalled that the
            # station/time is unavailable, surface that; otherwise the response
            # is simply not a sounding we can parse.
            if self._looks_unavailable(text):
                raise StationTimeUnavailableError(
                    "UWyo has no sounding for the requested station/time")
            raise SoundingParseError(
                "UWyo response did not contain a sounding data block")

        # Four header lines follow "<PRE>" (dashes / names / units / dashes);
        # the first data row is five lines below it.
        header_block = "\n".join(lines[pre_idx + 1:pre_idx + 5]).lower()
        bgn = pre_idx + 5

        # Wind-speed units: the modern server reports SPEED in m/s ("SPED" /
        # "m/s"); the legacy server / fixture reports knots ("SKNT" / "knot").
        in_knots = ("knot" in header_block) or ("sknt" in header_block)
        wspd_in_ms = (not in_knots) and (
            ("m/s" in header_block) or ("sped" in header_block))

        end = -1
        for i in range(bgn, len(lines)):
            if lines[i].strip().startswith("</PRE>"):
                end = i
                break
        if end == -1:
            raise SoundingParseError(
                "UWyo sounding data block was not terminated")
        if end <= bgn:
            raise SoundingParseError(
                "UWyo sounding data block was empty or malformed")

        cols = [[] for _ in self.CORE_FIELDS]
        # Column start indices in the 7-char fixed-width table for
        # pres, hght, tmpc, dwpc, wdir, wspd.
        field_cols = (0, 1, 2, 3, 6, 7)
        n_levels = 0
        for i in range(bgn, end):
            row = lines[i]
            if row.strip() == "":
                continue
            try:
                for k, j in enumerate(field_cols):
                    val = row[(7 * j):(7 * (j + 1))].strip()
                    cols[k].append(float(val) if val != "" else self.MISSING)
            except (ValueError, IndexError) as exc:
                raise SoundingParseError(
                    f"could not parse UWyo sounding level {i - bgn}: {exc}"
                ) from exc
            n_levels += 1

        if n_levels == 0:
            raise SoundingParseError(
                "UWyo sounding contained no reported levels")

        intermediate = {
            name: np.asarray(col, dtype=float)
            for name, col in zip(self.CORE_FIELDS, cols)
        }

        # Convert wind speed m/s -> knots on the modern server, leaving the
        # MISSING sentinel untouched (Requirement 7.2).
        if wspd_in_ms:
            ws = intermediate["wspd"]
            intermediate["wspd"] = np.where(
                ws == self.MISSING, self.MISSING, ws * MS_TO_KT)

        # Metadata: valid time, location label, latitude/longitude. Parsed from
        # whichever header style the response uses.
        valid, loc, lat, lon = self._parse_metadata(lines)

        intermediate["omeg"] = None
        intermediate["meta"] = {
            "loc": loc,
            "valid": valid,
            "lat": lat,
            "lon": lon,
            "model": "UWyo",
            "run": valid,
            "fxx": 0,
            "observed": True,
            "source": "uwyo",
        }
        return intermediate

    @staticmethod
    def _parse_metadata(lines):
        """Extract ``(valid, loc, lat, lon)`` from either header style.

        Handles the modern ``/wsgi/`` server (``<H1>Observations for Station
        NNN at HH UTC DD Mon YYYY</H1>``, ``<H3>NAME</H3>``, ``<I>Latitude: X
        Longitude: Y</I>``) and the legacy / fixture server (``<H2>LOC
        Observations at HHZ DD Mon YYYY</H2>`` plus a ``Station latitude:``
        line). Any field that cannot be parsed is returned as ``None`` / NaN.
        """
        valid = None
        loc = ""
        lat = float("nan")
        lon = float("nan")

        for line in lines:
            s = line.strip()
            low = s.lower()

            # Modern <H1> title with an embedded "HH UTC DD Mon YYYY" timestamp.
            if valid is None and s.startswith("<H1>") and " UTC " in s:
                try:
                    frag = s[4:].split("</H1>")[0]
                    when = frag.split(" at ", 1)[1].strip()
                    valid = datetime.strptime(when, "%H UTC %d %b %Y")
                except (ValueError, IndexError):
                    pass
                continue

            # Legacy / fixture <H2> title: "LOC   Observations at HHZ DD Mon".
            if s.startswith("<H2>") and s.endswith("</H2>"):
                if not loc:
                    loc = s[4:].split("Observations")[0].strip()
                if valid is None:
                    try:
                        valid = datetime.strptime(
                            s[-20:-5], "%HZ %d %b %Y")
                    except ValueError:
                        pass
                continue

            # Modern station-name subtitle.
            if not loc and s.startswith("<H3>") and s.endswith("</H3>"):
                name = s[4:-5].strip()
                if "station information" not in name.lower():
                    loc = name
                continue

            # Modern "<I>Latitude: X Longitude: Y</I>" line (may be prefixed by
            # markup such as "<BR/>"), so extract the numbers with a regex.
            if ("latitude" in low and "longitude" in low
                    and math.isnan(lat)):
                m = re.search(
                    r"latitude:\s*(-?\d+(?:\.\d+)?)\s*"
                    r"longitude:\s*(-?\d+(?:\.\d+)?)", low)
                if m:
                    try:
                        lat = float(m.group(1))
                        lon = float(m.group(2))
                    except ValueError:
                        pass
                continue

            # Legacy "Station latitude:" line.
            if "station latitude" in low and math.isnan(lat):
                try:
                    lat = float(s.split(":")[-1].strip())
                except ValueError:
                    pass
                continue

        return valid, loc, lat, lon

    @staticmethod
    def _looks_unavailable(text: str) -> bool:
        """Heuristically detect a UWyo 'no data for this station/time' page."""
        low = text.casefold()
        markers = (
            "can't get",
            "cannot get",
            "no data",
            "sorry",
            "unable to",
            "not available",
            "invalid",
        )
        return any(m in low for m in markers)

    # -- intermediate representation (Req 7.8) ------------------------------- #
    def to_intermediate(self, prof) -> dict:
        """Encode a :class:`Profile` to the ``.npz``-shaped intermediate dict.

        Produces the same array/metadata structure the HRRR ``.npz`` point
        sounding uses: arrays ``pres, hght, tmpc, dwpc, wdir, wspd`` (+ optional
        ``omeg``) plus a ``meta`` mapping. Masked levels are written back as the
        :attr:`MISSING` sentinel so a subsequent :meth:`from_intermediate`
        reproduces the mask (Requirement 7.8).
        """
        out: dict = {}
        for name in self.CORE_FIELDS:
            arr = np.ma.asarray(getattr(prof, name), dtype=float)
            out[name] = np.asarray(arr.filled(self.MISSING), dtype=float)
        omeg = getattr(prof, "omeg", None)
        if omeg is None:
            out["omeg"] = None
        else:
            omeg_arr = np.ma.asarray(omeg, dtype=float)
            out["omeg"] = np.asarray(omeg_arr.filled(self.MISSING), dtype=float)
        out["meta"] = dict(getattr(prof, "meta", {}) or {})
        return out

    def from_intermediate(self, data: dict):
        """Rebuild a :class:`Profile` from an intermediate dict (Req 7.2, 7.8).

        The inverse of :meth:`to_intermediate`. :attr:`MISSING` sentinels are
        converted to NaN so the resulting :class:`Profile` masks them, and the
        metadata mapping is attached verbatim.
        """
        missing_keys = [f for f in self.CORE_FIELDS if f not in data]
        if missing_keys:
            raise SoundingParseError(
                f"intermediate representation missing fields: "
                f"{', '.join(missing_keys)}")

        arrays = {}
        lengths = set()
        for name in self.CORE_FIELDS:
            arr = np.array(data[name], dtype=float)
            arr = np.where(arr == self.MISSING, np.nan, arr)
            arrays[name] = arr
            lengths.add(arr.size)
        if len(lengths) != 1:
            raise SoundingParseError(
                f"intermediate fields have mismatched lengths: {lengths}")

        omeg = data.get("omeg")
        if omeg is not None:
            omeg = np.array(omeg, dtype=float)
            omeg = np.where(omeg == self.MISSING, np.nan, omeg)

        meta = dict(data.get("meta", {}) or {})
        return _profile.create_profile(
            pres=arrays["pres"], hght=arrays["hght"], tmpc=arrays["tmpc"],
            dwpc=arrays["dwpc"], wdir=arrays["wdir"], wspd=arrays["wspd"],
            omeg=omeg, meta=meta,
        )

    # -- helpers ------------------------------------------------------------- #
    def _meta_for(self, sid: str) -> StationMeta:
        rec = self._stations[sid]
        name, lat, lon, elev_m = rec[0], rec[1], rec[2], rec[3]
        return StationMeta(id=sid, name=name, lat=float(lat), lon=float(lon),
                           elev_m=float(elev_m))
