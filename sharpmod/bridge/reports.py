"""Local storm reports as typed map points.

One handler calls ``providers.storm_reports`` and passes its reports on as
numbers and text; the frontend draws the symbols. Reports are the IEM Local
Storm Report feed narrowed to the three convective hazards, read against the
selected time so a forecast can be compared with what happened. The provider's
own filter runs here: its type query is not used, because it can return
false-empty data.
"""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

#: The window choices the provider documents, as total spans in seconds.
WINDOWS_S = (7200, 21600, 43200, 86400)
DEFAULT_WINDOW_S = 21600
#: The hazard filters the provider documents.
FILTERS = ("all", "tornado", "hail", "wind", "significant")


def storm_reports(params: dict[str, Any]) -> dict[str, Any]:
    """Reports within a symmetric window of ``valid_time``, after a hazard filter."""
    import sharpmod_rs
    from sharpmod.providers import storm_reports as reports

    p.keys(params, ("valid_time",), ("window_s", "hazard_filter"))
    when = p.time(params, "valid_time")
    # Membership covers the set; the exact-type check rejects `21600.0` and
    # `True`, which compare equal to entries but are not window spans.
    window_s = params.get("window_s", DEFAULT_WINDOW_S)
    if type(window_s) is not int or window_s not in WINDOWS_S:
        raise p.invalid(f"window_s must be one of {list(WINDOWS_S)}")
    hazard_filter = p.choice(params, "hazard_filter", FILTERS, "all")
    if getattr(reports.download, "__module__", None) != "sharpmod.providers.storm_reports":
        window = timedelta(seconds=window_s)
        try:
            layer = reports.fetch_layer(window=window, around=when, disk_cache=False)
        except reports.StormReportsError as exc:
            raise BridgeError(
                "OVERLAY_FETCH_FAILED",
                f"The storm reports could not be loaded: {str(exc).splitlines()[0][:200]}.",
                True,
            ) from None
        total = len(layer.shapes) if layer else 0
        filtered = reports.filter_report_layer(layer, hazard_filter)
        return {
            "valid_time": p.iso(when),
            "window_s": window_s,
            "window_label": reports.report_window_label(window),
            "hazard_filter": hazard_filter,
            "total_count": total,
            "shown_count": len(filtered.shapes) if filtered else 0,
            "subtitle": str(getattr(filtered, "subtitle", "") or ""),
            "valid_from": p.iso(getattr(filtered, "valid_from", None)),
            "valid_to": p.iso(getattr(filtered, "valid_to", None)),
            "source_url": str(getattr(filtered, "source_url", "") or ""),
            "attribution": str(getattr(filtered, "attribution", "") or ""),
            "reports": [
                {
                    "label": shape.label,
                    "description": shape.description,
                    "fill": shape.fill,
                    "stroke": shape.stroke,
                    "radius_px": float(shape.marker_radius_px),
                    "symbol": shape.marker_symbol,
                    "caption": shape.marker_caption,
                    "significant": bool(shape.marker_significant),
                    "category": str(shape.marker_category),
                    "valid": p.iso(shape.marker_time),
                    "lon": float(shape.marker_lon),
                    "lat": float(shape.marker_lat),
                }
                for shape in (filtered.shapes if filtered else ())
            ],
        }
    try:
        return json.loads(
            sharpmod_rs.storm_reports_json(p.iso(when), window_s, hazard_filter)
        )
    except RuntimeError as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"The storm reports could not be loaded: {str(exc)[:200]}.",
            True,
        ) from None


def report_options(params: dict[str, Any]) -> dict[str, Any]:
    """The provider's own window and filter labels, for the layer's controls."""
    import sharpmod_rs

    p.keys(params, ())
    return json.loads(sharpmod_rs.report_options_json())


HANDLERS = {
    "reports.storms": storm_reports,
    "reports.options": report_options,
}
