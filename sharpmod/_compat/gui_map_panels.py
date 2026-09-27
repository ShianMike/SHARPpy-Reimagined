"""Compatibility import; implementation lives in :mod:`sharpmod.ui.maps.panels`."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("sharpmod.ui.maps.panels")
