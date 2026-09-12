"""Choose which overlays the sounding's locator inset carries.

The inset's paint path already draws whatever is attached to the profile
collection, in either representation: :class:`~sharpmod.map_overlays.OverlayLayer`
for polygons and :class:`~sharpmod.map_overlays.OverlayRaster` for imagery. What
was missing was a way to *say* which ones, so the inset silently inherited
whatever the picker happened to have switched on.

This module owns that decision, and only that decision, so the GUI control and
the render CLI cannot drift apart on it. Three rules shape it:

* **The convective outlook and a model field compose; radar does not.** Risk
  areas and a gridded field answer different questions and read fine together.
  Two reflectivity ramps over one another do not, and radar over a risk polygon
  buries the polygon, so selecting radar displaces everything else.
* **Radar is a nowcast.** There is no archive behind these frames, so a live
  frame beside a sounding from last Tuesday would state a currency the picture
  does not have. Radar is therefore offered only for a sounding valid inside
  :data:`RADAR_NOWCAST_WINDOW` of now.
* **A single site is preferred when it can cover what is drawn.** It carries far
  more detail than the mosaic, but only across its own box, so a wide box-mean
  extent falls back to the mosaic rather than showing one corner of the area.

Imports of the four provider modules are deliberately deferred into the
functions that need them. This module is reachable from picker start-up, which
:mod:`sharpmod.tests.test_gui_startup_optimization` requires to stay free of
NumPy and the heavier analysis stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FAMILY_RISK = "risk"
FAMILY_HRRR = "hrrr"
FAMILY_REPORTS = "reports"
FAMILY_RADAR_SITE = "radar-site"
FAMILY_RADAR_MOSAIC = "radar-mosaic"

#: Every family a selection may name, in the order a control should offer them.
#:
#: ``reports`` follows ``risk`` because it is read against it, not instead of it.
FAMILIES = (
    FAMILY_RISK,
    FAMILY_REPORTS,
    FAMILY_HRRR,
    FAMILY_RADAR_SITE,
    FAMILY_RADAR_MOSAIC,
)

#: Lightweight collection metadata used to reproduce the locator selection
#: when an analysis session is reopened. It stores only the parsed text spec;
#: fetched geometry and raster bytes remain outside portable session files.
SELECTION_META_KEY = "sharpmod_locator_overlay_spec"

#: Families that may be shown at the same time as each other.
COMBINABLE = frozenset({FAMILY_RISK, FAMILY_HRRR, FAMILY_REPORTS})

#: Families that displace every other selection.
EXCLUSIVE = frozenset({FAMILY_RADAR_SITE, FAMILY_RADAR_MOSAIC})

#: Families that are meaningless without another one on screen.
#:
#: Storm reports exist to be read *against* the outlook that anticipated them:
#: the question they answer is whether what was forecast is what happened, and a
#: scatter of report markers with no risk areas behind them cannot answer it --
#: it is just dots. So the outlook has to be showing before the reports can be,
#: and turning the outlook off takes the reports with it rather than leaving
#: them stranded without their reference.
REQUIRES = {FAMILY_REPORTS: FAMILY_RISK}

#: Overlay registry key per family.
#:
#: Written out rather than read from each provider module so that selecting an
#: overlay does not import all four. ``test_locator_overlay`` asserts every
#: entry still matches its module's ``OVERLAY_KEY``, so the duplication cannot
#: rot silently.
OVERLAY_KEYS = {
    FAMILY_RISK: "spc_outlook",
    FAMILY_HRRR: "hrrr_field",
    FAMILY_REPORTS: "storm_reports",
    FAMILY_RADAR_SITE: "radar_site",
    FAMILY_RADAR_MOSAIC: "radar_mosaic",
}

#: How far from now a sounding may be valid and still be shown live radar.
#:
#: One hour is not arbitrary: :func:`sharpmod.viz.hodo_locator
#: .overlay_rasters_for_widget` discards any raster whose valid time differs
#: from the sounding's by more than an hour. Gating here on the same window is
#: what makes an attached frame one that will actually be drawn, rather than one
#: silently dropped later with nothing to explain it.
RADAR_NOWCAST_WINDOW = timedelta(hours=1)


class LocatorOverlayError(ValueError):
    """A selection string could not be understood."""


@dataclass(frozen=True)
class Selection:
    """One chosen overlay: a family, and optionally which product within it."""

    family: str
    product: str | None = None

    def __post_init__(self) -> None:
        if self.family not in OVERLAY_KEYS:
            raise LocatorOverlayError(
                f"unknown locator overlay family {self.family!r}; "
                f"expected one of: {', '.join(FAMILIES)}"
            )

    @property
    def key(self) -> str:
        """The overlay registry key this selection attaches under."""
        return OVERLAY_KEYS[self.family]

    def spec(self) -> str:
        """Round-trip back to the text form :func:`parse` accepts."""
        return self.family if self.product is None else f"{self.family}:{self.product}"


def parse(text: str | None) -> tuple[Selection, ...]:
    """Parse a selection string such as ``"risk:torn,hrrr:refc"``.

    ``None``, an empty string and ``"none"`` all mean "draw a bare locator",
    which is the default: an inset that reaches for the network before being
    asked is a surprise, not a feature.
    """
    if text is None:
        return ()
    cleaned = text.strip()
    if not cleaned or cleaned.casefold() == "none":
        return ()

    selections: list[Selection] = []
    for part in cleaned.split(","):
        token = part.strip()
        if not token:
            continue
        family, separator, product = token.partition(":")
        family = family.strip().casefold()
        product = product.strip() if separator else ""
        selections.append(Selection(family, product or None))
    return enforce_exclusivity(tuple(selections))


def enforce_exclusivity(selections) -> tuple[Selection, ...]:
    """Drop whatever cannot be shown alongside the selections given.

    A radar selection wins over the composable families rather than the other
    way round: naming radar is the more specific request, and silently ignoring
    it would leave the control disagreeing with the picture.

    A family whose prerequisite is not also selected is dropped, so a dependent
    overlay can never end up on screen without the thing it is read against.
    """
    ordered: list[Selection] = []
    seen_families: set[str] = set()
    for selection in selections:
        if selection.family in seen_families:
            continue  # one slot per family; the first mention wins
        seen_families.add(selection.family)
        ordered.append(selection)

    radar = [item for item in ordered if item.family in EXCLUSIVE]
    if radar:
        # Radar displaces the composable families, and a prerequisite that has
        # just been displaced cannot satisfy anything either.
        return (radar[0],)

    kept = [item for item in ordered if item.family in COMBINABLE]
    present = {item.family for item in kept}
    satisfied = [item for item in kept if REQUIRES.get(item.family) in (None, *present)]
    # Returned in FAMILIES order rather than the order they were named, because
    # the inset draws them in the order it receives them. Point markers have to
    # land on top of the areas they annotate, and which of the two the user
    # happened to type first is not a statement about draw order.
    return tuple(sorted(satisfied, key=lambda item: FAMILIES.index(item.family)))


def missing_prerequisite(family: str, selections) -> str | None:
    """Return the family ``family`` still needs, or ``None`` if it is satisfied.

    Written for a control that wants to explain *why* an option is unavailable
    rather than simply greying it out with no reason given.
    """
    required = REQUIRES.get(family)
    if required is None:
        return None
    chosen = {item.family for item in selections}
    return None if required in chosen else required


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def radar_is_current(valid_time, *, now=None) -> bool:
    """Whether a live frame may honestly sit beside a sounding for this time."""
    when = _aware(valid_time)
    if when is None:
        # No resolvable time means nothing can be said about currency, and the
        # raster filter downstream lets an untimed frame through, so allow it.
        return True
    moment = _aware(now) or datetime.now(timezone.utc)
    return abs(when - moment) <= RADAR_NOWCAST_WINDOW


def resolve(
    selections, *, lat, lon, valid_time=None, now=None, half_span_deg=None
) -> tuple[Selection, ...]:
    """Reduce a selection to what is actually available for this sounding.

    ``half_span_deg`` is how far the drawn inset reaches from the point, which a
    box-mean sounding widens well past any one antenna's coverage. Passing it is
    what lets a site selection degrade to the mosaic instead of drawing a
    detailed frame over one corner of the area being summarised.
    """
    resolved: list[Selection] = []
    for selection in enforce_exclusivity(selections):
        if selection.family in EXCLUSIVE:
            if not radar_is_current(valid_time, now=now):
                continue  # nowcast only; say nothing rather than imply currency
            if selection.family == FAMILY_RADAR_SITE:
                resolved.append(_site_or_mosaic(selection, lat, lon, half_span_deg))
                continue
        resolved.append(selection)
    return tuple(resolved)


def _site_or_mosaic(selection, lat, lon, half_span_deg) -> Selection:
    """Return the site selection, or the mosaic when a site cannot serve it."""
    from sharpmod import radar_site

    try:
        nearest = radar_site.nearest_site(float(lat), float(lon))
    except (TypeError, ValueError):
        nearest = None
    if nearest is None:
        # Outside the network there is no antenna to ask; the mosaic still
        # covers CONUS and says so at its own resolution.
        return Selection(FAMILY_RADAR_MOSAIC)
    site = nearest[0]
    if half_span_deg is not None and float(half_span_deg) > site.half_span_deg:
        return Selection(FAMILY_RADAR_MOSAIC)
    return selection


def fetch(
    selection,
    *,
    lat,
    lon,
    valid_time=None,
    run=None,
    fxx=None,
    now=None,
    span_deg=None,
    opener=None,
    should_cancel=None,
):
    """Fetch one selection, returning an overlay layer, raster, or ``None``.

    ``None`` means "nothing for this sounding" -- cancelled, out of coverage, or
    genuinely absent. Provider failures raise, and it is the caller's job to
    decide that one broken overlay must not take a render with it;
    :func:`apply` does exactly that.
    """
    if selection.family == FAMILY_RISK:
        from sharpmod import spc_outlook

        when = _aware(valid_time)
        if when is None or not spc_outlook.covers_location(lat, lon):
            return None
        return spc_outlook.fetch_layer(
            when,
            now=now,
            product=selection.product or spc_outlook.DEFAULT_PRODUCT,
            opener=opener,
            should_cancel=should_cancel,
        )

    if selection.family == FAMILY_REPORTS:
        from sharpmod import storm_reports

        # Deliberately *not* gated to the present the way radar is. There is a
        # deep archive behind these reports -- a 2015 query answers -- so an
        # archived sounding can still be shown what actually happened around it,
        # which is the question reading reports beside an outlook is asking. The
        # outlook is the shorter-lived partner of the two: its own archive begins
        # in 2020.
        when = _aware(valid_time)
        return storm_reports.fetch_layer(
            around=None if radar_is_current(valid_time, now=now) else when,
            span_deg=span_deg,
            opener=opener,
            should_cancel=should_cancel,
        )

    if selection.family == FAMILY_HRRR:
        from sharpmod import hrrr_field

        return hrrr_field.fetch_field(
            selection.product,
            valid_time=_aware(valid_time),
            run=run,
            fxx=fxx,
            now=now,
            opener=opener,
            should_cancel=should_cancel,
        )

    if selection.family == FAMILY_RADAR_SITE:
        from sharpmod import radar_site

        nearest = radar_site.nearest_site(float(lat), float(lon))
        if nearest is None:
            return None
        # Pinned by identifier rather than by view: the inset has a point, not a
        # map extent, and the nearest antenna to that point is the subject.
        return radar_site.fetch_frame(
            selection.product,
            site_id=nearest[0].id,
            opener=opener,
            should_cancel=should_cancel,
        )

    from sharpmod import radar_mosaic

    return radar_mosaic.fetch_frame(
        selection.product, now=now, opener=opener, should_cancel=should_cancel
    )


def apply(
    collection,
    selections,
    *,
    lat,
    lon,
    valid_time=None,
    run=None,
    fxx=None,
    now=None,
    span_deg=None,
    half_span_deg=None,
    opener=None,
    should_cancel=None,
) -> tuple[str, ...]:
    """Attach the resolved selections to ``collection`` and detach the rest.

    Every family not selected is explicitly detached, because the transport seam
    composes by design: without this, switching from a field to radar would
    leave the field underneath it.

    Returns the keys actually attached. A provider that fails is treated as
    having nothing to offer -- the inset is an aid to reading a sounding, and it
    must never be the reason one will not draw.
    """
    from sharpmod import map_overlays

    wanted = resolve(
        selections,
        lat=lat,
        lon=lon,
        valid_time=valid_time,
        now=now,
        half_span_deg=half_span_deg,
    )
    attached: list[str] = []
    for selection in wanted:
        try:
            layer = fetch(
                selection,
                lat=lat,
                lon=lon,
                valid_time=valid_time,
                run=run,
                fxx=fxx,
                now=now,
                span_deg=span_deg,
                opener=opener,
                should_cancel=should_cancel,
            )
        except Exception:  # noqa: BLE001 - one overlay must not stop the render
            layer = None
        if layer is None:
            continue
        map_overlays.attach_locator_overlay(collection, layer, key=selection.key)
        attached.append(selection.key)

    for key in OVERLAY_KEYS.values():
        if key not in attached:
            map_overlays.attach_locator_overlay(collection, None, key=key)
    return tuple(attached)
