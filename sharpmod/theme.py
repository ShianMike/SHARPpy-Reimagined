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
    "DEPENDENT_INDENT",
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

#: Bounds on a dropdown popup, counted in rows rather than pixels so they stay in
#: step with the row height whatever ``CONTROL_H['md']`` becomes.
#:
#: ``max`` is what makes menu-height rows safe to apply to every combo box in the
#: application. Without it the rule could only go on the top bar's own dropdown:
#: the forecast-hour list runs to 209 entries, which at a menu row's height is a
#: popup some 7000 px tall -- taller than any screen, so the entries past the
#: bottom were simply unreachable. Bounded, Qt gives the list a scrollbar instead,
#: and a long list behaves like a long menu.
#:
#: ``min`` keeps a one- or two-entry popup a comfortable pointer target rather
#: than a sliver appearing under the cursor.
POPUP_ROWS: dict[str, int] = {
    "min": 2,
    "max": 14,
}

#: Type ramp in **points**. Points (not pixels) so the ramp honours the OS text
#: scaling setting; the previous code mixed ``8pt`` and ``11px`` declarations.
#:
#: ``caption`` is 8.5 rather than 8.0. It carries the UTC clock, the top bar's
#: eyebrow label, and every data attribution -- text that is meant to recede but
#: still be read. At 8.0 those rendered a full 2pt below body copy and read as
#: debug output rather than as chrome.
FONT_PT: dict[str, float] = {
    "caption": 8.5,
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

#: Left indent for a control that belongs to the check box above it.
#:
#: Exactly the indicator column: the indicator's own width plus the gap to its
#: label, both taken from the check-box rule in the generated style sheet. That
#: makes a dependent control's left edge line up with its switch's *label* rather
#: than with the indicator, so the ownership is legible without a box or a rule
#: around the group.
#:
#: Derived rather than eyeballed, because it was eyeballed before: the
#: environmental-context controller hardcoded ``18`` and the outlook, radar, and
#: field controllers indented by nothing at all, so switching one overlay on
#: produced a stack of combo boxes with no visible owner.
DEPENDENT_INDENT: int = SPACE["lg"] + SPACE["sm"]

#: Vertical scrollbar width -- the column a scroll area reserves in its layout,
#: which is why every width budget has to include it or content is clipped once
#: the bar appears.
#:
#: The generated ``QScrollBar`` rule interpolates this constant directly, so the
#: reservation and the painted bar cannot drift. Note that the *handle* is
#: narrower than this: the rule pads the bar by ``SPACE["xxs"]`` on each side so
#: the handle does not butt against the content beside it.
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
    #: A control whose value is a timestamp: a date carrying its weekday, or a
    #: forecast hour carrying the time it is valid at. Both outgrew ``wide`` when
    #: they stopped making the reader compose the value themselves.
    "timestamp": 168,
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

    # Surface station plots. These are *values*, not geography and not markers:
    # the numbers beside a wind barb in the live-observation overlay.
    #
    # They live here rather than in sharpmod.colors because they are drawn on the
    # picker's basemap and have to clear it in every theme, which is a different
    # constraint from the same quantities plotted on the Skew-T's black canvas.
    # The convention they follow is the same one: warm for temperature, cool for
    # dewpoint, so a forecaster reads the pair without a legend.
    obs_temperature: str
    obs_dewpoint: str


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
    # Lighter than the station dot's #E03030 on purpose. Both are red on the same
    # map, so the temperature reading is tinted to read as text rather than as
    # another available-station marker.
    obs_temperature="#FF8A72",
    obs_dewpoint="#5FD98A",
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
    # Deepened rather than merely re-hued: the pale basemap gives small text far
    # less to work with than the dark one, so both readings darken to stay legible.
    obs_temperature="#B3261E",
    obs_dewpoint="#136B3A",
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
    # The conventional station plot is red temperature against green dewpoint,
    # which is the single worst pairing for a protanope -- the two readings sit
    # side by side and mean opposite things. So this palette moves the pair onto
    # the blue/yellow axis protanopia retains, keeping "warm is temperature, cool
    # is dewpoint" intact while making the hues separable.
    obs_temperature="#FFD08A",
    obs_dewpoint="#9BDCF5",
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
OBJ_WARNING_TEXT = "warningText"     # the same prose when it reports a caveat
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
OBJ_NUMERIC = "numeric"              # monospace tabular value, label or combo
OBJ_CANVAS_HOST = "canvasHost"       # frame hosting the scientific canvas

# Top bar. The picker's menu bar doubles as the application's top bar: the
# source dropdown is a left corner widget and the UTC clock a right one. Left
# unstyled the three groups -- source, menus, clock -- ran together into one
# undifferentiated strip at a single visual weight, so none of them read as
# more or less important than the others. These names exist to rank them.
OBJ_TOP_BAR = "topBar"               # frame holding the source cluster
OBJ_TOP_BAR_END = "topBarEnd"        # frame holding the trailing readout
OBJ_TOP_BAR_LABEL = "topBarLabel"    # eyebrow naming that cluster
OBJ_TOP_BAR_SOURCE = "topBarSource"  # the source dropdown itself
OBJ_TOP_BAR_MENU = "topBarMenu"      # that dropdown's popup, styled as a menu
OBJ_UTC_CLOCK = "utcClock"           # right-corner UTC readout

# Card internals.
OBJ_CARD_TOGGLE = "cardToggle"       # a card's collapsible header button
OBJ_CARD_RULE = "cardRule"           # hairline dividing one card's contents

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

/* The middle rung between OBJ_STATUS and OBJ_ERROR_TEXT: the result stands, but
 * it rests on less than it should -- a partial ensemble, a substituted run. Red
 * overstates that and plain secondary text hides it entirely. */
QLabel#{OBJ_WARNING_TEXT} {{
    color: {t.warning};
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

/* A combo box whose entries are figures rather than names -- cycle hours and
 * forecast hours, both of which now carry a timestamp. Monospaced so the columns
 * line up down the list and it reads as a table instead of ragged prose; the
 * proportional face put every ``F012 . Sep 14 12Z`` at a different offset.
 *
 * The popup needs the rule too, and needs it scoped. A popup is a separate
 * top-level window, so the field's family does not reach it, and the shared
 * ``QComboBox QAbstractItemView`` rule is the wrong place to fix that -- it would
 * put model names and region names in mono as well. */
QComboBox#{OBJ_NUMERIC} {{
    font-family: {mono};
}}

QComboBox#{OBJ_NUMERIC} QAbstractItemView {{
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

/* A collapsible card's header (see ``CollapsibleRailSection``).
 *
 * This is a QToolButton because it has to be clickable and carry a chevron, but
 * it is a *heading* -- so it takes OBJ_CARD_TITLE's treatment rather than a
 * button's. It previously wore OBJ_GHOST, which is the borderless *action*
 * style: regular weight at body size, identical to a tertiary link. Five of
 * those stacked down the rail meant no card had a title that read as a title,
 * which is most of why the rail looked like a column of flat grey boxes.
 *
 * The border stays transparent rather than dropping to `border: 0`. Fusion
 * derives the arrow's rectangle from the button's frame, and a style sheet that
 * removes the frame outright takes the chevron with it -- so the disclosure
 * affordance would vanish. Only the bottom edge is painted, which turns the
 * header into a banded row and separates it from the controls below.
 *
 * Hover brightens the text instead of washing the row with `accent_subtle` as
 * OBJ_GHOST does: a full-width tint under a header rule reads as a selected
 * list item, not as a heading you can fold. */
QToolButton#{OBJ_CARD_TOGGLE} {{
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 1px solid {t.border};
    border-radius: 0;
    color: {t.text_secondary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
    padding: 0 0 {s['xs']}px 0;
    text-align: left;
}}

QToolButton#{OBJ_CARD_TOGGLE}:hover {{
    color: {t.text_primary};
}}

QToolButton#{OBJ_CARD_TOGGLE}:focus {{
    color: {t.text_primary};
    border-bottom-color: {t.accent};
}}

/* A hairline separating two ideas inside one card -- currently the map overlays
 * from the sounding-locator overlays below them.
 *
 * Painted as a 1px block rather than declared as a border, because Qt draws a
 * QFrame's HLine shape from the palette through the style and ignores a style
 * sheet border on it. The owning code therefore sets ``NoFrame`` and lets this
 * rule do the drawing, which is also what puts the line on a token colour. */
QFrame#{OBJ_CARD_RULE} {{
    background: {t.border};
    border: 0;
    min-height: 1px;
    max-height: 1px;
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
    font-size: {FONT_PT['subhead']}pt;
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

/* A square glyph tool button -- currently the rail's zoom steppers.
 *
 * The shared button rule pads every button by `md` on each side, which is right
 * for a worded action and wrong for a single "+" or minus sign: the two
 * steppers rendered as wide pills flanking "Reset", so a row that should read
 * as [-][+] Reset read as three mismatched slabs. Squaring them to the control
 * height puts the glyph in the middle of its own target.
 *
 * Pinned here rather than with ``setFixedWidth`` for the reason the OBJ_GHOST
 * variant below documents: QStyleSheetStyle recomputes a widget's size
 * constraints from the style sheet and overrides the programmatic size, so the
 * style sheet has to be the single source of truth. The ghost rule carries an id
 * selector and therefore still wins for dock close buttons. */
QToolButton[{PROP_COMPACT}="true"] {{
    min-width: {h['md']}px;
    max-width: {h['md']}px;
    padding: 0;
}}

/* --- Check boxes and radio buttons --------------------------------- */

/* Unchecked is secondary, checked is primary.
 *
 * The overlay card stacks ten of these, and with every label at `text_primary`
 * the card was a paragraph-shaped wall in which the two or three overlays
 * actually switched on were indistinguishable from the seven that were not.
 * Weight is deliberately *not* changed with state: the label would re-measure
 * on every toggle, and a row that shifts width as you click it reads as a
 * glitch. Colour alone is enough here because the indicator carries the state
 * too -- this is emphasis, not the only signal.
 *
 * The row padding is `xs` rather than `xxs`: at 2px the rows ran together into
 * continuous text, so the card read as prose rather than as a list of controls. */
QCheckBox, QRadioButton {{
    background: transparent;
    color: {t.text_secondary};
    spacing: {s['sm']}px;
    padding: {s['xs']}px 0;
}}

QCheckBox:checked, QRadioButton:checked {{
    color: {t.text_primary};
}}

QCheckBox:hover, QRadioButton:hover {{
    color: {t.text_primary};
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

/* --- Analysis workspace ------------------------------------------- */

/* The workspace is tabbed *into* the sounding sidebar, so Qt's dock tab bar and
 * the workspace's own tab bar stack directly on top of each other. Left
 * identical they read as one confusing double row; a smaller, tighter inner bar
 * reads as a level below the panel switcher above it. */
QTabWidget#analysisWorkspaceTabs > QTabBar::tab {{
    padding: {s['xs']}px {s['md']}px;
    font-size: {FONT_PT['small']}pt;
}}

/* Widened from the app's 1px hairline: the pane headings already separate the
 * table from the chart, so this handle only has to be grabbable. Transparent at
 * rest, tinted on hover, which is what advertises it as draggable. */
QSplitter#analysisEnsembleSplitter::handle:vertical {{
    height: {s['sm']}px;
    background: transparent;
}}

QSplitter#analysisEnsembleSplitter::handle:vertical:hover {{
    background: {t.accent_subtle};
}}

/* Notes are prose, not figures, and are usually read as a paragraph rather than
 * scanned -- so this one input gets breathing room the shared rule does not. */
QPlainTextEdit#analysisWorkspaceNotes {{
    padding: {s['sm']}px;
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

/* --- Top bar and menus -------------------------------------------- */

/* The menu bar doubles as the application's top bar: the picker installs the
 * source dropdown as its top-left corner widget and the UTC clock as its
 * top-right one. So this rule sets the height and rhythm of the whole strip,
 * not just of the four menu titles.
 *
 * The vertical padding must stay at `xxs`. ``gui_viewer._enlarge_canvas``
 * derives the sounding canvas height from ``menuBar().sizeHint().height()``, and
 * that composed geometry is a contract: ``sharpmod-render``'s PNG output shares
 * this code path and is required to stay byte-identical
 * (``test_composed_canvas_matches_the_documented_geometry`` pins it at
 * 1630x1091). Raising this to `xs` made every menu bar 4px taller and moved the
 * rendered PNG. The picker's top bar gets its breathing room from the vertical
 * margins on its own corner widget instead, which only affects the window that
 * actually has one. Horizontal padding is free -- the canvas budget reads the
 * height alone. */
QMenuBar {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border-bottom: 1px solid {t.border};
    padding: {s['xxs']}px {s['sm']}px;
    spacing: {s['xxs']}px;
}}

/* Secondary, not primary. The menus are the least-used group in the bar -- the
 * source dropdown beside them is the control that actually drives the window --
 * but they inherited `text_primary` from the rule above and so carried exactly
 * as much weight as it did. */
QMenuBar::item {{
    background: transparent;
    color: {t.text_secondary};
    padding: {s['xs']}px {s['md']}px;
    border-radius: {r['md']}px;
    font-weight: {WEIGHT['medium']};
}}

QMenuBar::item:selected {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

QMenuBar::item:pressed {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

/* The source cluster. Transparent, so it reads as part of the bar rather than
 * as a panel floating in it, with a hairline on its right edge separating it
 * from the menus. Without that divider the eyebrow, the dropdown, and "File"
 * ran together as one row of same-sized text.
 *
 * The shared height floor is what makes this divider and the one on
 * QFrame#topBarEnd the same length. Each frame sits above different content -- a
 * 25px dropdown at one end, a line of caption text at the other -- so without a
 * common floor the two rules came out 33px and 18px. `sm` clears the taller of
 * the two, so both frames resolve to exactly it. Their children are added centred
 * rather than filled, or the layout would grow them to meet it. */
QFrame#{OBJ_TOP_BAR} {{
    background: transparent;
    border: 0;
    border-right: 1px solid {t.border};
    min-height: {h['sm']}px;
}}

/* An eyebrow, not a heading: it names the control beside it. Previously this
 * wore OBJ_EMPHASIS -- semibold `text_primary` at body size -- which is the
 * treatment for a resolved *value*, so it competed with both the dropdown it
 * labels and the menu titles next to it. */
QLabel#{OBJ_TOP_BAR_LABEL} {{
    color: {t.text_tertiary};
    font-size: {FONT_PT['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* Same material as the menu items beside it and as the sounding window's View
 * toolbar: transparent and borderless at rest, accent-tinted on hover, matching
 * radius, padding, height, and weight.
 *
 * It previously took the shared input treatment -- a `surface_sunken` fill inside
 * a `border_strong` outline, 32px tall -- so the one control in the top bar was
 * the only boxed thing in a strip of borderless text, and read as a form field
 * dropped into a menu rather than as part of the bar.
 *
 * `min-height: 0` is load-bearing, and is why the strip is short. Omitting it does
 * not mean "no floor": the cascade falls back to the shared QComboBox rule, which
 * declares the 32px control height, and padding then stacks on top of that -- so
 * the control rendered 42px tall and forced the bar to 50px, which is twice what
 * the four menu titles beside it need. The floor has to be actively cancelled.
 *
 * The vertical padding is `xxs` where QMenuBar::item uses `xs`, because the two
 * are measured differently and this rule matches the *result* rather than the
 * declaration: Fusion adds its own metrics to a menu-bar item on top of the style
 * sheet's padding, while for a combo box the style sheet is the whole story. At
 * `xs` this came out 29px against the menu titles' 25px; at `xxs` the two sit on
 * one line. Everything else here is QMenuBar::item's own value.
 *
 * The result is under the 28px pointer target CONTROL_H documents. Accepted
 * knowingly: this is a menu-bar entry, and holding that floor is exactly what
 * made it the one item in the strip that did not sit with the others.
 *
 * No `min-width`. The width has to be stable or the menus slide sideways when the
 * source changes, but ``SourceSelector`` already guarantees that with
 * ``setMinimumContentsLength(18)`` -- one more than the longest entry
 * ("Reanalysis (ERA5)"), so every entry clamps to the same floor. */
QComboBox#{OBJ_TOP_BAR_SOURCE} {{
    min-height: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r['md']}px;
    padding: {s['xxs']}px {s['md']}px;
    color: {t.text_secondary};
    font-weight: {WEIGHT['medium']};
}}

/* Drop the arrow. This is the one place ::drop-down *should* be styled, and it is
 * the exact inverse of the warning on the shared QComboBox rule above: touching
 * this sub-control moves it from the style to the style sheet, and a style sheet
 * cannot draw an arrow without an image asset -- so the arrow disappears. There
 * that was the bug; here it is the entire point.
 *
 * A permanent arrow is what still marked this out as a form control among the
 * menu titles: File, Locations, View and Help all open menus and none of them
 * advertises it. The list appears on click, exactly as theirs do. Collapsing the
 * sub-control to zero width also returns its column to the text. */
QComboBox#{OBJ_TOP_BAR_SOURCE}::drop-down {{
    width: 0;
    border: 0;
    background: transparent;
}}

/* QMenuBar::item:selected, again matched declaration for declaration -- a menu
 * title lifts to `text_primary` on a tinted ground, and so does this. */
QComboBox#{OBJ_TOP_BAR_SOURCE}:hover {{
    background: {t.accent_subtle};
    border-color: {t.accent_subtle};
    color: {t.text_primary};
}}

/* Latched while its popup is open, mirroring the View toolbar's :checked state
 * so "this menu is showing" reads identically in both windows. Without it the
 * control looks untouched while its list is down, because the transparent rest
 * state gives Fusion nothing to press. */
QComboBox#{OBJ_TOP_BAR_SOURCE}:on {{
    background: {t.accent_subtle};
    border-color: {t.accent};
    color: {t.text_primary};
}}

/* Keyboard focus has to stay visible now that the resting border is invisible:
 * this is the window's primary navigation control, and it is first in the tab
 * order. */
QComboBox#{OBJ_TOP_BAR_SOURCE}:focus {{
    border-color: {t.focus_ring};
}}

/* ...and so is its popup.
 *
 * Matching the field to the menu items beside it only got halfway: clicking it
 * still opened a form-field list, on a tighter radius with compact rows, so the
 * control read as a menu right up until it was used. These are QMenu's own
 * values, duplicated for the same reason QMenuBar::item's are on the field -- a
 * sub-control and a widget cannot share one rule.
 *
 * Selected by an object name on the *view*, not as ``QComboBox#topBarSource
 * QAbstractItemView``. That descendant form is accepted and silently matches
 * nothing: a combo's popup lives in its own top-level window, so the view is not
 * a style-sheet descendant of the combo even though it is a child of it in Qt's
 * object tree. The measured row height stayed at the shared list value.
 *
 * The name is now set on *every* combo popup in the application, by
 * ``sharpmod.gui_common.install_popup_placement``, so a dropdown anywhere opens
 * with the same metrics and the same relationship to its field as the menus in
 * the top bar. It was restricted to this one view while the height was unbounded,
 * because the forecast-hour list runs to 209 entries and menu-height rows made a
 * popup taller than the screen. :data:`POPUP_ROWS` caps it, so a long list now
 * scrolls the way a long menu does. */
QAbstractItemView#{OBJ_TOP_BAR_MENU} {{
    border-radius: {r['lg']}px;
    padding: {s['xs']}px;
}}

/* Row *height* is not set here. It comes from the view's item delegate, which does
 * not consult the style sheet -- both ``padding`` and ``min-height`` were measured
 * leaving these rows at the compact list height -- so ``_MenuRowDelegate`` in
 * sharpmod.gui_shell supplies it instead. These are the declarations that do paint. */
QAbstractItemView#{OBJ_TOP_BAR_MENU}::item {{
    padding: 0 {s['xl']}px 0 {s['md']}px;
    border-radius: {r['sm']}px;
}}

/* The trailing group, mirroring QFrame#topBar at the other end of the bar.
 *
 * The divider lives on this frame rather than on the clock label so that both
 * rules are the height of a frame with the same vertical margins, and therefore
 * the same length by construction. Carrying it on the label instead meant tuning
 * a `min-height` token until the two happened to agree -- which then silently
 * became the tallest thing in the bar and set its height. */
QFrame#{OBJ_TOP_BAR_END} {{
    background: transparent;
    border: 0;
    border-left: 1px solid {t.border};
    min-height: {h['sm']}px;
}}

/* A readout, not a value the user acts on, so it recedes to secondary.
 *
 * No padding and no height here. ``_install_utc_clock`` pins this label's width
 * from a measured widest-case sample, and because the text is right-aligned any
 * width the style sheet adds after that measurement is clipped off the *left*
 * edge -- which is what once rendered "UTC" as "JTC". Spacing and the divider
 * both belong to the frame above, which cannot affect the measurement. */
QLabel#{OBJ_UTC_CLOCK} {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {FONT_PT['caption']}pt;
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

/* Matches the top bar's treatment, so the window is framed by two strips of the
 * same material rather than by one styled bar and one default one. The padding
 * is what stops the readiness line sitting flush against the window edge. */
QStatusBar {{
    background: {t.surface_raised};
    color: {t.text_secondary};
    border-top: 1px solid {t.border};
    padding: {s['xxs']}px {s['sm']}px;
    font-size: {FONT_PT['small']}pt;
}}

QStatusBar::item {{
    border: 0;
}}

/* --- Scrollbars --------------------------------------------------- */

/* The width is SCROLLBAR_W itself, not a spacing token that happens to equal
 * it: a scroll area reserves this much in its layout, so the two drifting
 * apart clips content or leaves a dead strip.
 *
 * The padding insets the handle *inside* that reservation instead of
 * shrinking it, which is what gives the bar a gutter. Without it the handle
 * filled all 12 px and butted straight against the card border beside it,
 * reading as though the bar sat on top of the rail rather than beside it.
 * Padding on the bar is the only one of the three obvious ways to inset a
 * handle that Qt actually applies -- margin on the handle and a transparent
 * border on the handle both leave the rect at the full bar width. */
QScrollBar:vertical {{
    background: transparent;
    width: {SCROLLBAR_W}px;
    padding: 0 {s['xxs']}px;
    margin: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: {SCROLLBAR_W}px;
    padding: {s['xxs']}px 0;
    margin: 0;
}}

/* Split by orientation. A shared rule also puts a min-width on the vertical
 * handle and a min-height on the horizontal one -- each bar's short axis,
 * where a 24 px floor can only fight the width declared above it. Only the
 * long axis wants a minimum, so the handle stays grabbable on a long rail. */
QScrollBar::handle:vertical {{
    background: {t.scrollbar};
    border-radius: {r['sm']}px;
    min-height: {s['xxl']}px;
}}

QScrollBar::handle:horizontal {{
    background: {t.scrollbar};
    border-radius: {r['sm']}px;
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
