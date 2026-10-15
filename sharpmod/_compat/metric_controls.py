"""Legacy import alias for :mod:`sharpmod.metric_controls`.

The implementation now lives in :mod:`sharpmod.analysis.metric_controls` (the
numeric profiles) and :mod:`sharpmod.ui.features.metric_controls` (the one
widget helper). This shim aliases the old import name so every path shares one
module object and the same public and compatibility attributes."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module('sharpmod.ui.features.metric_controls')
