"""Compatibility alias for :mod:`sharpmod.ui.picker.wrf`."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("sharpmod.ui.picker.wrf")
