"""Compatibility import for :mod:`sharpmod.ui.maps.overlays.controllers`."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("sharpmod.ui.maps.overlays.controllers")
