"""Legacy import alias for :mod:`sharpmod.ensemble_members`.

The implementation now lives in :mod:`sharpmod.analysis.ensemble_members`. This shim
aliases the old import name to that implementation so both paths share one module object
and the same public and compatibility attributes."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module('sharpmod.analysis.ensemble_members')
