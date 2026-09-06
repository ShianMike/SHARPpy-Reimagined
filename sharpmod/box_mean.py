"""Average every extracted sounding in a box into one composite sounding.

A box gives you many soundings from one download. Reading them as a field map
answers "where is this largest"; reading them as a single mean profile answers
"what does the airmass over this area look like", which is the question a
forecaster usually has when drawing a box in the first place.

The output is an ordinary portable sounding, so it opens in the same workspace,
with the same overlays, town lookup, and parcel logic as a single-point fetch.
Nothing downstream needs to know it was averaged.

Four choices are worth knowing about, because each is a place a naive average
gets the physics wrong:

*Winds are averaged as components, never as speed and direction.* The mean of
350 degrees and 10 degrees is 0, not 180. Every member is resolved to ``u`` and
``v``, the components are averaged, and the result is converted back.

*Moisture is averaged as mixing ratio, not as dewpoint.* Dewpoint is nonlinear
in vapour pressure, so averaging it biases the column dry. Each member's
dewpoint becomes a mixing ratio through the application's own
:mod:`sharppy.sharptab.thermo`, the ratios are averaged, and the mean is
converted back.

*The averaged dewpoint is clamped to the averaged temperature.* Saturation
mixing ratio is convex in temperature, so by Jensen's inequality the mean of the
members' mixing ratios can exceed the saturation ratio at the mean temperature —
producing a supersaturated level out of members that were each subsaturated.
Those levels are clamped and counted rather than silently shipped.

*Only the layer every member shares is averaged.* Terrain varies across a box,
so the members do not all start at the same pressure. Averaging a level that
only the low-ground members reach would blend a real column with nothing and
invent a lapse rate at the boundary. The mean therefore begins at the highest
ground in the box, and the levels dropped at each end are reported.

One caveat cannot be engineered away, so it is stated instead: **the derived
parameters of the mean sounding are not the mean of the members' parameters.**
CAPE of the average column is not the average CAPE. Averaging smooths the
extremes, so a mean sounding is a description of the airmass, not a summary of
the worst case in the box. Use the field map when the extreme is the question.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

__all__ = [
    "BoxMeanError",
    "BoxMeanProfile",
    "MEAN_PROFILE_FIELDS",
    "MIN_MEAN_MEMBERS",
    "box_mean_badge_lines",
    "box_mean_profile",
    "mean_model_label",
    "write_box_mean_sounding",
]

#: Sentinel a portable sounding uses for an absent value.
MISSING = -9999.0

#: Anything at or below this is the sentinel rather than a reading.
_MISSING_LIMIT = -9998.0

#: The columns a portable sounding carries, and therefore the columns this
#: module has to produce for the result to open like any other sounding.
MEAN_PROFILE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg")

#: Fewer contributing soundings than this is not an area average, it is a point
#: with extra steps, and the caller should be told rather than handed a mean of
#: one.
MIN_MEAN_MEMBERS = 2


class BoxMeanError(Exception):
    """A box mean sounding cannot be built as asked."""


def mean_model_label(model, members) -> str:
    """Return the model name to display for a mean of ``members`` soundings.

    The Skew-T draws its title from the model name, so this is what puts the
    average on the plot itself. A mean column that looks exactly like a point
    sounding is the one outcome worth engineering against: every parcel, index,
    and hodograph on the page belongs to an average, and the reader has to be
    able to see that without checking where the window came from.
    """
    label = str(model or "").strip() or "MODEL"
    try:
        count = int(members)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return f"{label} box mean"
    return f"{label} box mean of {count}"


def box_mean_badge_lines(box_mean, members) -> tuple[str, ...]:
    """Return the on-plot callout for a mean sounding, or ``()`` for a point.

    Two lines, because there are two separate mistakes to prevent and the title
    alone has never prevented either. The first line says the column is an
    average of many soundings rather than one place. The second says its indices
    were computed *from* that average: they are not the average of the members'
    indices, so the CAPE printed on the page is the CAPE of the mean column and
    not the mean CAPE of the box. A forecaster who reads it the other way has
    read a number that does not exist anywhere in the data.

    Qt-independent on purpose -- the wording is a product decision and belongs
    beside :func:`mean_model_label`, while drawing it belongs to the renderer.
    """
    if not box_mean:
        return ()
    try:
        count = int(members)
    except (TypeError, ValueError):
        count = 0
    headline = (f"BOX MEAN \u00b7 {count} SOUNDINGS" if count > 0
                else "BOX MEAN")
    return (headline, "indices of the mean, not mean indices")


@dataclass(frozen=True)
class BoxMeanProfile:
    """One composite sounding averaged across a box, and how it was built."""

    pres: tuple[float, ...]
    hght: tuple[float, ...]
    tmpc: tuple[float, ...]
    dwpc: tuple[float, ...]
    wdir: tuple[float, ...]
    wspd: tuple[float, ...]
    omeg: tuple[float, ...]
    uwnd: tuple[float, ...]
    vwnd: tuple[float, ...]
    #: Centre of the averaged area, which is what the sounding represents.
    lat: float
    lon: float
    #: Soundings that actually contributed.
    members: int
    #: Files offered, so a caller can see how many were unreadable.
    requested: int
    #: Contributors at each surviving level, bottom first. A number below
    #: ``members`` means a field was unreadable for someone, not that the level
    #: was outside the shared layer -- that case is trimmed, not thinned.
    counts: tuple[int, ...]
    #: Levels the deepest member had that the shared layer could not use:
    #: below, because another member's ground was above them; above, because
    #: another member's profile stopped first.
    trimmed_below: int
    trimmed_above: int
    #: Levels whose averaged mixing ratio implied a dewpoint above the averaged
    #: temperature and was therefore clamped to it.
    clamped_dewpoints: int

    @property
    def levels(self) -> int:
        return len(self.pres)

    @property
    def surface_pressure_hpa(self) -> float:
        """Pressure of the lowest averaged level: the box's highest ground."""
        return float(self.pres[0])

    def arrays(self) -> dict:
        """Return the profile columns as numpy arrays for a portable ``.npz``."""
        return {
            "pres": np.asarray(self.pres, dtype=float),
            "hght": np.asarray(self.hght, dtype=float),
            "tmpc": np.asarray(self.tmpc, dtype=float),
            "dwpc": np.asarray(self.dwpc, dtype=float),
            "wdir": np.asarray(self.wdir, dtype=float),
            "wspd": np.asarray(self.wspd, dtype=float),
            "omeg": np.asarray(self.omeg, dtype=float),
            "uwnd": np.asarray(self.uwnd, dtype=float),
            "vwnd": np.asarray(self.vwnd, dtype=float),
        }

    def describe(self) -> str:
        """Return a reader-facing one-line summary."""
        text = (
            f"mean of {self.members} soundings, {self.levels} levels, "
            f"surface {self.surface_pressure_hpa:.0f} hPa"
        )
        if self.requested > self.members:
            text += f"; {self.requested - self.members} unreadable"
        trimmed = self.trimmed_below + self.trimmed_above
        if trimmed:
            text += (
                f"; {trimmed} level(s) outside the layer every sounding shares"
            )
        if self.clamped_dewpoints:
            text += f"; {self.clamped_dewpoints} dewpoint(s) clamped"
        return text


def _finite(values) -> np.ndarray:
    """Return ``values`` as float with sentinels turned into ``NaN``."""
    array = np.asarray(values, dtype=float).reshape(-1)
    return np.where(np.isfinite(array) & (array > _MISSING_LIMIT), array, np.nan)


def _read_member(path):
    """Return one member's columns, or ``None`` when it cannot be read.

    A single unreadable file must not end the average; it is counted instead, so
    the caller can say how many of the box's points contributed.
    """
    try:
        with np.load(os.fspath(path), allow_pickle=False) as data:
            columns = {
                name: _finite(data[name])
                for name in MEAN_PROFILE_FIELDS
                if name in data
            }
            for name in ("uwnd", "vwnd"):
                if name in data:
                    columns[name] = _finite(data[name])
            point = {}
            for name in ("lat", "lon"):
                if name in data:
                    value = np.asarray(data[name]).reshape(-1)
                    if value.size == 1 and np.isfinite(float(value[0])):
                        point[name] = float(value[0])
    except Exception:
        return None
    if "pres" not in columns:
        return None
    pressure = columns["pres"]
    usable = np.isfinite(pressure) & (pressure > 0.0)
    if int(np.count_nonzero(usable)) < 2:
        return None
    # A portable sounding is written surface-first. Sorting by descending
    # pressure rather than trusting the order keeps a hand-built or re-ordered
    # archive from producing a scrambled ladder.
    order = np.argsort(-pressure[usable])
    member = {
        name: column[usable][order]
        for name, column in columns.items()
        if column.size == pressure.size
    }
    if "pres" not in member:
        return None
    member.update(point)
    return member


def _components(member) -> tuple[np.ndarray, np.ndarray]:
    """Return a member's wind as ``(u, v)``.

    Prefers the components the archive already carries; falls back to resolving
    speed and direction. Averaging direction directly is never correct, so this
    is the only wind path.
    """
    levels = member["pres"].size
    have_u = "uwnd" in member and "vwnd" in member
    if have_u:
        u = member["uwnd"]
        v = member["vwnd"]
        if u.size == levels and v.size == levels:
            return u, v
    speed = member.get("wspd")
    direction = member.get("wdir")
    if speed is None or direction is None:
        blank = np.full(levels, np.nan)
        return blank, blank.copy()
    radians = np.radians(np.asarray(direction, dtype=float))
    # Meteorological convention: direction is where the wind comes *from*.
    u = -speed * np.sin(radians)
    v = -speed * np.cos(radians)
    return u, v


def _interpolate(target_log, source_log, values) -> np.ndarray:
    """Interpolate ``values`` onto ``target_log`` in log-pressure.

    Levels outside the member's own range become ``NaN`` rather than being
    extrapolated: a member that does not reach a level must not vote on it.
    """
    usable = np.isfinite(source_log) & np.isfinite(values)
    if int(np.count_nonzero(usable)) < 2:
        return np.full(target_log.size, np.nan)
    source = source_log[usable]
    column = values[usable]
    # ``np.interp`` needs an increasing coordinate; log-pressure decreases
    # upward, so both are reversed together.
    order = np.argsort(source)
    result = np.interp(
        target_log, source[order], column[order],
        left=np.nan, right=np.nan,
    )
    return np.asarray(result, dtype=float)


def _nanmean(stack) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(mean, count)`` down a stack of columns, ignoring ``NaN``."""
    array = np.asarray(stack, dtype=float)
    present = np.isfinite(array)
    counts = present.sum(axis=0).astype(int)
    totals = np.where(present, array, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore"):
        mean = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)
    return mean, counts


def _reference_member(members, lat, lon):
    """Return the member whose levels become the shared vertical ladder.

    The deepest member wins. Every member comes from the same model and so from
    the same set of pressure levels; the only thing that differs between their
    ladders is how much terrain truncated the bottom. Taking the deepest one
    therefore keeps the model's full vertical resolution *and* makes the reported
    trim counts describe the whole terrain effect, instead of depending on which
    member happened to be nearest the centre.

    Ties break toward the centre, which is the member that best represents the
    area if the ladders really do differ.
    """
    scale = (
        float(np.cos(np.radians(float(lat)))) if lat is not None else 1.0
    )

    def rank(member):
        levels = int(member["pres"].size)
        mlat = member.get("lat")
        mlon = member.get("lon")
        if lat is None or lon is None or mlat is None or mlon is None:
            distance = 0.0
        else:
            # Plain squared degrees with a cosine weight on longitude: the
            # members are a few grid spacings apart, so a great-circle distance
            # would not change the ordering.
            distance = (mlat - lat) ** 2 + ((mlon - lon) * scale) ** 2
        return (-levels, distance)

    return min(members, key=rank)


def box_mean_profile(
    outputs,
    *,
    lat=None,
    lon=None,
    min_members=MIN_MEAN_MEMBERS,
) -> BoxMeanProfile:
    """Average every readable sounding in ``outputs`` into one profile.

    ``outputs`` is an iterable of ``.npz`` paths, or a mapping of anything to
    them, which is the shape a box extraction already produces.

    ``lat``/``lon`` are the centre the mean represents. The shared vertical
    ladder is taken from the deepest member (see :func:`_reference_member`), so
    the result keeps the model's own vertical resolution rather than being
    resampled onto a coarser grid and looking unlike a real sounding.
    """
    if isinstance(outputs, Mapping):
        paths = [value for _key, value in sorted(outputs.items())]
    elif isinstance(outputs, (str, bytes)):
        raise BoxMeanError("outputs must be a collection of .npz paths")
    elif isinstance(outputs, Sequence):
        paths = list(outputs)
    else:
        paths = list(outputs)
    requested = len(paths)
    if requested == 0:
        raise BoxMeanError("no soundings were given to average")

    members = [member for member in (_read_member(p) for p in paths) if member]
    if len(members) < max(1, int(min_members)):
        raise BoxMeanError(
            f"only {len(members)} of {requested} sounding(s) could be read; "
            f"at least {int(min_members)} are needed to average an area"
        )

    reference = _reference_member(members, lat, lon)
    ladder = reference["pres"]

    # The shared layer: no lower than the highest ground, no higher than the
    # shallowest profile's top. Averaging outside it would blend a real column
    # with nothing.
    floor = min(float(member["pres"][0]) for member in members)
    ceiling = max(float(member["pres"][-1]) for member in members)
    inside = (ladder <= floor + 1e-6) & (ladder >= ceiling - 1e-6)
    trimmed_below = int(np.count_nonzero(ladder > floor + 1e-6))
    trimmed_above = int(np.count_nonzero(ladder < ceiling - 1e-6))
    ladder = ladder[inside]
    if ladder.size < 2:
        raise BoxMeanError(
            "these soundings share fewer than two pressure levels; the box "
            "spans too much terrain to average as one column"
        )

    target_log = np.log10(ladder)
    stacks: dict[str, list] = {
        name: [] for name in ("hght", "tmpc", "mixr", "omeg", "u", "v")
    }
    for member in members:
        source_log = np.log10(member["pres"])
        for name in ("hght", "tmpc", "omeg"):
            column = member.get(name)
            if column is None:
                stacks[name].append(np.full(ladder.size, np.nan))
                continue
            stacks[name].append(_interpolate(target_log, source_log, column))
        stacks["mixr"].append(
            _interpolate(
                target_log, source_log, _member_mixing_ratio(member))
        )
        u, v = _components(member)
        stacks["u"].append(_interpolate(target_log, source_log, u))
        stacks["v"].append(_interpolate(target_log, source_log, v))

    height, height_counts = _nanmean(stacks["hght"])
    temperature, temperature_counts = _nanmean(stacks["tmpc"])
    mixing_ratio, moisture_counts = _nanmean(stacks["mixr"])
    omega, _omega_counts = _nanmean(stacks["omeg"])
    u_mean, wind_counts = _nanmean(stacks["u"])
    v_mean, _v_counts = _nanmean(stacks["v"])

    # A level needs a pressure, a height, and a temperature to be a level at
    # all. Wind and omega may be absent without making the column unusable.
    keep = (
        np.isfinite(height) & np.isfinite(temperature)
        & (height_counts > 0) & (temperature_counts > 0)
    )
    if int(np.count_nonzero(keep)) < 2:
        raise BoxMeanError(
            "the averaged column has fewer than two usable levels")
    ladder = ladder[keep]
    height = height[keep]
    temperature = temperature[keep]
    mixing_ratio = mixing_ratio[keep]
    omega = omega[keep]
    u_mean = u_mean[keep]
    v_mean = v_mean[keep]
    counts = np.maximum(temperature_counts[keep], height_counts[keep])

    dewpoint, clamped = _dewpoint_from_mixing_ratio(
        ladder, mixing_ratio, temperature)

    # The portable contract wants a strictly decreasing pressure ladder and a
    # strictly increasing height ladder. Averaging preserves both, but a
    # duplicated reference level or two members whose heights cross would not,
    # so it is enforced rather than assumed.
    ladder, height, keep_monotonic = _strictly_stacked(ladder, height)
    if ladder.size < 2:
        raise BoxMeanError(
            "the averaged column is not vertically ordered after averaging")
    temperature = temperature[keep_monotonic]
    dewpoint = dewpoint[keep_monotonic]
    omega = omega[keep_monotonic]
    u_mean = u_mean[keep_monotonic]
    v_mean = v_mean[keep_monotonic]
    counts = counts[keep_monotonic]

    speed, direction = _speed_direction(u_mean, v_mean)
    centre_lat, centre_lon = _centre(members, lat, lon)

    return BoxMeanProfile(
        pres=tuple(float(value) for value in ladder),
        hght=tuple(float(value) for value in height),
        tmpc=tuple(_or_missing(value) for value in temperature),
        dwpc=tuple(_or_missing(value) for value in dewpoint),
        wdir=tuple(_or_missing(value) for value in direction),
        wspd=tuple(_or_missing(value) for value in speed),
        omeg=tuple(_or_missing(value) for value in omega),
        uwnd=tuple(_or_missing(value) for value in u_mean),
        vwnd=tuple(_or_missing(value) for value in v_mean),
        lat=float(centre_lat),
        lon=float(centre_lon),
        members=len(members),
        requested=requested,
        counts=tuple(int(value) for value in counts),
        trimmed_below=trimmed_below,
        trimmed_above=trimmed_above,
        clamped_dewpoints=int(clamped),
    )


def _member_mixing_ratio(member) -> np.ndarray:
    """Return one member's mixing ratio (g/kg) from its dewpoint."""
    from sharppy.sharptab import thermo

    pressure = member["pres"]
    dewpoint = member.get("dwpc")
    if dewpoint is None:
        return np.full(pressure.size, np.nan)
    usable = np.isfinite(pressure) & (pressure > 0.0) & np.isfinite(dewpoint)
    result = np.full(pressure.size, np.nan)
    if not np.any(usable):
        return result
    with np.errstate(all="ignore"):
        ratio = thermo.mixratio(pressure[usable], dewpoint[usable])
    ratio = np.asarray(ratio, dtype=float)
    result[usable] = np.where(np.isfinite(ratio) & (ratio > 0.0), ratio, np.nan)
    return result


def _dewpoint_from_mixing_ratio(
    pressure, mixing_ratio, temperature,
) -> tuple[np.ndarray, int]:
    """Convert averaged mixing ratio back to dewpoint, clamped to ``<= T``.

    Saturation mixing ratio is convex in temperature, so the mean of several
    subsaturated members can imply saturation at the mean temperature. Those
    levels are clamped to the temperature and counted.
    """
    from sharppy.sharptab import thermo

    result = np.full(np.asarray(pressure).size, np.nan)
    usable = (
        np.isfinite(pressure) & (np.asarray(pressure) > 0.0)
        & np.isfinite(mixing_ratio) & (np.asarray(mixing_ratio) > 0.0)
    )
    if np.any(usable):
        with np.errstate(all="ignore"):
            converted = thermo.temp_at_mixrat(
                np.asarray(mixing_ratio)[usable], np.asarray(pressure)[usable])
        converted = np.asarray(converted, dtype=float)
        result[usable] = np.where(np.isfinite(converted), converted, np.nan)
    clamped = 0
    both = np.isfinite(result) & np.isfinite(temperature)
    excess = both & (result > temperature)
    if np.any(excess):
        clamped = int(np.count_nonzero(excess))
        result = np.where(excess, temperature, result)
    return result, clamped


def _speed_direction(u, v) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(speed, direction)`` for averaged wind components."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    speed = np.hypot(u, v)
    with np.errstate(invalid="ignore"):
        direction = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    both = np.isfinite(u) & np.isfinite(v)
    speed = np.where(both, speed, np.nan)
    direction = np.where(both, direction, np.nan)
    # A calm mean -- opposing members cancelling -- has no direction to report,
    # and 0 degrees would read as due north rather than as "no vector".
    calm = both & (speed < 1e-6)
    direction = np.where(calm, 0.0, direction)
    return speed, direction


def _strictly_stacked(pressure, height):
    """Keep the longest run with pressure falling and height rising."""
    pressure = np.asarray(pressure, dtype=float)
    height = np.asarray(height, dtype=float)
    keep = np.zeros(pressure.size, dtype=bool)
    last_p = None
    last_h = None
    for index in range(pressure.size):
        p = float(pressure[index])
        h = float(height[index])
        if last_p is not None and not (p < last_p and h > last_h):
            continue
        keep[index] = True
        last_p, last_h = p, h
    return pressure[keep], height[keep], keep


def _centre(members, lat, lon) -> tuple[float, float]:
    """Return the centre the mean represents."""
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    lats = [m["lat"] for m in members if m.get("lat") is not None]
    lons = [m["lon"] for m in members if m.get("lon") is not None]
    if not lats or not lons:
        raise BoxMeanError(
            "these soundings carry no coordinates, so the centre of the "
            "averaged area is unknown; pass lat and lon"
        )
    radians = np.deg2rad(np.asarray(lons, dtype=float))
    mean_lon = np.degrees(np.arctan2(
        np.mean(np.sin(radians)), np.mean(np.cos(radians))))
    # Keep the portable coordinate in the application's conventional
    # [-180, 180) range.  Unlike an arithmetic mean, this leaves a box around
    # the antimeridian at the antimeridian rather than moving it to Greenwich.
    mean_lon = (float(mean_lon) + 180.0) % 360.0 - 180.0
    return float(np.mean(lats)), mean_lon


def _or_missing(value) -> float:
    """Return ``value``, or the portable sentinel when it is not finite."""
    number = float(value)
    return number if np.isfinite(number) else MISSING


def write_box_mean_sounding(
    profile: BoxMeanProfile,
    path,
    *,
    model,
    run_time,
    valid_time,
    fxx=0,
    loc=None,
    member=None,
    model_key=None,
    spacing_km=None,
    box=None,
) -> str:
    """Write ``profile`` as a portable sounding pair and return the ``.npz``.

    Emits the same archive a single-point model fetch does -- the seven profile
    columns plus ``valid``/``run``/``loc``/``lat`` -- so the mean opens through
    the ordinary decode path and inherits the outlook overlay, the town lookup,
    and the parcel logic without any of them knowing it was averaged.

    Leaving ``loc`` unset writes a coordinate label, which is exactly what the
    single-point path does when the user types no label, and is what makes the
    automatic town lookup fire for the centre of the box.
    """
    from sharpmod.tools.era5_extract import (
        _atomic_write_json,
        _atomic_write_npz,
        _quiet_remove,
    )

    if not isinstance(profile, BoxMeanProfile):
        raise BoxMeanError("profile must be a BoxMeanProfile")
    label = str(model or "").strip() or "MODEL"
    try:
        run_str = run_time.strftime("%Y-%m-%d %H:%M")
        valid_str = valid_time.strftime("%Y-%m-%d %H:%M")
    except AttributeError as exc:
        raise BoxMeanError(
            "run_time and valid_time must be datetimes") from exc

    # The model name is what the Skew-T prints in its own title, so the fact that
    # this column is an average of many rides along with it. Without this the
    # plot is indistinguishable from an ordinary point sounding, which is the one
    # thing a mean must never be mistaken for.
    display_model = mean_model_label(label, profile.members)

    loc_label = str(loc).strip() if loc and str(loc).strip() else (
        f"{label} {profile.lat:.2f}, {profile.lon:.2f}"
    )

    arrays = dict(profile.arrays())
    arrays.update({
        "lat": float(profile.lat),
        "lon": float(profile.lon),
        "loc": loc_label,
        "model": display_model,
        "run": run_str,
        "valid": valid_str,
        "fxx": int(fxx),
        "observed": False,
    })

    meta = {
        "model": display_model,
        # The plain product name is kept separately so provenance stays
        # machine-readable even though the displayed name carries the average.
        "model_label": label,
        "loc": loc_label,
        "selected_lat": float(profile.lat),
        "selected_lon": float(profile.lon),
        "run": run_str,
        "valid": valid_str,
        "fxx": int(fxx),
        "observed": False,
        "npz": os.path.abspath(os.fspath(path)),
        "levels": profile.levels,
        "decoder": "box mean of model point soundings",
        # Every member satisfied the verified surface contract when it was
        # extracted, and the mean starts at the highest ground in the box, so no
        # averaged level sits below any member's terrain.
        "surface_merged": True,
        "below_ground_levels_removed": True,
        "surface_pressure_hpa": profile.surface_pressure_hpa,
        "box_mean": True,
        "box_mean_members": profile.members,
        "box_mean_requested": profile.requested,
        "box_mean_level_counts": list(profile.counts),
        "box_mean_trimmed_below": profile.trimmed_below,
        "box_mean_trimmed_above": profile.trimmed_above,
        "box_mean_clamped_dewpoints": profile.clamped_dewpoints,
        "box_mean_note": (
            "Winds averaged as u/v components; moisture averaged as mixing "
            "ratio then converted back and clamped to the mean temperature. "
            "Derived parameters of this sounding are not the mean of the "
            "members' parameters."
        ),
        "cache_hit": False,
    }
    if model_key:
        meta["model_key"] = str(model_key)
    if member is not None:
        meta["member"] = str(member)
    if spacing_km is not None:
        meta["box_mean_spacing_km"] = float(spacing_km)
    if box is not None:
        meta["box_mean_bounds"] = [float(value) for value in box]

    target = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    _atomic_write_npz(target, arrays)
    sidecar = os.path.splitext(target)[0] + ".json"
    try:
        _atomic_write_json(sidecar, meta)
    except BaseException:
        _quiet_remove(target)
        raise
    return target
