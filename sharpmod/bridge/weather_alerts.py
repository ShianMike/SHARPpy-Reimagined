"""Validated NWS warning/watch snapshot requests."""

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import weather_alerts


def current(params):
    p.keys(params, ("valid_time",))
    when = p.time(params, "valid_time")
    try:
        return weather_alerts.fetch(when)
    except (
        weather_alerts.AlertError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        AttributeError,
    ) as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"NWS warnings and watches could not be loaded: {str(exc)[:250]}",
            not isinstance(exc, weather_alerts.HistoryUnavailable),
        ) from None


HANDLERS = {"alerts.weather": current}
