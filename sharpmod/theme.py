"""Design tokens and generated Qt stylesheets for the desktop GUI chrome.

This module is deliberately **Qt-free**, mirroring :mod:`sharpmod.colors`. It
owns every colour, spacing, radius, type, and motion value used by the
application *chrome* -- windows, panels, cards, controls, menus, dialogs -- and
generates the Qt style sheet from those tokens. Qt-dependent application (the
``QPalette``, font registration, and ``QApplication`` wiring) lives in
:mod:`sharpmod.gui_theme`.

Scope boundary
--------------
"Chrome" means the application frame. It does **not** include the scientific
canvas: the Skew-T, hodograph, insets, index boards, and derived-parameter
panels paint themselves with :mod:`sharpmod.colors` values pushed through the
``setPreferences`` contract. Those plotted colours are scientifically
meaningful and are not tokens here. Restyling chrome cannot alter them,
because the canvas widgets never read the style sheet for plotted content.

Why the type tokens matter
--------------------------
``sharpmod.render.install_font`` replaces ``QtGui.QFont`` process-wide with a
subclass that rewrites the family on construction, and calls
``app.setFont(...)``. It runs when the first sounding window opens, i.e. after
the picker is already visible. Chrome typography therefore cannot rely on
``QApplication.setFont`` alone -- it would be silently reassigned mid-session.
The generated style sheet declares ``font-family`` explicitly, because a style
sheet declaration wins over the application font. Code that needs a chrome
``QFont`` object should go through :func:`sharpmod.gui_theme.ui_font`, which
sets the family *after* construction to defeat that monkeypatch.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = [
    "SPACE",
    "RADIUS",
    "CONTROL_H",
    "FONT_PT",
    "WEIGHT",
    "MOTION_MS",
    "RAIL_W",
    "FIELD_W",
    "PROGRESS_H",
    "NAV_RAIL_W",
    "VIEWER_SIDEBAR_W",
    "ZOOM_SLIDER_W",
    "SCROLLBAR_W",
    "FAMILY_UI_STACK",
    "FAMILY_MONO_STACK",
    "Theme",
    "GRAPHITE_DARK",
    "PAPER_LIGHT",
    "PROTANOPIA_DARK",
    "THEMES",
    "DEFAULT_THEME_NAME",
    "COLOR_STYLE_THEMES",
    "theme_for_color_style",
    "MapPalette",
    "DARK_MAP",
    "LIGHT_MAP",
    "PROTANOPIA_MAP",
    "THEME_MAP_PALETTES",
    "map_palette",
    "font_stack_css",
    "build_chrome_qss",
    "AVAIL_STATUS_ROLES",
    "PROP_AVAIL_STATUS",
    "PROP_COMPACT",
]


# ---------------------------------------------------------------------------
# Dimensional scales
# ---------------------------------------------------------------------------

#: Spacing scale on a 4 px base. Every margin, padding, and gap in the chrome
#: resolves to one of these; no literal pixel gaps at call sites.
SPACE: dict[str, int] = {
    "xxs": 2,
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 20,
    "xxl": 24,
    "xxxl": 32,
}

#: Corner radii. Controls use ``sm``/``md``; cards and popups use ``lg``/``xl``.
RADIUS: dict[str, int] = {
    "sm": 4,
    "md": 6,
    "lg": 8,
    "xl": 10,
    "pill": 999,
}

#: Interactive control heights. ``md`` is the default; ``lg`` is a primary
#: action; ``xl`` is a hero action. Nothing may fall below ``sm`` (28 px), which
#: is the minimum comfortable pointer target for dense scientific controls.
CONTROL_H: dict[str, int] = {
    "xs": 24,
    "sm": 28,
    "md": 32,
    "lg": 36,
    "xl": 40,
}

#: Type ramp in **points**. Points (not pixels) so the ramp honours the OS text
#: scaling setting; the previous code mixed ``8pt`` and ``11px`` declarations.
FONT_PT: dict[str, float] = {
    "caption": 8.0,
    "small": 9.0,
    "body": 10.0,
    "subhead": 11.0,
    "title": 13.0,
    "heading": 16.0,
    "display": 20.0,
}

#: CSS font weights available in the bundled families.
WEIGHT: dict[str, int] = {
    "light": 300,
    "regular": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
}

#: Transition durations in milliseconds.
MOTION_MS: dict[str, int] = {
    "fast": 120,
    "base": 180,
    "slow": 240,
}

#: Vertical scrollbar width. Must track the ``QScrollBar`` rule in the generated
#: style sheet, because a scroll area has to reserve this much or its content is
#: clipped once the bar appears.
SCROLLBAR_W: int = SPACE["md"]

#: Usable content width for the picker's left control rail -- i.e. excluding the
#: scrollbar, which :func:`_scrolling_control_rail` adds on top.
#:
#: 400 px is set by the widest card any panel contains: the forecast "Point"
#: group needs 372 px for its longitude label, spin box, and Center button. The
#: rails previously ran at 324, 380, and 412 px in different panels, which both
#: clipped the model panel and moved the divider when switching source.
RAIL_W: dict[str, int] = {
    "min": 320,
    "max": 400,
}

#: Named widths for content-sized fields.
#:
#: These are not on the spacing scale, because they are sized by the content
#: they must hold -- a UTC cycle like ``00Z``, a date like ``2026-08-27`` -- not
#: by a rhythm. Naming them still beats repeating the literals at each call
#: site, which previously disagreed between otherwise-identical panels.
FIELD_W: dict[str, int] = {
    "compact": 72,   # a cycle or forecast-hour combo
    "action": 96,    # an inline button such as "Most recent"
    "date": 118,     # an ISO date edit
    "wide": 132,     # a date/cycle/forecast control in the model panel
    #: Label column in a rail form card. Sized for the longest label any panel
    #: uses ("Longitude:") so that every field in every card starts at the same
    #: x -- per-card label columns made the rows step in and out down the rail.
    "label": 76,
}

#: Progress-bar track height. Deliberately slim: the bar reports transfer
#: progress and should not compete with the controls above it.
PROGRESS_H: int = 10

#: Width of the source navigation rail. Wide enough for the longest source name
#: ("Reanalysis (ERA5)") at the body size without truncation.
NAV_RAIL_W: int = 188

#: Width of the viewer's context sidebar.
#:
#: Two constraints set this, and the second is the tighter one.
#:
#: *Fit mode* is generous: the composed sounding is about 1630x1091, so on a
#: maximized 1920x1080 window the fit is limited by height and leaves roughly
#: 460 px of horizontal slack that would otherwise be empty letterbox. Anything
#: up to ~450 px is free there.
#:
#: *Actual size* is not. At 100% the canvas needs its full ~1630 px of width, so
#: the viewport must be at least that wide or the pressure-axis labels are
#: clipped off the left edge. This matters more than it sounds: the vendored
#: canvas paints into a bitmap at its natural size, so 100% is the only view
#: that is not resampled -- every other scale is a bitmap downscale and looks
#: soft. Clipping the one crisp view is the worst possible trade.
#:
#: The budget has to include the vertical scrollbar, which *does* appear at 100%
#: (the canvas is ~150 px taller than the viewport) and takes another
#: ``SCROLLBAR_W``. On a 1920 px screen that puts the ceiling near 255 px, not
#: the 272 px a scrollbar-free measurement suggests. The first version of this
#: panel was 320 px and cut 48 px off the sounding.
#:
#: Measured, not assumed: see ``test_gui_viewer_sidebar.py``, which pins both
#: the fit-scale and actual-size consequences.
VIEWER_SIDEBAR_W: int = 248

#: Width of the viewer's zoom slider. Long enough that one pixel of travel is a
#: fraction of a percent, so fine adjustment is possible by dragging.
ZOOM_SLIDER_W: int = 140

#: UI text family stack. "Space Grotesk" is bundled under
#: ``sharpmod/resources/fonts``; the rest are per-platform fallbacks so a
#: source checkout without registered fonts still resolves something sane.
#: The previous chrome declared only ``"Segoe UI", "Arial"``, which has no
#: macOS or Linux fallback.
FAMILY_UI_STACK: tuple[str, ...] = (
    "Space Grotesk",
    "Segoe UI Variable Text",
    "Segoe UI",
    "SF Pro Text",
    "Inter",
    "Ubuntu",
    "Cantarell",
    "DejaVu Sans",
    "sans-serif",
)

#: Numeric / tabular family stack. "JetBrains Mono" is bundled. Used for
#: coordinates, pressures, times, and any column of figures, so digits share an
#: advance width and values line up vertically.
FAMILY_MONO_STACK: tuple[str, ...] = (
    "JetBrains Mono",
    "Cascadia Mono",
    "Consolas",
    "SF Mono",
    "DejaVu Sans Mono",
    "monospace",
)


def font_stack_css(stack: tuple[str, ...]) -> str:
    """Render a family stack as a Qt style-sheet ``font-family`` value.

    Multi-word families are quoted; the generic keyword terminator
    (``sans-serif`` / ``monospace``) is left bare, as CSS requires.
    """
    parts = []
    for family in stack:
        if family in {"sans-serif", "serif", "monospace"}:
            parts.append(family)
        else:
            parts.append(f'"{family}"')
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Colour roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """A complete set of chrome colour roles.

    Roles are semantic, never literal. A widget asks for ``surface_raised``,
    not ``#161B24``, so a new theme is a new instance rather than a search and
    replace. Values are ``#rrggbb`` strings, matching the convention in
    :mod:`sharpmod.colors`.

    Contrast contract
    -----------------
    Every value here was solved against WCAG AA using
    :func:`sharpmod.colors.contrast_ratio` and is enforced by
    ``tests/test_gui_theme_tokens.py``:

    * ``text_primary`` / ``text_secondary`` / ``text_tertiary`` clear **4.5:1**
      on every surface they may be drawn on.
    * ``accent_text`` clears **4.5:1** on ``accent``, ``accent_hover``, and
      ``accent_pressed`` -- so a primary button label stays legible while
      hovered, not only at rest.
    * ``border_strong`` clears **3.0:1** on every surface, because it is the
      boundary of an interactive control. Adjacent surfaces differ by only
      ~1.05:1, so the outline -- not the fill -- is what identifies a control,
      which puts it squarely under WCAG 1.4.11 (non-text contrast).
    * ``border`` is exempt. It is decoration only: panel dividers, card edges,
      and table rules. It must never be the sole means of identifying a
      control; use ``border_strong`` for that.
    * ``text_disabled`` is deliberately below 4.5:1. Reduced contrast is the
      signal that a control is unavailable, and WCAG exempts disabled
      controls.
    """

    #: Stable identifier used in settings and tests.
    name: str
    #: ``True`` when the theme is dark. Drives icon variants and the
    #: contrast direction for generated hover/pressed states.
    is_dark: bool

    # Surfaces, back to front.
    surface: str            # window background
    surface_raised: str     # cards, group panels, rails
    surface_sunken: str     # text inputs, wells, list backgrounds
    surface_overlay: str    # menus, popups, toasts, tooltips

    # Lines.
    # Lines. The split is an accessibility requirement, not a stylistic one --
    # see the class docstring note below.
    border: str             # decorative hairline: dividers, card edges
    border_strong: str      # boundary of an interactive control

    # Text, in descending prominence.
    text_primary: str       # values, titles, active labels
    text_secondary: str     # supporting copy, field labels
    text_tertiary: str      # hints, attribution, metadata
    text_disabled: str      # unavailable controls

    # The single accent. Exactly one per theme, reserved for the primary
    # action and the current selection.
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_subtle: str      # tinted background for selected rows / chips
    accent_text: str        # text drawn on top of ``accent``

    # Status. Used for readiness chips, validation, and progress states.
    success: str
    warning: str
    danger: str
    info: str

    #: Keyboard focus ring. Must be visible against every surface above.
    focus_ring: str

    #: Scrollbar handle, which needs to read as chrome rather than content.
    scrollbar: str
    scrollbar_hover: str

    def with_overrides(self, **roles: str) -> "Theme":
        """Return a copy with individual roles replaced."""
        return replace(self, **roles)


#: Default dark chrome. Pairs with the Standard (black canvas) palette so the
#: scientific canvas reads as content inside a near-black frame, instead of the
#: previous black canvas floating in a light-grey window.
GRAPHITE_DARK = Theme(
    name="graphite-dark",
    is_dark=True,
    # Genuinely neutral graphite, with a warm cast of only a few points in R
    # over B. The first version of this palette was blue-tinted throughout
    # (#0F1319 / #161B24 / #1C222D, all around 0.4 relative chroma on a ~215
    # degree hue) with a saturated blue accent -- which is the default dark
    # ramp every UI framework ships, and read as generic.
    #
    # Neutral is also the right answer for this application specifically: the
    # scientific canvas is pure black carrying highly saturated data colours
    # (red and green traces, cyan/magenta/yellow hodograph, blue isotherms).
    # Chrome that carries its own chroma competes with that and reads as part
    # of the plot; chrome with almost none makes the canvas's colour the only
    # saturated thing on screen, which is what it should be.
    surface="#131312",
    surface_raised="#1F1E1B",
    surface_sunken="#0D0D0C",
    surface_overlay="#282622",
    border="#2F2D29",
    # Solved to 3.58:1 against the darkest surface. The predecessor #313B4B
    # read as a pleasant hairline but only reached 1.65:1, leaving control
    # outlines effectively invisible.
    border_strong="#6B6862",
    text_primary="#F1EFEB",
    text_secondary="#B5B0A7",
    # 4.86:1 on surface_raised, its worst case. An earlier #6F7C8D reached
    # only 4.06:1 -- and the code this replaces used plain "gray" (#808080).
    text_tertiary="#8D887F",
    text_disabled="#5A5650",
    # Muted steel blue, not the stock bright primary. Cool against the warm
    # neutral surfaces, so the one chromatic element in the chrome is the one
    # that means "this is the action" or "this is selected".
    #
    # Squeezed between two opposing constraints, which is why it is not simply
    # "a nice blue": the fill needs 3:1 against ``surface_raised`` to read as a
    # control at all, while a white label needs 4.5:1 against the fill. That
    # leaves a usable luminance band of roughly 0.134 to 0.183, and all three
    # states have to land inside it -- so hover cannot be much brighter than
    # rest. A first pass at #33679E looked right and failed the lower bound at
    # 2.84:1.
    accent="#376DA5",
    accent_hover="#3D75AF",
    accent_pressed="#2F5F92",
    accent_subtle="#1A2028",
    accent_text="#FFFFFF",
    # Status hues are deliberately unchanged: they are semantic in a
    # severe-weather tool, and users read them faster than they read labels.
    success="#3FB950",
    warning="#D29922",
    danger="#F85149",
    info="#58A6FF",
    focus_ring="#6FA8DC",
    scrollbar="#3A3733",
    scrollbar_hover="#4A463F",
)

#: Light chrome. Pairs with the Inverted (light canvas) palette.
#:
#: Warm off-white rather than the blue-tinted greys this used to carry
#: (#EEF2F7 / #EDF1F6 / #D9E0E8), so it pairs with the dark theme's neutral
#: graphite: switching theme changes lightness, not hue family. It also lets
#: "paper" read as paper instead of as cold screen grey.
#:
#: ``surface`` is deliberately deeper than the obvious near-white: at #F6F8FB it
#: sat only 1.064:1 from the white cards, so panels read as one undifferentiated
#: block. #F1EFEA raises that to 1.117:1, which is about as dark as this theme
#: can go before ``border_strong`` falls under the 3:1 non-text minimum.
#:
#: A consequence worth knowing: ``surface_sunken`` is within 1.03:1 of
#: ``surface``, so an input placed directly on the window background does not
#: read as recessed by fill alone. That is acceptable because ``border_strong``
#: identifies controls (see :class:`Theme`), and in practice inputs sit inside
#: white cards, against which the sunken fill still reads.
PAPER_LIGHT = Theme(
    name="paper-light",
    is_dark=False,
    surface="#F1EFEA",
    # Cards stay pure white: against the warm surface they read as sheets laid
    # on a desk, which is the layering the light theme depends on.
    surface_raised="#FFFFFF",
    surface_sunken="#EBE8E2",
    surface_overlay="#FFFFFF",
    border="#DDD8D0",
    # 3.26:1 against surface_sunken, its worst case. #8A857C sat at 2.88:1 --
    # the warm surfaces are slightly lighter than the blue-grey ones they
    # replace, so the control outline had to darken to keep up.
    border_strong="#847F76",
    text_primary="#1A1815",
    text_secondary="#55504A",
    text_tertiary="#6B6660",
    text_disabled="#A8A29A",
    # On a light surface the accent *deepens* on hover, so contrast increases
    # with interaction instead of decreasing.
    accent="#2C6BA8",
    accent_hover="#24598E",
    accent_pressed="#1D4A78",
    accent_subtle="#E3EBF3",
    accent_text="#FFFFFF",
    # Status hues unchanged: semantic in a severe-weather tool.
    success="#1A7F37",
    warning="#9A6700",
    danger="#CF222E",
    info="#0969DA",
    focus_ring="#2C6BA8",
    scrollbar="#CBC5BC",
    scrollbar_hover="#B2ABA1",
)

#: Protanopia-safe dark chrome.
#:
#: Red and green are the confusable pair, so status moves off the red/green
#: axis onto the blue/yellow axis that protanopes retain. Hue alone is not
#: enough though: four statuses cannot be encoded by hue on a two-ended axis.
#: So the set is arranged as two pairs, each separated by *luminance*:
#:
#:   blue axis    success  L=0.244  ->  info    L=0.439   (delta 0.195)
#:   yellow axis  warning  L=0.278  ->  danger  L=0.493   (delta 0.215)
#:
#: Cross-axis neighbours that happen to sit at similar luminance
#: (success/warning, info/danger) stay distinguishable by hue instead. The
#: safety-critical success-vs-danger pair is separated on both dimensions
#: (delta 0.250). ``danger`` is the brightest value in the set, so the most
#: urgent state is also the most prominent.
#:
#: Colour is still never the only signal: status chips carry a text label too.
PROTANOPIA_DARK = GRAPHITE_DARK.with_overrides(
    name="protanopia-dark",
    success="#1E93B2",
    warning="#C08420",
    danger="#F5AC45",
    info="#6CB6FF",
)


@dataclass(frozen=True)
class MapPalette:
    """Colours for the picker's station and point-selection maps.

    Kept separate from :class:`Theme` because a map is a geographic drawing
    surface, not a widget: it has its own vocabulary (landmass, graticule,
    coastline, markers) that does not map onto chrome roles, and it is painted
    with ``QPainter`` rather than styled with a style sheet.

    It is still *chrome*, not scientific canvas -- it selects a location, it does
    not plot data -- so it must follow the theme. Before this existed the maps
    were unconditionally dark, which was invisible while the picker was always
    dark and became an obvious dark rectangle once a light theme existed.

    Marker semantics are deliberately preserved across themes: amber means
    selected, red means an available station, cyan means a saved location. Only
    the exact shades shift, so a user's learned reading of the map survives a
    theme change.
    """

    background: str         # landmass / sea fill
    graticule: str          # lat/lon grid lines
    graticule_label: str    # degree labels
    states: str             # internal administrative borders
    countries: str          # national borders
    coastline: str          # most prominent outline
    readout_text: str       # coordinate readout / hover label
    readout_shadow: str     # outline behind readout text, for legibility
    station: str            # an available radiosonde station
    station_edge: str
    station_hover: str      # station under the pointer
    station_hover_edge: str  # deliberately distinct from station_edge
    selected: str           # the chosen station or point
    selected_edge: str
    selected_crosshair: str  # crosshair drawn *over* the selected marker, so it
                             # must contrast with `selected`, not the background
    saved: str              # a user-saved named location
    saved_edge: str
    domain_edge: str        # WRF domain perimeter


#: Map colours for the dark themes.
#:
#: These are the long-standing literal values, so the dark map renders as it
#: always has. One deliberate simplification: the saved-location label formerly
#: used ``#EAFAFF`` while the coordinate readout used ``#EEF2F8``. Both are now
#: ``readout_text``; the difference between them was not perceptible.
DARK_MAP = MapPalette(
    # Geography is neutral; data is chromatic. Every one of these roles used to
    # be a shade of navy (#05070D landmass, #2C3E55 states, #54697F countries,
    # #A9C0DC coastline), which meant the basemap, the model-domain outline and
    # the markers were all competing in the same blue -- so nothing separated
    # figure from ground and the map read as a generic dark rectangle.
    #
    # Now the terrain carries almost no chroma and the things that *mean*
    # something keep theirs: red available station, amber selection, cyan saved
    # location, blue model domain. Those pop off neutral terrain in a way they
    # never did off navy.
    background="#0A0A09",
    graticule="#1C1B18",
    # Brighter than the #3A4A63 it replaces, which left the degree labels
    # barely legible against the landmass.
    graticule_label="#6A665E",
    # Luminance-matched to the navy lines they replace rather than merely
    # de-tinted: a first pass at #3B3833 / #6E6A61 was *darker* than the old
    # #2C3E55 / #54697F and made state borders hard to pick out at all.
    states="#4A463F",
    countries="#7C7669",
    coastline="#AEA89C",
    readout_text="#F1EFEB",
    readout_shadow="#000000",
    station="#E03030",
    station_edge="#7A1414",
    station_hover="#FF8A8A",
    station_hover_edge="#FFFFFF",
    selected="#FFD000",
    selected_edge="#FFFFFF",
    selected_crosshair="#0A0A09",
    saved="#44D7FF",
    saved_edge="#0B1216",
    # Deliberately still cool: the model domain is a data overlay, not
    # geography, and against neutral terrain that now reads unambiguously.
    domain_edge="#79B8FF",
)

#: Map colours for the light theme. The line hierarchy is inverted -- lines get
#: *darker* as they get more important, rather than lighter -- and the readout
#: shadow becomes white so text stays legible over pale terrain.
LIGHT_MAP = MapPalette(
    # Neutral terrain for the same reason as DARK_MAP, warm to match
    # PAPER_LIGHT: geography carries no chroma so the markers can.
    #
    # A shade deeper than the panel surface it sits beside (#F1EFEA), so the
    # drawing area reads as a distinct region. At #EDEAE4 the two were close
    # enough that the map bled into the chrome around it.
    background="#E7E3DB",
    graticule="#D8D3CA",
    graticule_label="#6B6660",
    states="#B3ADA3",
    countries="#837D73",
    coastline="#46423B",
    readout_text="#1A1815",
    readout_shadow="#FFFFFF",
    station="#C42B2B",
    station_edge="#FFFFFF",
    # Lighter than `station` so hover still reads as "lit up", but dark enough
    # to clear 3:1 on the pale basemap -- 3.16:1 here. This marker has very
    # little headroom: the obvious #E06A6A reached only 2.77:1, and #D95C5C fell
    # to 2.91:1 once the basemap was deepened to separate it from the panel.
    station_hover="#D35555",
    station_hover_edge="#FFFFFF",
    selected="#B37400",
    selected_edge="#FFFFFF",
    selected_crosshair="#2A1C00",
    saved="#0C7C99",
    saved_edge="#FFFFFF",
    domain_edge="#2C6BA8",
)

#: Protanopia map colours. The station/selected pair is the one that matters:
#: red and amber are the confusable combination, so selected moves to a
#: high-luminance blue that cannot be mistaken for a station dot.
PROTANOPIA_MAP = MapPalette(
    background=DARK_MAP.background,
    graticule=DARK_MAP.graticule,
    graticule_label=DARK_MAP.graticule_label,
    states=DARK_MAP.states,
    countries=DARK_MAP.countries,
    coastline=DARK_MAP.coastline,
    readout_text=DARK_MAP.readout_text,
    readout_shadow=DARK_MAP.readout_shadow,
    station="#C08420",
    station_edge="#3A2704",
    station_hover="#E3B341",
    station_hover_edge="#FFFFFF",
    selected="#7FD3F5",
    selected_edge="#FFFFFF",
    selected_crosshair="#04222E",
    saved="#1E93B2",
    saved_edge="#07131B",
    domain_edge="#9FC9FF",
)


#: Every selectable chrome theme, keyed by :attr:`Theme.name`.
THEMES: dict[str, Theme] = {
    GRAPHITE_DARK.name: GRAPHITE_DARK,
    PAPER_LIGHT.name: PAPER_LIGHT,
    PROTANOPIA_DARK.name: PROTANOPIA_DARK,
}

DEFAULT_THEME_NAME = GRAPHITE_DARK.name

#: Maps the *existing* ``preferences/color_style`` setting onto chrome themes.
#: Reusing that setting means chrome follows the canvas palette through the
#: persistence and live-update path that already works, rather than adding a
#: second, independently-stored theme preference the user has to keep in sync.
COLOR_STYLE_THEMES: dict[str, str] = {
    "standard": GRAPHITE_DARK.name,
    "inverted": PAPER_LIGHT.name,
    "protanopia": PROTANOPIA_DARK.name,
}


#: Map palette paired with each chrome theme.
THEME_MAP_PALETTES: dict[str, MapPalette] = {
    GRAPHITE_DARK.name: DARK_MAP,
    PAPER_LIGHT.name: LIGHT_MAP,
    PROTANOPIA_DARK.name: PROTANOPIA_MAP,
}


def theme_for_color_style(style: str | None) -> Theme:
    """Return the chrome theme paired with a canvas ``color_style``.

    Unknown or missing values fall back to the default dark theme rather than
    raising, so a hand-edited settings file cannot prevent the app starting.
    """
    key = (style or "").strip().lower()
    return THEMES[COLOR_STYLE_THEMES.get(key, DEFAULT_THEME_NAME)]


def map_palette(theme: Theme | None = None) -> MapPalette:
    """Return the map palette paired with ``theme`` (default: dark)."""
    if theme is None:
        return DARK_MAP
    return THEME_MAP_PALETTES.get(theme.name, DARK_MAP)


# ---------------------------------------------------------------------------
# Style-sheet generation
# ---------------------------------------------------------------------------

# Semantic object names. Assign with ``widget.setObjectName(...)`` and the
# generated style sheet styles it -- instead of an inline ``setStyleSheet``
# call, which breaks the cascade and cannot follow a theme change.
OBJ_HINT = "hint"                    # de-emphasised helper text
OBJ_EMPHASIS = "emphasis"            # the resolved value a panel is acting on
OBJ_ATTRIBUTION = "attribution"      # third-party data credit, smallest text
OBJ_STATUS = "statusText"            # secondary prose: readiness, validation
OBJ_ERROR_TEXT = "errorText"         # the same prose when it reports a problem
OBJ_PROGRESS_DETAIL = "progressDetail"   # byte counts / phase under a bar
OBJ_SECTION_LABEL = "sectionLabel"   # small caps-ish group heading
OBJ_CARD = "card"                    # flat panel replacing QGroupBox
OBJ_CARD_TITLE = "cardTitle"
OBJ_PLAIN = "plainContainer"         # groups widgets without painting anything
OBJ_HEADER_BAR = "headerBar"         # window-top identity/breadcrumb strip
OBJ_NAV_RAIL = "navRail"             # left source navigation
OBJ_NAV_RAIL_HEADER = "navRailHeader"  # small caption above the rail entries
OBJ_SIDEBAR = "viewerSidebar"        # right context sidebar in the viewer
OBJ_DOCK_TITLE = "dockTitle"         # label in a dock's own title-bar widget
OBJ_REPORT = "report"                # read-only column-aligned text report
OBJ_GUIDE_DIALOG = "guideDialog"     # the interaction guide window
OBJ_GUIDE_BODY = "guideBody"         # its scrollable rich-text body
OBJ_PRIMARY = "primaryAction"        # the one accent button per panel
OBJ_DANGER = "dangerAction"          # destructive action
OBJ_GHOST = "ghostAction"            # borderless tertiary action
OBJ_NUMERIC = "numeric"              # monospace tabular value
OBJ_CANVAS_HOST = "canvasHost"       # frame hosting the scientific canvas

# Availability chip. Styled by Qt property selector rather than by rewriting a
# style sheet per update, so it follows a theme change like everything else.
#: Set on a nav-rail list whose rows are a single line, so they do not inherit
#: the taller row the two-line sounding rows need.
PROP_COMPACT = "compact"

OBJ_AVAIL_DOT = "availDot"
OBJ_AVAIL_TEXT = "availText"
OBJ_AVAIL_STATION = "availStation"

#: Dynamic Qt property carrying the availability state.
PROP_AVAIL_STATUS = "availStatus"

#: Maps each availability state onto a :class:`Theme` colour role.
#:
#: The state names are duplicated from ``sharpmod.gui_workers`` rather than
#: imported, because this module must stay Qt-free and ``gui_workers`` pulls in
#: the whole Qt widget stack. ``test_gui_theme_tokens`` asserts the two sets
#: agree, so the duplication cannot drift silently.
#:
#: Note "checking" maps to ``info``, not ``warning``. It previously shared amber
#: with the fallback state, which conflated "still working on it" with "the
#: requested cycle is missing and an older one was substituted" -- two things a
#: user needs to tell apart at a glance.
AVAIL_STATUS_ROLES: dict[str, str] = {
    "unknown": "text_tertiary",       # not probed yet: no signal to give
    "checking": "info",               # probe in flight
    "available": "success",           # a usable sounding exists
    "fallback": "warning",            # only an earlier cycle exists
    "insufficient": "text_secondary",  # present but too sparse to be useful
    "unavailable": "danger",          # nothing archived, or unreachable
}


def _avail_status_rules(theme: Theme) -> str:
    """Generate the per-state availability-chip rules.

    One dot rule and one text rule per state, selected by the
    :data:`PROP_AVAIL_STATUS` dynamic property.
    """
    lines = []
    for status, role in AVAIL_STATUS_ROLES.items():
        colour = getattr(theme, role)
        lines.append(
            f'QLabel#{OBJ_AVAIL_DOT}[{PROP_AVAIL_STATUS}="{status}"] '
            f'{{ background: {colour}; }}')
        lines.append(
            f'QLabel#{OBJ_AVAIL_TEXT}[{PROP_AVAIL_STATUS}="{status}"] '
            f'{{ color: {colour}; }}')
    return "\n".join(lines)


def build_chrome_qss(theme: Theme) -> str:
    """Generate the complete chrome style sheet for ``theme``.

    Applied once on ``QApplication`` so it also reaches widgets built later --
    the picker materializes four of its five source panels lazily, and dialogs
    are constructed on demand, so a per-window style sheet would miss them.
    """
    t = theme
    s = SPACE
    r = RADIUS
    h = CONTROL_H
    ui = font_stack_css(FAMILY_UI_STACK)
    mono = font_stack_css(FAMILY_MONO_STACK)

    return f"""
/* ===================================================================
 * SHARPpy Reimagined -- chrome style sheet
 * Generated from sharpmod.theme tokens for theme "{t.name}".
 * Do not hand-edit: change the tokens instead.
 * =================================================================== */

/* --- Base ---------------------------------------------------------- */

QWidget {{
    background: {t.surface};
    color: {t.text_primary};
    font-family: {ui};
    font-size: {FONT_PT['body']}pt;
}}

QMainWindow, QDialog {{
    background: {t.surface};
}}

/* Containers must not repaint the surface, or nested panels stack
 * progressively lighter/darker tints on top of each other. */
QScrollArea, QSplitter, QStackedWidget, QTabWidget {{
    background: transparent;
    border: 0;
}}

QLabel {{
    background: transparent;
    color: {t.text_primary};
}}

QToolTip {{
    background: {t.surface_overlay};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['sm']}px;
    padding: {s['xs']}px {s['sm']}px;
}}

/* --- Semantic text roles ------------------------------------------- */

QLabel#{OBJ_HINT} {{
    color: {t.text_tertiary};
    font-size: {FONT_PT['small']}pt;
}}

QLabel#{OBJ_ATTRIBUTION} {{
    color: {t.text_tertiary};
    font-size: {FONT_PT['caption']}pt;
}}

QLabel#{OBJ_EMPHASIS} {{
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

/* Secondary prose: readiness and validation sentences. Deliberately not
 * monospace -- only columns of figures get the mono family. */
/* The validating counterpart of OBJ_STATUS: same size and role, but reporting a
 * problem. Exists so a label that alternates between the two states can swap
 * object name rather than carrying an inline colour, which outranks this sheet
 * and cannot follow a theme change -- the timeline's summary line was hardcoded
 * to the dark palette and rendered pale grey on white on paper-light. */
QLabel#{OBJ_ERROR_TEXT} {{
    color: {t.danger};
    font-size: {FONT_PT['small']}pt;
}}

QLabel#{OBJ_STATUS} {{
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
}}

/* Byte counts, transfer rates, and phase counters, which are figures that
 * change in place -- monospace stops the text jittering as digits change. */
QLabel#{OBJ_PROGRESS_DETAIL} {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {FONT_PT['small']}pt;
}}

QLabel#{OBJ_SECTION_LABEL} {{
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

QLabel#{OBJ_NUMERIC}, QLabel[role="numeric"] {{
    font-family: {mono};
}}

/* --- Shell: header bar and navigation rail ------------------------- */

QFrame#{OBJ_HEADER_BAR} {{
    background: {t.surface_raised};
    border-bottom: 1px solid {t.border};
}}

QFrame#{OBJ_NAV_RAIL} {{
    background: {t.surface_raised};
    border: 0;
    border-right: 1px solid {t.border};
}}

QLabel#{OBJ_NAV_RAIL_HEADER} {{
    color: {t.text_tertiary};
    font-size: {FONT_PT['caption']}pt;
    font-weight: {WEIGHT['semibold']};
    padding: 0 {s['sm']}px {s['xxs']}px {s['sm']}px;
}}

/* The rail's list must not inherit the sunken, bordered treatment that data
 * lists get -- it is navigation chrome sitting on the rail surface. */
QListWidget#{OBJ_NAV_RAIL} {{
    background: transparent;
    border: 0;
    padding: 0;
    outline: 0;
}}

QListWidget#{OBJ_NAV_RAIL}::item {{
    color: {t.text_secondary};
    padding: {s['sm']}px {s['sm']}px {s['sm']}px {s['md']}px;
    margin: {s['xxs']}px 0;
    border-radius: {r['md']}px;
    /* Reserve the accent bar's width on every row so selecting one does not
     * shift its label sideways. */
    border-left: {s['xxs']}px solid transparent;
    min-height: {h['md']}px;
}}

QListWidget#{OBJ_NAV_RAIL}::item:hover {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

/* Tinted background plus an accent bar, rather than a filled accent row:
 * a solid accent block for the current section overpowers the panel beside it. */
QListWidget#{OBJ_NAV_RAIL}::item:selected {{
    background: {t.accent_subtle};
    color: {t.text_primary};
    border-left: {s['xxs']}px solid {t.accent};
    font-weight: {WEIGHT['semibold']};
}}

/* Single-line rows: the padding above keeps a two-line row legible, which is
 * far too airy when the row is just a member name. */
QListWidget#{OBJ_NAV_RAIL}[{PROP_COMPACT}="true"]::item {{
    padding: {s['xs']}px {s['sm']}px {s['xs']}px {s['md']}px;
    min-height: {CONTROL_H['sm']}px;
}}

/* --- Viewer context sidebar ---------------------------------------- */

/* Mirrors the nav rail, but bordered on the left because it docks right. The
 * scientific canvas sits immediately beside it, so the divider is what keeps
 * the chrome from reading as part of the sounding. */
QFrame#{OBJ_SIDEBAR} {{
    background: {t.surface_raised};
    border: 0;
    border-left: 1px solid {t.border};
}}

/* The dock supplies its own title-bar widget (see ``_dock_title_bar``), so
 * QDockWidget::title and ::close-button are not styled here. Qt's built-in
 * title bar was abandoned rather than themed: Fusion derives the close button's
 * rect from title-bar metrics and ignores the QSS width/height, collapsing it
 * to about 16x9px -- too small to aim at, and the only affordance for
 * dismissing the panel. */
QDockWidget {{
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

QLabel#{OBJ_DOCK_TITLE} {{
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* A column-aligned text report. The family has to come from here rather than a
 * QFont: render.install_font patches QFont process-wide when the first sounding
 * opens, so a face chosen in Python is replaced by the chart font. */
QPlainTextEdit#{OBJ_REPORT} {{
    font-family: {mono};
    font-size: {FONT_PT['small']}pt;
}}

/* The interaction guide. Prose, so it gets the reading size and a little room
 * to breathe rather than the dense control metrics. */
QDialog#{OBJ_GUIDE_DIALOG} {{
    background: {t.surface};
}}

QTextBrowser#{OBJ_GUIDE_BODY} {{
    background: {t.surface_sunken};
    border: 1px solid {t.border};
    border-radius: {r['md']}px;
    padding: {s['md']}px;
    font-size: {FONT_PT['body']}pt;
}}

/* A square glyph button, e.g. a header close.
 *
 * The size is pinned here rather than with setFixedSize because
 * QStyleSheetStyle recomputes a widget's size constraints from the style sheet
 * and overrides the programmatic fixed size -- setting it in both places gives
 * two sources of truth, and the style sheet wins. Left at the shared
 * min-height it renders 34px tall and inflates its header to 50px. */
QToolButton#{OBJ_GHOST}[{PROP_COMPACT}="true"] {{
    min-width: {CONTROL_H['xs']}px;
    max-width: {CONTROL_H['xs']}px;
    min-height: {CONTROL_H['xs']}px;
    max-height: {CONTROL_H['xs']}px;
    padding: 0;
}}

/* --- Cards (replacing the notched QGroupBox) ----------------------- */

QFrame#{OBJ_CARD} {{
    background: {t.surface_raised};
    border: 1px solid {t.border};
    border-radius: {r['lg']}px;
}}

QLabel#{OBJ_CARD_TITLE} {{
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* A bare container used only to group widgets. The base `QWidget` rule paints
 * the window surface, which is a different colour from a card's raised
 * surface, so an unnamed grouping widget inside a card drew a visible panel of
 * the wrong shade behind its children. The id selector applies to the
 * container alone and is not inherited by what it holds. */
QWidget#{OBJ_PLAIN} {{
    background: transparent;
}}

/* QGroupBox stays styled while panels are migrated to cards, so the app
 * is coherent at every commit rather than only at the end.
 *
 * The title is placed *inside* the border, as a card header. The default
 * `subcontrol-origin: margin` draws it in the margin band above the frame,
 * which reads as a detached floating label rather than a heading that belongs
 * to the panel. Top padding reserves the row the title occupies.
 *
 * That reserved row is `xxl` rather than `xxxl`: the title is one line of the
 * small font, so `xxxl` left a visible empty band under every heading. At eight
 * pixels per card that band was also the single largest avoidable cost in the
 * control rails, where the forecast panel stacks seven cards. */
QGroupBox {{
    background: {t.surface_raised};
    border: 1px solid {t.border};
    border-radius: {r['lg']}px;
    margin-top: 0;
    padding: {s['xxl']}px {s['md']}px {s['md']}px {s['md']}px;
    font-weight: {WEIGHT['semibold']};
}}

QGroupBox::title {{
    subcontrol-origin: border;
    subcontrol-position: top left;
    margin: {s['sm']}px 0 0 {s['md']}px;
    padding: 0;
    /* Explicitly transparent: without this the title sub-control picks up the
     * window `surface` from the QWidget rule and paints a mismatched strip
     * across the top of the card. */
    background: transparent;
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* --- Text and numeric inputs --------------------------------------- */

QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['sm']}px;
    min-height: {h['md']}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {t.accent};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {t.focus_ring};
}}

QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
    border-color: {t.border};
}}

/* Coordinates, forecast hours, and cycle numbers are figures: monospace
 * so digits share an advance width and columns align. */
QDoubleSpinBox, QSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    font-family: {mono};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['xs']}px 0 {s['sm']}px;
    min-height: {h['md']}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QDoubleSpinBox:hover, QSpinBox:hover, QDateEdit:hover,
QTimeEdit:hover, QDateTimeEdit:hover {{
    border-color: {t.accent};
}}

QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus,
QTimeEdit:focus, QDateTimeEdit:focus {{
    border-color: {t.focus_ring};
}}

QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    background: transparent;
    border: 0;
    width: {s['lg']}px;
}}

QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
    background: {t.accent_subtle};
    border-radius: {r['sm']}px;
}}

/* --- Combo boxes --------------------------------------------------- */

QComboBox {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['sm']}px;
    min-height: {h['md']}px;
}}

QComboBox:hover {{
    border-color: {t.accent};
}}

QComboBox:focus {{
    border-color: {t.focus_ring};
}}

QComboBox:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
}}

/* The drop-down sub-control is deliberately *not* restyled.
 *
 * Giving it any property makes Qt render that sub-control from the style sheet
 * instead of from the style, and a style sheet cannot draw the arrow without an
 * `image:` asset. The previous rule set only a border and a width, so the arrow
 * vanished and every combo box in the application looked exactly like a
 * read-only text field -- there was no way to tell "United States (CONUS)" or
 * "HRRR" was a menu. Leaving it alone lets Fusion paint a palette-aware arrow,
 * which matches the date edit's arrow beside it. */

/* The popup is a separate top-level window, so it needs its own
 * surface, border, and selection colours. */
QComboBox QAbstractItemView {{
    background: {t.surface_overlay};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: {s['xs']}px;
    outline: 0;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

/* --- Buttons ------------------------------------------------------- */

QPushButton, QToolButton {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['md']}px;
    min-height: {h['md']}px;
    font-weight: {WEIGHT['medium']};
}}

QPushButton:hover, QToolButton:hover {{
    background: {t.surface_overlay};
    border-color: {t.accent};
}}

QPushButton:pressed, QToolButton:pressed {{
    background: {t.surface_sunken};
}}

QPushButton:focus, QToolButton:focus {{
    border-color: {t.focus_ring};
}}

QPushButton:disabled, QToolButton:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
    border-color: {t.border};
}}

/* Exactly one accent button per panel. Previously every button shared
 * one colour, so nothing read as the primary action. */
QPushButton#{OBJ_PRIMARY} {{
    background: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: {WEIGHT['semibold']};
    min-height: {h['lg']}px;
}}

QPushButton#{OBJ_PRIMARY}:hover {{
    background: {t.accent_hover};
    border-color: {t.accent_hover};
}}

QPushButton#{OBJ_PRIMARY}:pressed {{
    background: {t.accent_pressed};
    border-color: {t.accent_pressed};
}}

QPushButton#{OBJ_PRIMARY}:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
    border-color: {t.border};
}}

QPushButton#{OBJ_DANGER} {{
    background: {t.danger};
    color: {t.accent_text};
    border: 1px solid {t.danger};
    font-weight: {WEIGHT['semibold']};
}}

QPushButton#{OBJ_GHOST}, QToolButton#{OBJ_GHOST} {{
    background: transparent;
    border: 1px solid transparent;
    color: {t.text_secondary};
    font-weight: {WEIGHT['regular']};
}}

QPushButton#{OBJ_GHOST}:hover, QToolButton#{OBJ_GHOST}:hover {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

/* --- Check boxes and radio buttons --------------------------------- */

QCheckBox, QRadioButton {{
    background: transparent;
    color: {t.text_primary};
    spacing: {s['sm']}px;
    padding: {s['xxs']}px 0;
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {t.text_disabled};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {s['lg']}px;
    height: {s['lg']}px;
    background: {t.surface_sunken};
    border: 1px solid {t.border_strong};
}}

QCheckBox::indicator {{
    border-radius: {r['sm']}px;
}}

QRadioButton::indicator {{
    border-radius: {s['sm']}px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {t.accent};
    border-color: {t.accent};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {t.surface};
    border-color: {t.border};
}}

/* --- Tabs ---------------------------------------------------------- */

QTabWidget::pane {{
    background: transparent;
    border: 1px solid {t.border};
    border-radius: {r['lg']}px;
    top: -1px;
}}

QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-top-left-radius: {r['md']}px;
    border-top-right-radius: {r['md']}px;
    padding: {s['sm']}px {s['lg']}px;
    margin-right: {s['xxs']}px;
    font-weight: {WEIGHT['medium']};
}}

QTabBar::tab:hover {{
    background: {t.surface_raised};
    color: {t.text_primary};
}}

/* A 2 px accent underline reads as current far more clearly than the
 * previous filled-tab treatment. */
QTabBar::tab:selected {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border-color: {t.border};
    border-bottom: 2px solid {t.accent};
}}

QTabBar::tab:disabled {{
    color: {t.text_disabled};
}}

/* --- Lists and tables --------------------------------------------- */

QListWidget, QListView, QTreeView, QTableWidget, QTableView {{
    background: {t.surface_sunken};
    alternate-background-color: {t.surface};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: {s['xs']}px;
    outline: 0;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QListWidget::item, QListView::item, QTreeView::item {{
    padding: {s['xs']}px {s['sm']}px;
    border-radius: {r['sm']}px;
}}

QListWidget::item:hover, QListView::item:hover, QTreeView::item:hover {{
    background: {t.accent_subtle};
}}

QListWidget::item:selected, QListView::item:selected,
QTreeView::item:selected {{
    background: {t.accent};
    color: {t.accent_text};
}}

QTableWidget::item, QTableView::item {{
    padding: {s['xs']}px {s['sm']}px;
}}

QHeaderView {{
    background: transparent;
}}

QHeaderView::section {{
    background: {t.surface_raised};
    color: {t.text_secondary};
    border: 0;
    border-bottom: 1px solid {t.border};
    padding: {s['sm']}px;
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

QTableCornerButton::section {{
    background: {t.surface_raised};
    border: 0;
}}

/* --- Menus -------------------------------------------------------- */

QMenuBar {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border-bottom: 1px solid {t.border};
    padding: {s['xxs']}px {s['xs']}px;
}}

QMenuBar::item {{
    background: transparent;
    padding: {s['xs']}px {s['md']}px;
    border-radius: {r['sm']}px;
}}

QMenuBar::item:selected {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

QMenu {{
    background: {t.surface_overlay};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['lg']}px;
    padding: {s['xs']}px;
}}

QMenu::item {{
    padding: {s['sm']}px {s['xl']}px {s['sm']}px {s['md']}px;
    border-radius: {r['sm']}px;
}}

QMenu::item:selected {{
    background: {t.accent};
    color: {t.accent_text};
}}

QMenu::item:disabled {{
    color: {t.text_disabled};
}}

QMenu::separator {{
    height: 1px;
    background: {t.border};
    margin: {s['xs']}px {s['sm']}px;
}}

/* --- Progress ----------------------------------------------------- */

QProgressBar {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['sm']}px;
    min-height: {PROGRESS_H}px;
    max-height: {PROGRESS_H}px;
    text-align: center;
    font-family: {mono};
    font-size: {FONT_PT['caption']}pt;
}}

QProgressBar::chunk {{
    background: {t.accent};
    border-radius: {r['sm']}px;
}}

/* --- Toolbar ------------------------------------------------------ */

QToolBar {{
    background: {t.surface_raised};
    border: 0;
    border-bottom: 1px solid {t.border};
    padding: {s['xxs']}px {s['sm']}px;
    spacing: {s['xxs']}px;
}}

/* Toolbar buttons are borderless until hovered, so a row of them reads as one
 * strip rather than a fence of boxed controls. */
QToolBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r['md']}px;
    color: {t.text_secondary};
    padding: {s['xs']}px {s['sm']}px;
    min-height: {h['sm']}px;
    font-weight: {WEIGHT['medium']};
}}

QToolBar QToolButton:hover {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

QToolBar QToolButton:pressed {{
    background: {t.surface_sunken};
}}

/* A latched mode button. Setting a checkable QAction is not enough on its own:
 * with a style sheet in play, Qt stops drawing its native checked indicator, so
 * a checked button would look identical to an unchecked one. */
QToolBar QToolButton:checked {{
    background: {t.accent_subtle};
    border-color: {t.accent};
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

QToolBar QToolButton:disabled {{
    color: {t.text_disabled};
    background: transparent;
}}

QToolBar::separator {{
    background: {t.border};
    width: 1px;
    margin: {s['xs']}px {s['sm']}px;
}}

/* --- Status bar --------------------------------------------------- */

QStatusBar {{
    background: {t.surface_raised};
    color: {t.text_secondary};
    border-top: 1px solid {t.border};
}}

QStatusBar::item {{
    border: 0;
}}

/* --- Scrollbars --------------------------------------------------- */

QScrollBar:vertical {{
    background: transparent;
    width: {s['md']}px;
    margin: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: {s['md']}px;
    margin: 0;
}}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {t.scrollbar};
    border-radius: {r['sm']}px;
    min-height: {s['xxl']}px;
    min-width: {s['xxl']}px;
}}

QScrollBar::handle:hover {{
    background: {t.border_strong};
}}

/* Qt draws stepper arrows and page-gap tracks by default; both look
 * dated and neither is needed with a visible handle. */
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
    border: 0;
    background: transparent;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- Sliders (forecast timeline) ---------------------------------- */

QSlider::groove:horizontal {{
    background: {t.surface_sunken};
    border: 1px solid {t.border_strong};
    border-radius: {r['sm']}px;
    height: {s['xs']}px;
}}

QSlider::sub-page:horizontal {{
    background: {t.accent};
    border-radius: {r['sm']}px;
}}

QSlider::handle:horizontal {{
    background: {t.accent};
    border: 2px solid {t.surface};
    width: {s['md']}px;
    height: {s['md']}px;
    margin: -{s['sm']}px 0;
    border-radius: {s['sm']}px;
}}

QSlider::handle:horizontal:hover {{
    background: {t.accent_hover};
}}

QSlider::handle:horizontal:disabled {{
    background: {t.border_strong};
}}

QSlider::sub-page:horizontal:disabled {{
    background: {t.border};
}}

/* The zoom slider sits inside a toolbar, so it needs tighter vertical metrics
 * than the timeline slider: the default handle margin makes the whole toolbar
 * taller than its buttons require. */
QSlider#zoomSlider {{
    margin: 0 {s['sm']}px;
}}

QSlider#zoomSlider::groove:horizontal {{
    height: {s['xxs']}px;
}}

QSlider#zoomSlider::handle:horizontal {{
    width: {s['md']}px;
    height: {s['md']}px;
    margin: -{s['xs']}px 0;
    border-radius: {s['sm']}px;
}}

/* --- Splitters ---------------------------------------------------- */

QSplitter::handle {{
    background: {t.border};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QSplitter::handle:hover {{
    background: {t.accent};
}}

/* --- Dialog button box -------------------------------------------- */

QDialogButtonBox {{
    button-layout: 2;  /* Windows-style: affirmative action on the right */
}}

/* --- Availability chip -------------------------------------------- */

/* State-driven via a dynamic Qt property, so a theme switch restyles it
 * through the normal cascade. The previous implementation rewrote its own
 * style sheet on every update, which overrode any theme.
 *
 * Colour is never the only signal: the chip always carries a text label
 * alongside the dot. */
QLabel#{OBJ_AVAIL_STATION} {{
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

QLabel#{OBJ_AVAIL_DOT} {{
    border-radius: 7px;
    border: 1px solid {t.surface_sunken};
    background: {t.text_tertiary};
}}

QLabel#{OBJ_AVAIL_TEXT} {{
    font-weight: {WEIGHT['semibold']};
    color: {t.text_tertiary};
}}

{_avail_status_rules(t)}

/* --- Scientific canvas host --------------------------------------- */

/* The canvas paints its own background from sharpmod.colors. The host only
 * supplies a neutral inset so the canvas reads as content within the frame;
 * it must not impose a colour on the canvas itself.
 *
 * Worn by the two sounding hosts (_FixedSoundingScrollArea and
 * _ScaledSoundingView), which both derive from QFrame. They used to carry this
 * colour in an inline style sheet, which outranks this one and is not
 * recomputed, so switching colour style with a sounding open left the surround
 * on the previous theme.
 *
 * Borderless on purpose. These hosts fill the central widget, so a border would
 * be a line against the window edge -- and it would take 2px out of the
 * viewport in each direction, which is exactly what the sidebar width budget
 * and the fit scale are measured against. */
QFrame#{OBJ_CANVAS_HOST} {{
    background: {t.surface_sunken};
    border: 0;
}}
"""
