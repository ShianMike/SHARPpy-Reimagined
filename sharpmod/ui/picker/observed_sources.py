"""Observed sounding provider choices exposed by the picker."""

from __future__ import annotations

#: Selectable observed-sounding sources as ``(key, label, tooltip)``, in the
#: order the control rail offers them.
#:
#: Restated here instead of read from :mod:`sharpmod.providers.observations` because that
#: module imports NumPy at module scope and this rail is built before first
#: paint -- the same reason the HRRR field catalogue is imported lazily.
#: ``test_gui_observed_source.py`` asserts these keys equal ``("auto",) +
#: observations.registered_provider_keys()``, so the restatement cannot drift
#: away from the registry unnoticed.
OBSERVED_SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "auto",
        "Automatic (UWyo, then IEM)",
        "Try the University of Wyoming archive, then the Iowa Environmental "
        "Mesonet. Whichever answers is recorded; levels are never combined "
        "between archives.",
    ),
    (
        "uwyo",
        "University of Wyoming only",
        "Use only the University of Wyoming upper-air archive.",
    ),
    (
        "iem",
        "Iowa Environmental Mesonet only",
        "Use only the Iowa Environmental Mesonet RAOB archive.",
    ),
    (
        "igra2",
        "NOAA IGRA v2 (deep archive)",
        "NOAA's quality-assured global radiosonde archive: about 2,900 stations, "
        "some with a record reaching back over a century. It publishes one "
        "archive per station rather than per sounding, so the first request for "
        "a station downloads that archive and later ones are served from disk.",
    ),
)

#: Default source: the established University of Wyoming then IEM behaviour.
DEFAULT_OBSERVED_SOURCE = "auto"
