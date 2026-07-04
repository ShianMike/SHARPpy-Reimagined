"""Qt6/PySide6 backward-compatibility shim for the vendored ``sharppy.viz`` stack.

The pip-installed upstream ``sharppy.viz`` widgets were written for Qt5/PySide2
and access Qt enum members in the *unscoped* style -- e.g. ``QPainter.Antialiasing``
(class access) and, more commonly, ``qp.Antialiasing`` (instance access). Under
Qt6/PySide6 those members live only on nested *scoped* enum types
(``QPainter.RenderHint.Antialiasing``, ``Qt.AlignmentFlag.AlignCenter``,
``QFont.Weight.Bold`` ...), so the legacy access raises ``AttributeError`` -- the
documented "vendored Qt6 enum seam" that blocks headless rendering.

Importing this module and calling :func:`apply` flattens every nested scoped
enum back onto its owning Qt class, restoring the unscoped attribute names. By
normal class-attribute lookup this also makes the *instance* form (``qp.Antialiasing``)
resolve. It never overwrites an existing attribute (so scoped access and real
methods are untouched), is idempotent, and requires no edits to the vendored
package.

This is the root-cause fix for the enum half of the seam: SHARPpy Reimagined's own widgets
already use correct Qt6 scoped access, so the shim exists solely to let the
vendored widget stack paint under PySide6.
"""

from __future__ import annotations

import enum

__all__ = ["apply"]

_APPLIED = False


def _enum_member_map(cls) -> dict:
    """Return ``{bare_name: scoped_member}`` for every nested enum of ``cls``.

    Collects the members of each nested ``enum.Enum`` subclass found on ``cls``
    (e.g. ``QPainter.RenderHint`` -> ``{"Antialiasing": RenderHint.Antialiasing,
    ...}``). The first member wins on a name collision across nested enums.
    """
    mapping: dict = {}
    for attr_name in dir(cls):
        try:
            attr = getattr(cls, attr_name)
        except Exception:
            continue
        if isinstance(attr, type) and issubclass(attr, enum.Enum):
            for member in attr:
                mapping.setdefault(member.name, member)
    return mapping


def _flatten_class_enums(cls) -> None:
    """Restore Qt5-style unscoped enum access on ``cls`` for Qt6/PySide6.

    Two access styles the vendored widgets use must both work:

    * **Class access** (``QPainter.Antialiasing``, ``Qt.AlignCenter``) -- each
      scoped-enum member is bound under its bare name directly on ``cls`` unless
      that name is already taken.
    * **Instance access** (``qp.Antialiasing``) -- Shiboken instances do *not*
      fall back to dynamically-injected class attributes, so a ``__getattr__``
      resolving the bare enum names is installed on ``cls`` as well. It only
      fires on an attribute miss and re-raises ``AttributeError`` for genuinely
      unknown names, so real methods/attributes are untouched.
    """
    mapping = _enum_member_map(cls)
    if not mapping:
        return

    # Class access: bind the bare names onto the class.
    for name, member in mapping.items():
        if not hasattr(cls, name):
            try:
                setattr(cls, name, member)
            except (AttributeError, TypeError):
                # Some C++ types refuse new attributes; skip silently.
                pass

    # Instance access: install a miss-only __getattr__ fallback (idempotent).
    existing = cls.__dict__.get("__getattr__")
    if getattr(existing, "_sharpmod_shim", False):
        return

    def __getattr__(self, name, _map=mapping, _prev=existing):
        try:
            return _map[name]
        except KeyError:
            pass
        if _prev is not None:
            return _prev(self, name)
        raise AttributeError(name)

    __getattr__._sharpmod_shim = True
    try:
        cls.__getattr__ = __getattr__
    except (AttributeError, TypeError):
        pass


def _flatten_module(module) -> None:
    """Flatten scoped enums for every Qt class exposed by ``module``."""
    for name in dir(module):
        obj = getattr(module, name, None)
        if isinstance(obj, type):
            _flatten_class_enums(obj)


class _MappedSignalAdapter:
    """Qt5-style ``QSignalMapper.mapped`` shim over the Qt6 split signals.

    Qt6 replaced the overloaded ``mapped`` signal with the type-specific
    ``mappedInt`` / ``mappedString`` / ``mappedObject`` signals. The vendored
    code selects an overload with ``mapper.mapped[str]`` (and occasionally
    connects ``mapper.mapped`` directly). This adapter maps the subscript form
    to the matching Qt6 signal and defaults a bare ``.connect`` to the int
    signal, matching the Qt5 default overload.
    """

    __slots__ = ("_mapper",)

    def __init__(self, mapper):
        self._mapper = mapper

    def __getitem__(self, key):
        m = self._mapper
        if key is str:
            return m.mappedString
        if key is int:
            return m.mappedInt
        # QWidget/QObject overloads collapse onto the object signal in Qt6.
        return getattr(m, "mappedObject", m.mappedInt)

    def connect(self, *args, **kwargs):
        return self._mapper.mappedInt.connect(*args, **kwargs)

    def emit(self, *args, **kwargs):
        return self._mapper.mappedInt.emit(*args, **kwargs)


def _patch_methods() -> None:
    """Patch non-enum Qt5 APIs the vendored render path uses that Qt6 removed.

    Only the APIs actually exercised while composing/painting the window
    headless are shimmed; each patch is guarded and skipped when the modern
    name is unavailable or the legacy name already works.
    """
    from PySide6 import QtGui, QtCore

    # QFontMetrics(F).width(text) -> horizontalAdvance(text) (removed in Qt6).
    for fm_cls in (QtGui.QFontMetrics, QtGui.QFontMetricsF):
        if not hasattr(fm_cls, "width") and hasattr(fm_cls, "horizontalAdvance"):
            try:
                fm_cls.width = fm_cls.horizontalAdvance
            except (AttributeError, TypeError):
                pass

    # QFont(family, size, bold=..., italic=...): PySide2 accepted Q_PROPERTY
    # keyword arguments in the constructor; PySide6 rejects unknown kwargs.
    # Translate the ones the vendored widgets pass into post-construction
    # setter calls.
    font_cls = QtGui.QFont
    if not getattr(font_cls.__init__, "_sharpmod_shim", False):
        _orig_font_init = font_cls.__init__

        def _font_init(self, *args, **kwargs):
            bold = kwargs.pop("bold", None)
            italic = kwargs.pop("italic", None)
            weight = kwargs.pop("weight", None)
            _orig_font_init(self, *args, **kwargs)
            if weight is not None:
                try:
                    self.setWeight(weight)
                except (TypeError, ValueError):
                    pass
            if bold is not None:
                self.setBold(bool(bold))
            if italic is not None:
                self.setItalic(bool(italic))

        _font_init._sharpmod_shim = True
        try:
            font_cls.__init__ = _font_init
        except (AttributeError, TypeError):
            pass

    # QSignalMapper.mapped[...] -> mappedInt/mappedString/mappedObject (Qt6).
    mapper_cls = QtCore.QSignalMapper
    if not hasattr(mapper_cls, "mapped"):
        prev = mapper_cls.__dict__.get("__getattr__")
        if not getattr(prev, "_sharpmod_shim", False):
            def __getattr__(self, name, _prev=prev):
                if name == "mapped":
                    return _MappedSignalAdapter(self)
                if _prev is not None:
                    return _prev(self, name)
                raise AttributeError(name)
            __getattr__._sharpmod_shim = True
            try:
                mapper_cls.__getattr__ = __getattr__
            except (AttributeError, TypeError):
                pass


def _patch_numpy_aliases() -> None:
    """Restore the deprecated NumPy scalar aliases removed in NumPy >= 1.24.

    The vendored ``sharppy`` compute/plot paths still reference ``np.float`` /
    ``np.int`` / ``np.bool`` etc., which were removed in NumPy 1.24. SHARPpy Reimagined's
    own code never uses them; this only lets the vendored stack run on the
    modernized NumPy pin. Each alias is added only when absent.
    """
    import warnings

    try:
        import numpy as np
    except Exception:
        return
    aliases = {
        "float": float, "int": int, "bool": bool, "object": object,
        "str": str, "complex": complex, "long": int, "unicode": str,
    }
    # Probing some removed names (e.g. ``np.object``) triggers NumPy's own
    # FutureWarning via its module ``__getattr__``; silence it while detecting.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name, target in aliases.items():
            if not hasattr(np, name):
                try:
                    setattr(np, name, target)
                except (AttributeError, TypeError):
                    pass


class _NumpyWhere0dProxy:
    """Forwards every attribute to NumPy but promotes 0-d ``where`` input.

    ``np.where(cond)`` raises under NumPy >= 1.25 when ``cond`` is 0-dimensional
    (``Calling nonzero on 0d arrays is not allowed``). This proxy delegates every
    attribute to the real ``numpy`` module and only overrides ``where`` to
    promote its condition with ``atleast_1d`` first -- a no-op for arrays that
    are already >= 1-D, so it preserves behaviour everywhere else.
    """

    def __init__(self, np_module):
        self._np = np_module

    def __getattr__(self, name):
        return getattr(self._np, name)

    def where(self, condition, *args, **kwargs):
        return self._np.where(self._np.atleast_1d(condition), *args, **kwargs)


def _patch_sharppy_pwv_climo() -> None:
    """Make the vendored ``pwv_climo`` tolerate NumPy's 0-d ``nonzero`` change.

    ``sharppy.databases.pwv.pwv_climo`` calls ``np.where(pwv_300 > sigma)`` where
    ``pwv_300`` is a scalar, so the condition is 0-dimensional -- which NumPy
    >= 1.25 rejects. Rather than wrapping the function (which would miss callers
    that imported it *by name* before this shim ran, e.g.
    ``sharppy.sharptab.profile``), this permanently replaces the ``np`` reference
    *inside the pwv module* with a proxy. The original function resolves ``np``
    from its module globals at call time, so the fix applies no matter where the
    function is referenced, while leaving the global ``numpy`` module untouched.
    """
    try:
        import numpy as np
        from sharppy.databases import pwv as pwv_mod
    except Exception:
        return
    if isinstance(getattr(pwv_mod, "np", None), _NumpyWhere0dProxy):
        return
    try:
        pwv_mod.np = _NumpyWhere0dProxy(np)
    except (AttributeError, TypeError):
        pass


def apply() -> bool:
    """Install the Qt5->Qt6 / modern-NumPy compatibility shims.

    Restores unscoped Qt enum access (class and instance), patches the handful
    of removed Qt5 methods the vendored render path relies on, and restores the
    NumPy scalar aliases removed in NumPy >= 1.24. Idempotent: repeated calls
    are no-ops after the first success. Returns ``True`` when the shim was
    applied (or already active), ``False`` when PySide6 is unavailable.
    """
    global _APPLIED
    if _APPLIED:
        return True
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except Exception:
        return False
    for module in (QtCore, QtGui, QtWidgets):
        _flatten_module(module)
    _patch_methods()
    _patch_numpy_aliases()
    _patch_sharppy_pwv_climo()
    _APPLIED = True
    return True
