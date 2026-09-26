"""Qt application of the chrome design tokens.

:mod:`sharpmod.ui.styles.theme` owns the token values and generates the style sheet as
text. This module is the only place that touches Qt: it builds the
``QPalette``, registers the bundled chrome fonts, and applies both to a live
``QApplication``.

Why a QPalette *and* a style sheet
----------------------------------
A style sheet only reaches widgets whose selectors match. Several surfaces are
drawn by the platform style from palette roles rather than from style-sheet
rules -- native message boxes, tooltips on some platforms, text-cursor and
selection colours inside complex controls, and the disabled-state text of
composite widgets. With no palette set, those fall back to the OS theme, which
is why the app previously mixed visual languages inside a single window. Setting
both keeps unstyled corners coherent.

Why the font handling looks defensive
-------------------------------------
``sharpmod.rendering.cli.install_font`` replaces ``QtGui.QFont`` process-wide with a
subclass that rewrites the family during construction, then calls
``app.setFont(...)``. It runs lazily, when the first sounding window opens, so
it lands *after* the picker is already on screen. Two consequences are handled
here:

* Chrome typography is carried by the generated style sheet (a style-sheet
  ``font-family`` declaration outranks the application font), so the picker does
  not silently restyle itself mid-session.
* :func:`ui_font` sets the family *after* construction, because the monkeypatch
  only intercepts the constructor. Any chrome code needing a real ``QFont``
  should use it rather than ``QFont("Space Grotesk", ...)``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging

from qtpy.QtGui import QColor, QFont, QFontDatabase, QPalette
from qtpy.QtWidgets import QApplication

from sharpmod.ui.styles import theme as _theme
from sharpmod.ui.styles.theme import (
    DEFAULT_DENSITY,
    DEFAULT_TEXT_SCALE,
    FAMILY_MONO_STACK,
    FAMILY_UI_STACK,
    FONT_PT,
    Theme,
    build_chrome_qss,
    normalize_density,
    normalize_text_scale,
    theme_for_color_style,
)

_LOGGER = logging.getLogger("sharpmod.gui")

__all__ = [
    "build_qpalette",
    "install_chrome_fonts",
    "chrome_font_available",
    "chrome_qss",
    "ui_font",
    "mono_font",
    "apply_theme",
    "ensure_theme_applied",
    "theme_is_applied",
    "current_theme",
    "current_text_scale",
    "current_font_text_scale",
    "current_density",
    "font_text_scale",
]

#: Registered chrome families, resolved once per process by
#: :func:`install_chrome_fonts`. ``None`` means "not attempted yet".
_registered_families: frozenset[str] | None = None

#: The theme most recently applied, so widgets built later can consult it
#: without threading a parameter through every constructor.
_current_theme: Theme = _theme.GRAPHITE_DARK

#: Whether a theme has been pushed onto a live ``QApplication``. Tracked
#: separately from :data:`_current_theme`, which always holds a usable value.
_theme_applied: bool = False

#: Active chrome sizing preferences. They are process-wide for the same reason
#: the palette and style sheet are process-wide. Neither value affects the
#: scientific canvas transform or its zoom controls.
_current_text_scale: int = DEFAULT_TEXT_SCALE
_current_density: str = DEFAULT_DENSITY

#: Memoized ``(theme, text scale, density) -> style sheet``. Generating
#: the sheet is string templating, but ``apply_theme`` builds it twice per
#: real change (once for the shortcut check, once to apply) and the scaling
#: tests change themes repeatedly; the cache makes repeat combinations free.
#: Keyed on the full immutable ``Theme``, not its name: ``with_overrides()``
#: can change a colour while keeping the name, and a name key would then
#: return stale CSS missing the new accent.
_chrome_qss_cache: dict[tuple[Theme, int, str], str] = {}

# A figure can have many more device pixels than the live window.  Painters
# that deliberately provide their own output-density transform can request the
# base type ramp without mutating the user's application preference.  A
# ContextVar keeps the override scoped to the synchronous detached paint (and
# safe from unrelated worker threads).
_font_text_scale_override: ContextVar[int | None] = ContextVar(
    "sharpmod_font_text_scale_override", default=None)


def current_theme() -> Theme:
    """Return the most recently applied chrome theme."""
    return _current_theme


def current_text_scale() -> int:
    """Return the active chrome text scale as a percentage."""
    return _current_text_scale


def current_font_text_scale() -> int:
    """Return the scale used by programmatic fonts in the current context."""

    return _font_text_scale_override.get() or _current_text_scale


def current_density() -> str:
    """Return the active chrome control-density choice."""
    return _current_density


def chrome_qss(theme: Theme, *, text_scale=DEFAULT_TEXT_SCALE,
               density=DEFAULT_DENSITY) -> str:
    """Return the memoized chrome style sheet for one theme/scale/density."""
    key = (theme, normalize_text_scale(text_scale),
           normalize_density(density))
    cached = _chrome_qss_cache.get(key)
    if cached is None:
        cached = build_chrome_qss(theme, text_scale=key[1], density=key[2])
        _chrome_qss_cache[key] = cached
    return cached


def _expected_stylesheet(app, theme, scale, density):
    """Return the sheet ``app`` should carry, or ``None`` on build failure."""
    if app is None:
        return None
    try:
        return chrome_qss(theme, text_scale=scale, density=density)
    except Exception:
        return None


def _stylesheet_is_current(app, theme, scale, density) -> bool:
    """Return whether ``app`` already carries the expected chrome sheet."""
    expected = _expected_stylesheet(app, theme, scale, density)
    return expected is not None and app.styleSheet() == expected


def _font_is_current(app) -> bool:
    """Return whether the app font matches the recorded text scale.

    ``render.install_font`` deliberately rewrites the application font
    mid-session (and the style sheet outranks it for chrome typography), so
    only the chrome baseline is checked here: the ``ui_font("body")`` the
    last ``apply_theme`` installed for the recorded scale. A bare size change
    (e.g. 42 pt forced from outside) is drift this function must report.
    """
    if app is None:
        return True
    try:
        return app.font() == ui_font("body")
    except Exception:
        return False


def _chrome_is_current(app, theme, scale, density) -> bool:
    """Return whether ``app`` carries every theme-owned application state."""
    if not _stylesheet_is_current(app, theme, scale, density):
        return False
    if not _font_is_current(app):
        return False
    try:
        if app.palette() != build_qpalette(theme):
            return False
    except Exception:
        return False
    return True


@contextmanager
def font_text_scale(percent: int):
    """Temporarily use ``percent`` for programmatic fonts in this context.

    This does not restyle widgets or change :func:`current_text_scale`; it is
    intended for detached export painters that scale a high-DPI paint device
    explicitly.  Normal on-screen painting therefore continues to honour the
    user's interface text setting.
    """

    resolved = normalize_text_scale(percent)
    token = _font_text_scale_override.set(resolved)
    try:
        yield
    finally:
        _font_text_scale_override.reset(token)


def theme_is_applied() -> bool:
    """Return whether a chrome theme has been pushed onto a live application."""
    return _theme_applied


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def install_chrome_fonts() -> frozenset[str]:
    """Register the bundled TTFs with Qt and return the families obtained.

    Idempotent and safe to call before or after
    :func:`sharpmod.rendering.cli.install_font`; both ultimately call
    ``addApplicationFont``, which de-duplicates by file.

    Unlike the renderer's version this does **not** monkeypatch ``QFont`` or
    force a family globally -- chrome typography is applied through the style
    sheet, and the scientific canvas must keep choosing its own fonts.

    Never raises: an unavailable bundled font degrades to the platform fallback
    declared in the family stacks rather than preventing startup.
    """
    global _registered_families
    if _registered_families is not None:
        return _registered_families

    families: set[str] = set()
    try:
        from sharpmod.resources import font_resolver
    except Exception:
        _LOGGER.warning("chrome_fonts.resolver_unavailable", exc_info=True)
        _registered_families = frozenset()
        return _registered_families

    try:
        names = font_resolver.font_names()
    except Exception:
        _LOGGER.warning("chrome_fonts.enumerate_failed", exc_info=True)
        _registered_families = frozenset()
        return _registered_families

    for name in names:
        # Skip variable-weight files ("[wght]"); the static instances cover
        # every weight the ramp uses and register cleaner family names.
        if "[" in name:
            continue
        try:
            path = str(font_resolver.font_path(name))
        except Exception:
            continue
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id == -1:
            continue
        try:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
        except Exception:
            # Older bindings expose this as an instance method; the families
            # are only used for diagnostics, so a miss is not fatal.
            pass

    _registered_families = frozenset(families)
    _LOGGER.info("chrome_fonts.registered families=%s",
                 sorted(_registered_families))
    return _registered_families


def chrome_font_available(family: str) -> bool:
    """Return whether ``family`` resolved during font registration."""
    return family in (_registered_families or frozenset())


def _font_from_stack(stack: tuple[str, ...], point_size: float,
                     weight: int | None) -> QFont:
    """Build a ``QFont`` for a family stack, defeating the render monkeypatch.

    ``QFont`` may have been replaced by ``render.install_font`` with a subclass
    that rewrites the family inside ``__init__``. Constructing empty and then
    calling ``setFamily``/``setFamilies`` sidesteps that, because only the
    constructor is intercepted.
    """
    font = QFont()
    font.setFamily(stack[0])
    try:
        # Qt6 honours an ordered fallback list, so the platform fallbacks in
        # the token stacks apply to programmatic fonts too, not just to QSS.
        font.setFamilies(list(stack))
    except (AttributeError, TypeError):
        pass
    font.setPointSizeF(float(point_size))
    if weight is not None:
        font.setWeight(_qt_weight(weight))
    return font


def _qt_weight(css_weight: int):
    """Map a CSS numeric weight onto a Qt weight value.

    Qt6 ``QFont.Weight`` uses the CSS 100-900 scale directly, so the numeric
    value passes through. Older bindings need the legacy enum, hence the
    fallback.
    """
    try:
        return QFont.Weight(css_weight)
    except (ValueError, TypeError):
        if css_weight >= 700:
            return QFont.Bold
        if css_weight >= 600:
            return QFont.DemiBold
        if css_weight >= 500:
            return QFont.Medium
        return QFont.Normal


def ui_font(size_key: str = "body", weight: int | None = None) -> QFont:
    """Return a chrome UI ``QFont`` for a :data:`sharpmod.ui.styles.theme.FONT_PT` key."""
    scale = current_font_text_scale()
    point_size = FONT_PT[size_key] * scale / 100.0
    return _font_from_stack(FAMILY_UI_STACK, point_size, weight)


def mono_font(size_key: str = "body", weight: int | None = None) -> QFont:
    """Return a tabular/numeric ``QFont`` for a token size key.

    Use for any column of figures -- coordinates, pressures, forecast hours --
    so digits share an advance width and values align vertically.
    """
    scale = current_font_text_scale()
    point_size = FONT_PT[size_key] * scale / 100.0
    return _font_from_stack(FAMILY_MONO_STACK, point_size, weight)


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


def build_qpalette(theme: Theme) -> QPalette:
    """Translate a :class:`~sharpmod.ui.styles.theme.Theme` into a ``QPalette``.

    Covers the Active, Inactive, and Disabled groups. Qt does not derive
    disabled colours from the active ones in a way that survives a dark theme,
    so the Disabled group is populated explicitly; otherwise unavailable
    controls keep near-full-contrast text and stop reading as unavailable.
    """
    p = QPalette()

    surface = QColor(theme.surface)
    raised = QColor(theme.surface_raised)
    sunken = QColor(theme.surface_sunken)
    overlay = QColor(theme.surface_overlay)
    text = QColor(theme.text_primary)
    subtle = QColor(theme.text_secondary)
    disabled = QColor(theme.text_disabled)
    border = QColor(theme.border)
    accent = QColor(theme.accent)
    accent_text = QColor(theme.accent_text)

    # Window / panel surfaces.
    p.setColor(QPalette.Window, surface)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, sunken)
    p.setColor(QPalette.AlternateBase, surface)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, raised)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#FFFFFF"))
    p.setColor(QPalette.PlaceholderText, disabled)

    # Tooltips are drawn by the platform style from these roles, which is why
    # they previously stayed light inside the dark picker.
    p.setColor(QPalette.ToolTipBase, overlay)
    p.setColor(QPalette.ToolTipText, text)

    # Selection.
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, accent_text)

    # Links.
    p.setColor(QPalette.Link, accent)
    p.setColor(QPalette.LinkVisited, QColor(theme.accent_pressed))

    # 3D bevel roles. Fusion still consults these for frames and separators;
    # collapsing them onto the border colour keeps hairlines instead of bevels.
    p.setColor(QPalette.Light, border)
    p.setColor(QPalette.Midlight, border)
    p.setColor(QPalette.Mid, border)
    p.setColor(QPalette.Dark, QColor(theme.border_strong))
    p.setColor(QPalette.Shadow, QColor(theme.surface_sunken))

    # Unfocused windows keep the same surfaces; only selection is muted, so a
    # background window does not appear disabled.
    for role, colour in (
        (QPalette.Window, surface),
        (QPalette.WindowText, text),
        (QPalette.Base, sunken),
        (QPalette.Text, text),
        (QPalette.Button, raised),
        (QPalette.ButtonText, text),
        (QPalette.Highlight, QColor(theme.accent_subtle)),
        (QPalette.HighlightedText, text),
    ):
        p.setColor(QPalette.Inactive, role, colour)

    # Disabled group, set explicitly (see the docstring).
    for role, colour in (
        (QPalette.Window, surface),
        (QPalette.WindowText, disabled),
        (QPalette.Base, surface),
        (QPalette.AlternateBase, surface),
        (QPalette.Text, disabled),
        (QPalette.Button, surface),
        (QPalette.ButtonText, disabled),
        (QPalette.PlaceholderText, disabled),
        (QPalette.Highlight, QColor(theme.border)),
        (QPalette.HighlightedText, subtle),
        (QPalette.ToolTipBase, overlay),
        (QPalette.ToolTipText, subtle),
    ):
        p.setColor(QPalette.Disabled, role, colour)

    return p


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def apply_theme(
    app: QApplication | None = None,
    *,
    theme: Theme | None = None,
    color_style: str | None = None,
    text_scale=DEFAULT_TEXT_SCALE,
    density=DEFAULT_DENSITY,
) -> Theme:
    """Apply a chrome theme to ``app``: style, palette, font, style sheet.

    Args:
        app: Target application. Defaults to the running instance.
        theme: Explicit theme. Takes precedence over ``color_style``.
        color_style: A ``preferences/color_style`` value (``standard`` /
            ``inverted`` / ``protanopia``) to derive the paired chrome theme
            from. Reusing that setting keeps chrome and canvas in step without
            a second stored preference.
        text_scale: Chrome text-size percentage. This never changes scientific
            canvas zoom.
        density: ``compact`` or ``comfortable`` chrome spacing/control height.

    Returns:
        The theme that was applied.

    The style sheet is set on the *application*, not per window, because the
    picker materializes four of its five source panels lazily and dialogs are
    built on demand; a per-window sheet would miss everything created later.
    """
    global _current_theme, _current_text_scale, _current_density, _theme_applied

    app = app or QApplication.instance()
    resolved = theme or theme_for_color_style(color_style)
    resolved_scale = normalize_text_scale(text_scale)
    resolved_density = normalize_density(density)
    # Skip the application-wide re-polish when nothing changed. Re-setting an
    # identical style sheet forces Qt to re-polish every live widget, so a
    # repeated apply_theme call in a long serial run gets progressively slower
    # as more test windows accumulate. The comparison covers every theme-owned
    # application state -- sheet text, application font, palette -- not mere
    # non-emptiness: another component may have replaced any one of them, and
    # reapplying must restore it in that case. Themes compare by full value,
    # not name: with_overrides() can recolour a theme while keeping its name,
    # and a name comparison would keep the stale sheet.
    if (
        _theme_applied
        and _current_theme is not None
        and _current_theme == resolved
        and _current_text_scale == resolved_scale
        and _current_density == resolved_density
        and _chrome_is_current(app, resolved, resolved_scale, resolved_density)
    ):
        return _current_theme
    _current_text_scale = resolved_scale
    _current_density = resolved_density
    # Widgets receive synchronous palette/font/style events during application.
    # They must resolve semantic colours against the new theme, not the old one.
    _current_theme = resolved

    if app is None:
        # Tokens are still useful without a live application (tests, headless
        # token checks), so record the choice and return rather than raising.
        _current_theme = resolved
        return resolved

    # Fusion gives identical control metrics on every platform. Without it the
    # native Windows style draws anything the style sheet does not explicitly
    # cover, which is what produced mixed visual languages in one window.
    try:
        current_style = app.style()
        style_name = (
            current_style.objectName().casefold()
            if current_style is not None else ""
        )
        if style_name != "fusion":
            app.setStyle("Fusion")
    except Exception:
        _LOGGER.warning("chrome_theme.style_unavailable", exc_info=True)

    install_chrome_fonts()

    try:
        palette = build_qpalette(resolved)
        if app.palette() != palette:
            app.setPalette(palette)
    except Exception:
        _LOGGER.warning("chrome_theme.palette_failed", exc_info=True)

    # Baseline for anything the style sheet does not reach. The style sheet
    # still carries the authoritative chrome family, since render.install_font
    # may overwrite this later in the session.
    try:
        font = ui_font("body")
        if app.font() != font:
            app.setFont(font)
    except Exception:
        _LOGGER.warning("chrome_theme.font_failed", exc_info=True)

    try:
        stylesheet = chrome_qss(
            resolved,
            text_scale=resolved_scale,
            density=resolved_density,
        )
        if app.styleSheet() != stylesheet:
            app.setStyleSheet(stylesheet)
    except Exception:
        _LOGGER.exception("chrome_theme.stylesheet_failed")

    _current_theme = resolved
    _theme_applied = True
    _LOGGER.info(
        "chrome_theme.applied theme=%s text_scale=%s density=%s",
        resolved.name,
        resolved_scale,
        resolved_density,
    )
    return resolved


def ensure_theme_applied(
    app: QApplication | None = None,
    *,
    color_style: str | None = None,
    text_scale=DEFAULT_TEXT_SCALE,
    density=DEFAULT_DENSITY,
) -> Theme:
    """Apply the chrome theme once, if it has not been applied already.

    ``main`` applies the theme before the first widget is constructed, which
    avoids a visible restyle on startup. But ``PickerWindow`` is also built
    directly -- by the test suite, and by any caller embedding the picker -- and
    those paths would otherwise get unstyled chrome, because the style sheet now
    lives on the application rather than on the window.

    This restores a replaced style sheet without a full re-apply, but it never
    changes the recorded scale or density: those are startup preferences owned
    by ``apply_theme``, and a bare call from a window constructor must not
    silently downgrade a 150% session to 100%.
    """
    resolved_app = app or QApplication.instance()
    if _theme_applied and _current_theme is not None and resolved_app is not None:
        if color_style is not None:
            # An explicit theme argument names a different sheet than the
            # recorded one, so fall through and apply it.
            return apply_theme(
                app,
                color_style=color_style,
                text_scale=text_scale,
                density=density,
            )
        expected = _expected_stylesheet(
            resolved_app, _current_theme, _current_text_scale,
            _current_density)
        if expected is not None:
            if resolved_app.styleSheet() == expected:
                return _current_theme
            resolved_app.setStyleSheet(expected)
            return _current_theme
    return apply_theme(
        app,
        color_style=color_style,
        text_scale=text_scale,
        density=density,
    )
