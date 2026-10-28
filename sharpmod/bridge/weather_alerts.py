"""Validated NWS warning/watch snapshot requests."""

import json

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError


def current(params):
    import sharpmod_rs

    p.keys(params, ("valid_time",))
    when = p.time(params, "valid_time")
    try:
        return json.loads(sharpmod_rs.weather_alerts(p.iso(when)))
    except ValueError as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"NWS warnings and watches could not be loaded: {str(exc)[:250]}",
            not isinstance(exc, sharpmod_rs.WeatherHistoryUnavailable),
        ) from None


HANDLERS = {"alerts.weather": current}
