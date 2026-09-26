"""Legacy import alias for :mod:`sharpmod.render`.

The implementation now lives in :mod:`sharpmod.rendering.cli`. This shim aliases the old
import name to that implementation so both paths share one module object and the same
public and compatibility attributes."""

from importlib import import_module as _import_module
from sys import modules as _modules

_implementation = _import_module("sharpmod.rendering.cli")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

_modules[__name__] = _implementation
