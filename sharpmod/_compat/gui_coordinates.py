"""Compatibility import; implementation lives in :mod:`sharpmod.ui.picker.coordinates`."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("sharpmod.ui.picker.coordinates")
