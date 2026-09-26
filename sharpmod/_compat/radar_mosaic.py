"""Legacy import alias for :mod:`sharpmod.radar_mosaic`.

The implementation now lives in :mod:`sharpmod.providers.radar_mosaic`. This shim
aliases the old import name to that implementation so both paths share one module object
and the same public and compatibility attributes."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module('sharpmod.providers.radar_mosaic')
