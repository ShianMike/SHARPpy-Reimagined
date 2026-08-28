"""Contract tests for the chrome design tokens and generated style sheet.

These lock three things that are easy to regress by eye:

1. **Contrast.** Every text and interactive-boundary role clears WCAG AA on
   every surface it can be drawn on, in all three themes. Colours were solved
   against :func:`sharpmod.colors.contrast_ratio`, so the same function is the
   arbiter here.
2. **No hardcoded colour.** The generated style sheet may only contain colours
   that came from a token. This is what stops inline hex values creeping back
   into the chrome, which is the state the redesign replaced.
3. **Font-family survival.** ``sharpmod.render.install_font`` replaces
   ``QtGui.QFont`` process-wide and calls ``app.setFont``. It runs when the
   first sounding opens, i.e. after the picker is visible. Chrome typography
   must survive that.

Full WCAG conformance cannot be asserted from colour values alone -- it also
requires manual assistive-technology testing and expert review. These tests
cover the contrast criteria that *are* mechanically checkable.
"""

from __future__ import annotations

import re

import pytest

from sharpmod import colors
from sharpmod import theme as T

#: WCAG 2.2 AA thresholds.
BODY_TEXT = 4.5   # 1.4.3 Contrast (Minimum)
NON_TEXT = 3.0    # 1.4.11 Non-text Contrast

#: ``(foreground_role, background_roles, required_ratio)``.
CONTRAST_CHECKS = (
    ("text_primary",
     ("surface", "surface_raised", "surface_sunken", "surface_overlay"),
     BODY_TEXT),
    ("text_secondary",
     ("surface", "surface_raised", "surface_sunken", "surface_overlay"),
     BODY_TEXT),
    ("text_tertiary",
     ("surface", "surface_raised", "surface_sunken"),
     BODY_TEXT),
    # A primary button label must stay legible in every interaction state, not
    # just at rest -- a brighter accent that only passed at rest was rejected.
    ("accent_text",
     ("accent", "accent_hover", "accent_pressed"),
     BODY_TEXT),
    # Adjacent surfaces differ by only ~1.05:1, so the outline is what
    # identifies an interactive control. That puts it under 1.4.11.
    ("border_strong",
     ("surface", "surface_raised", "surface_sunken"),
     NON_TEXT),
    ("focus_ring",
     ("surface", "surface_raised", "surface_sunken"),
     NON_TEXT),
    ("accent", ("surface", "surface_raised"), NON_TEXT),
    ("success", ("surface", "surface_raised"), NON_TEXT),
    ("warning", ("surface", "surface_raised"), NON_TEXT),
    ("danger", ("surface", "surface_raised"), NON_TEXT),
    ("info", ("surface", "surface_raised"), NON_TEXT),
)

#: Roles deliberately outside the contrast contract, asserted explicitly so an
#: exemption is a decision on record rather than an omission.
#:
#: ``border``        decoration only: dividers, card edges, table rules. Never
#:                   the sole identifier of a control -- that is
#:                   ``border_strong``.
#: ``text_disabled`` reduced contrast *is* the unavailability signal, and WCAG
#:                   exempts disabled controls.
EXEMPT_ROLES = frozenset({"border", "text_disabled"})

ALL_THEMES = tuple(T.THEMES.values())
THEME_IDS = tuple(t.name for t in ALL_THEMES)

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


# ---------------------------------------------------------------------------
# Contrast
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_every_role_pair_meets_wcag_aa(theme):
    """No text or control boundary falls below its AA threshold."""
    failures = []
    for fg_role, bg_roles, required in CONTRAST_CHECKS:
        for bg_role in bg_roles:
            ratio = colors.contrast_ratio(getattr(theme, fg_role),
                                          getattr(theme, bg_role))
            if ratio < required:
                failures.append(
                    f"{fg_role} on {bg_role}: {ratio:.2f} < {required}")
    assert not failures, (
        f"{theme.name} contrast failures:\n  " + "\n  ".join(failures))


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_exempt_roles_are_not_silently_covered(theme):
    """The exemption list stays honest.

    If a future change raises ``border`` or ``text_disabled`` to full AA, the
    exemption is obsolete and should be deleted rather than left in place
    implying a waiver that is no longer needed.
    """
    for role in EXEMPT_ROLES:
        assert hasattr(theme, role), f"exempt role {role!r} no longer exists"
    assert not (EXEMPT_ROLES & {c[0] for c in CONTRAST_CHECKS}), (
        "a role cannot be both exempt and contrast-checked")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_disabled_text_is_dimmer_than_enabled_text(theme):
    """Disabled text must read as unavailable next to secondary text."""
    on_surface = colors.contrast_ratio(theme.text_disabled, theme.surface)
    secondary = colors.contrast_ratio(theme.text_secondary, theme.surface)
    assert on_surface < secondary, (
        f"{theme.name}: disabled text ({on_surface:.2f}) is not dimmer than "
        f"secondary text ({secondary:.2f})")


#: Surface and border roles, which are fill -- not the accent, and not status.
CHROME_NEUTRAL_ROLES = ("surface", "surface_raised", "surface_sunken",
                        "surface_overlay", "border", "border_strong",
                        "text_primary", "text_secondary", "text_tertiary")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_chrome_surfaces_and_text_are_neutral(theme):
    """Fill and text must carry almost no colour of their own.

    The first version of these palettes was blue-tinted throughout -- #0F1319,
    #161B24, #1C222D and friends, all near 0.4 relative chroma on a ~215 degree
    hue, which is the default dark ramp shipped by every UI framework. Beyond
    looking generic, it competed with the scientific canvas: that canvas is pure
    black carrying saturated data colours, so chrome with its own chroma reads
    as part of the plot.

    Chroma is guarded rather than hue, so a future retint can move the neutrals
    freely but cannot reintroduce a tinted ramp. ``accent``, ``focus_ring`` and
    the status roles are excluded on purpose: they are *supposed* to be the only
    coloured things in the chrome.
    """
    offenders = {
        role: round(_relative_chroma(getattr(theme, role)), 3)
        for role in CHROME_NEUTRAL_ROLES
        if _relative_chroma(getattr(theme, role)) > NEUTRAL_MAX_CHROMA
    }
    assert not offenders, (
        f"{theme.name} chrome is tinted, not neutral: {offenders}")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_the_accent_is_the_most_saturated_chrome_role(theme):
    """The one coloured thing should be the one that means "act here"."""
    accent_chroma = _relative_chroma(theme.accent)
    for role in CHROME_NEUTRAL_ROLES:
        assert _relative_chroma(getattr(theme, role)) < accent_chroma, (
            f"{theme.name}: {role} is at least as saturated as the accent")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_raised_surface_separates_from_the_window_surface(theme):
    """A card must read as a distinct layer, not as the same slab.

    Both palettes previously sat around 1.05:1 here, which is close enough to
    invisible that panels merged into the window behind them. Borders do most of
    the work of identifying a card, but fill should not be actively unhelpful.
    """
    ratio = colors.contrast_ratio(theme.surface_raised, theme.surface)
    assert ratio >= 1.07, (
        f"{theme.name}: raised surface is only {ratio:.3f}:1 from the window "
        f"surface, so cards do not read as a separate layer")


#: Minimum relative-luminance gap for two status colours that share a hue
#: axis. Protanopes cannot separate those by hue, so lightness must do it.
SAME_AXIS_LUMINANCE_GAP = 0.15


def test_protanopia_status_pairs_separate_without_hue():
    """Statuses sharing a hue axis must differ in luminance.

    Four statuses cannot be encoded by hue alone on the single blue/yellow axis
    protanopia preserves. The set is therefore two pairs, each separated by
    lightness. This test asserts the pairs that a protanope *cannot* tell apart
    by hue are the ones carrying a luminance gap.
    """
    theme = T.PROTANOPIA_DARK
    lum = colors._relative_luminance

    same_axis = (
        ("success", "info"),     # both blue
        ("warning", "danger"),   # both yellow
        ("success", "danger"),   # safety-critical: separated on both axes
    )
    for a, b in same_axis:
        gap = abs(lum(getattr(theme, a)) - lum(getattr(theme, b)))
        assert gap >= SAME_AXIS_LUMINANCE_GAP, (
            f"protanopia {a}/{b} differ by only {gap:.3f} in luminance; a "
            f"protanope cannot separate them by hue either")


def test_protanopia_danger_is_the_most_prominent_status():
    """The most urgent state must also be the brightest."""
    theme = T.PROTANOPIA_DARK
    lum = colors._relative_luminance
    others = [lum(getattr(theme, r)) for r in ("success", "warning", "info")]
    assert lum(theme.danger) > max(others)


def test_protanopia_moves_status_off_the_red_green_axis():
    """Success must not stay green, nor danger stay red."""
    for role in ("success", "danger"):
        base = getattr(T.GRAPHITE_DARK, role)
        shifted = getattr(T.PROTANOPIA_DARK, role)
        assert base != shifted, (
            f"protanopia reuses the default {role} colour, so it is still on "
            f"the confusable red/green axis")


# ---------------------------------------------------------------------------
# Token scales
# ---------------------------------------------------------------------------


def test_scales_are_monotonic():
    """Ordered scales must actually ascend, or token names mislead."""
    for name, scale, keys in (
        ("SPACE", T.SPACE, ("xxs", "xs", "sm", "md", "lg", "xl", "xxl",
                            "xxxl")),
        ("RADIUS", T.RADIUS, ("sm", "md", "lg", "xl")),
        ("CONTROL_H", T.CONTROL_H, ("xs", "sm", "md", "lg", "xl")),
        ("FONT_PT", T.FONT_PT, ("caption", "small", "body", "subhead",
                                "title", "heading", "display")),
        ("MOTION_MS", T.MOTION_MS, ("fast", "base", "slow")),
    ):
        values = [scale[k] for k in keys]
        assert values == sorted(values), f"{name} is not ascending: {values}"


def test_control_heights_meet_minimum_pointer_target():
    """Interactive controls need a comfortable pointer target."""
    assert T.CONTROL_H["sm"] >= 28
    assert T.CONTROL_H["md"] >= 32


def test_spacing_scale_is_on_a_four_pixel_base():
    """A consistent base is the point of having a scale at all."""
    for key, value in T.SPACE.items():
        assert value % 2 == 0, f"SPACE[{key}]={value} breaks the even base"


# ---------------------------------------------------------------------------
# Theme selection
# ---------------------------------------------------------------------------


def test_color_style_maps_to_paired_chrome_theme():
    """Chrome follows the canvas palette, so both stay in step."""
    assert T.theme_for_color_style("standard") is T.GRAPHITE_DARK
    assert T.theme_for_color_style("inverted") is T.PAPER_LIGHT
    assert T.theme_for_color_style("protanopia") is T.PROTANOPIA_DARK


def test_light_canvas_gets_light_chrome():
    """The dark-picker/light-viewer split this replaces was the core defect."""
    assert T.theme_for_color_style("inverted").is_dark is False
    assert T.theme_for_color_style("standard").is_dark is True


@pytest.mark.parametrize("value", ["", "  ", "nonsense", None, "STANDARD"])
def test_unknown_color_style_falls_back_instead_of_raising(value):
    """A hand-edited settings file must not stop the app starting."""
    resolved = T.theme_for_color_style(value)
    assert resolved.name in T.THEMES
    if value == "STANDARD":
        # Case-insensitive, since QSettings round-trips raw strings.
        assert resolved is T.GRAPHITE_DARK


# ---------------------------------------------------------------------------
# Generated style sheet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_qss_generates_without_unresolved_placeholders(theme):
    qss = T.build_chrome_qss(theme)
    assert len(qss) > 5000, "style sheet is implausibly short"
    assert "{t." not in qss, "unresolved token placeholder in output"
    assert "None" not in qss, "a token resolved to None"


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_qss_contains_only_token_colours(theme):
    """Every colour in the style sheet must trace back to a token.

    This is the guard that keeps hardcoded hex out of the chrome. The code this
    replaced had 17 inline ``setStyleSheet`` calls with literal values that no
    theme change could reach.
    """
    allowed = {getattr(theme, f.name).upper()
               for f in theme.__dataclass_fields__.values()
               if isinstance(getattr(theme, f.name), str)
               and getattr(theme, f.name).startswith("#")}
    found = {m.group(0).upper() for m in _HEX_RE.finditer(
        T.build_chrome_qss(theme))}
    stray = found - allowed
    assert not stray, (
        f"{theme.name} style sheet has non-token colours: {sorted(stray)}")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_qss_declares_the_chrome_font_family(theme):
    """The style sheet must carry the family, not rely on the app font.

    ``render.install_font`` overwrites the application font mid-session. A
    style-sheet declaration outranks it, so this is what keeps the picker from
    silently restyling itself once a sounding opens.
    """
    qss = T.build_chrome_qss(theme)
    assert "Space Grotesk" in qss
    assert "JetBrains Mono" in qss, "numeric/tabular family missing"
    assert "sans-serif" in qss, "no generic fallback for non-Windows platforms"


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_qss_gives_the_primary_action_the_accent(theme):
    """Exactly one visual weight for the primary action."""
    qss = T.build_chrome_qss(theme)
    assert f"QPushButton#{T.OBJ_PRIMARY}" in qss
    primary_block = qss.split(f"QPushButton#{T.OBJ_PRIMARY} {{", 1)[1]
    primary_block = primary_block.split("}", 1)[0]
    assert theme.accent in primary_block


def test_semantic_object_names_replace_inline_styles():
    """The object names the picker migrates onto must exist as selectors."""
    qss = T.build_chrome_qss(T.GRAPHITE_DARK)
    for obj in (T.OBJ_HINT, T.OBJ_EMPHASIS, T.OBJ_ATTRIBUTION,
                T.OBJ_PROGRESS_DETAIL, T.OBJ_SECTION_LABEL, T.OBJ_CARD,
                T.OBJ_CARD_TITLE, T.OBJ_PRIMARY, T.OBJ_GHOST):
        assert f"#{obj}" in qss, f"no selector for object name {obj!r}"


# ---------------------------------------------------------------------------
# Map palettes
# ---------------------------------------------------------------------------

#: Terrain roles: the basemap drawing, which carries no meaning of its own.
GEOGRAPHY_ROLES = ("background", "graticule", "states", "countries",
                   "coastline")

#: Overlay roles: the things on the map that mean something.
DATA_ROLES = ("station", "station_hover", "selected", "saved", "domain_edge")

#: Marker colours, pinned across the neutral-basemap restyle.
#:
#: These *are* semantics -- the :class:`MapPalette` docstring commits to red
#: meaning an available station, amber meaning the current selection, and cyan
#: meaning a saved location -- so a user's learned reading of the map has to
#: survive a restyle. The terrain colours are deliberately not pinned: they were
#: navy (#05070D landmass through #A9C0DC coastline) and were intentionally
#: replaced with neutrals, which is what the two tests below assert instead.
LEGACY_DARK_MAP_MARKERS = {
    "station": "#E03030",
    "station_edge": "#7A1414",
    "station_hover": "#FF8A8A",
    "station_hover_edge": "#FFFFFF",
    "selected": "#FFD000",
    "selected_edge": "#FFFFFF",
    "saved": "#44D7FF",
    "readout_shadow": "#000000",
    "domain_edge": "#79B8FF",
}

#: Relative chroma below this counts as neutral. The navy basemap this replaced
#: ran from 0.34 to 0.62; the neutral one peaks at 0.15, so the threshold sits
#: in a wide gap rather than shaving a boundary.
NEUTRAL_MAX_CHROMA = 0.20

#: How much more saturated an overlay must be than the terrain under it.
#:
#: A ratio rather than an absolute floor, because relative chroma falls as a
#: colour gets paler: the protanopia map's domain outline is a deliberately
#: high-luminance blue (#9FC9FF) and lands at 0.38, which an absolute floor of
#: 0.45 rejected even though it is unmistakably blue against 0.14 terrain. What
#: actually matters is the gap between overlay and terrain, and that scales with
#: each palette. The tightest real case clears this at 2.6x.
OVERLAY_CHROMA_RATIO = 2.0


def _relative_chroma(hex_colour: str) -> float:
    """Saturation as ``(max - min) / max`` over the RGB channels.

    Relative rather than absolute, because a bright colour naturally spans more
    absolute channel range than a dark one at the same apparent saturation --
    an absolute threshold would call a pale coastline "colourful" and a dark
    navy landmass "neutral", which is backwards.
    """
    raw = hex_colour.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    high, low = max(r, g, b), min(r, g, b)
    return 0.0 if high == 0 else (high - low) / high


def test_marker_semantics_survive_the_basemap_restyle():
    """Red station, amber selection, cyan saved: learned meanings persist."""
    for role, expected in LEGACY_DARK_MAP_MARKERS.items():
        actual = getattr(T.DARK_MAP, role)
        assert actual.upper() == expected.upper(), (
            f"DARK_MAP.{role} drifted: {actual} != {expected}")


@pytest.mark.parametrize("name,palette", sorted(
    (n, p) for n, p in T.THEME_MAP_PALETTES.items()))
def test_basemap_terrain_is_neutral(name, palette):
    """Terrain must not compete with the data drawn on top of it.

    Every terrain role used to be a shade of navy, which put the landmass, the
    model-domain outline and the markers in one hue family -- so nothing
    separated figure from ground. Guarding chroma rather than exact hex means a
    future retint is free to move the neutrals around but cannot quietly
    reintroduce a coloured basemap.
    """
    offenders = {
        role: round(_relative_chroma(getattr(palette, role)), 3)
        for role in GEOGRAPHY_ROLES
        if _relative_chroma(getattr(palette, role)) > NEUTRAL_MAX_CHROMA
    }
    assert not offenders, f"{name} terrain is not neutral: {offenders}"


@pytest.mark.parametrize("name,palette", sorted(
    (n, p) for n, p in T.THEME_MAP_PALETTES.items()))
def test_map_overlays_are_clearly_more_chromatic_than_the_terrain(name, palette):
    """The counterpart: neutralizing terrain must not bleach the overlays.

    Together with :func:`test_basemap_terrain_is_neutral` this pins the whole
    point of the restyle -- terrain recedes, meaning advances -- without pinning
    a single hex value, so the palette stays free to move.
    """
    terrain = max(_relative_chroma(getattr(palette, role))
                  for role in GEOGRAPHY_ROLES)
    floor = terrain * OVERLAY_CHROMA_RATIO
    washed = {
        role: round(_relative_chroma(getattr(palette, role)), 3)
        for role in DATA_ROLES
        if _relative_chroma(getattr(palette, role)) < floor
    }
    assert not washed, (
        f"{name} overlays do not stand out from {terrain:.3f} terrain chroma "
        f"(need {floor:.3f}): {washed}")


def test_dark_map_lines_brighten_with_importance():
    """On dark terrain, importance reads as brighter.

    The light map inverts this (see below); asserting both directions keeps a
    copy-paste between the two palettes from silently flattening one of them.
    """
    lum = colors._relative_luminance
    dark = T.DARK_MAP
    order = ("graticule", "states", "countries", "coastline")
    values = [lum(getattr(dark, role)) for role in order]
    assert values == sorted(values), (
        "dark map line hierarchy is not monotonic: "
        + ", ".join(f"{r}={v:.4f}" for r, v in zip(order, values)))


def test_map_palette_pairs_with_every_chrome_theme():
    """A theme with no map palette would silently fall back to dark."""
    for theme in ALL_THEMES:
        assert theme.name in T.THEME_MAP_PALETTES, (
            f"{theme.name} has no paired map palette")
        assert T.map_palette(theme) is T.THEME_MAP_PALETTES[theme.name]


def test_map_palette_defaults_to_dark_without_a_theme():
    assert T.map_palette(None) is T.DARK_MAP


def test_map_palette_is_hashable():
    """The basemap cache key embeds the palette to invalidate on theme change."""
    for palette in T.THEME_MAP_PALETTES.values():
        assert hash(palette) is not None


def test_light_map_inverts_the_line_hierarchy():
    """On a pale basemap, importance must read as darker, not lighter.

    The dark map goes the other way -- coastline is its brightest line -- so a
    straight colour copy would have made the light map's most important outline
    its faintest.
    """
    lum = colors._relative_luminance
    light = T.LIGHT_MAP
    assert lum(light.coastline) < lum(light.countries) < lum(light.states), (
        "light map line hierarchy is not monotonically darker with importance")

    dark = T.DARK_MAP
    assert lum(dark.coastline) > lum(dark.countries) > lum(dark.states), (
        "dark map line hierarchy changed")


@pytest.mark.parametrize("name,palette", sorted(
    (n, p) for n, p in T.THEME_MAP_PALETTES.items()))
def test_map_markers_are_visible_against_the_basemap(name, palette):
    """Every marker must separate from the terrain it is drawn on."""
    for role in ("station", "station_hover", "selected", "saved"):
        ratio = colors.contrast_ratio(getattr(palette, role),
                                      palette.background)
        assert ratio >= 3.0, (
            f"{name}: {role} is only {ratio:.2f}:1 against the basemap")


@pytest.mark.parametrize("name,palette", sorted(
    (n, p) for n, p in T.THEME_MAP_PALETTES.items()))
def test_selected_crosshair_contrasts_with_its_own_marker(name, palette):
    """The crosshair is drawn over the marker, not over the terrain."""
    ratio = colors.contrast_ratio(palette.selected_crosshair, palette.selected)
    assert ratio >= 3.0, (
        f"{name}: crosshair is only {ratio:.2f}:1 against the selected marker")


@pytest.mark.parametrize("name,palette", sorted(
    (n, p) for n, p in T.THEME_MAP_PALETTES.items()))
def test_readout_text_contrasts_with_its_shadow(name, palette):
    """Map labels are drawn twice, offset, so the pair must separate."""
    ratio = colors.contrast_ratio(palette.readout_text, palette.readout_shadow)
    assert ratio >= 4.5, (
        f"{name}: readout text is only {ratio:.2f}:1 against its shadow")


def test_protanopia_map_separates_station_from_selected():
    """Red station dots against an amber selection is the confusable case."""
    palette = T.PROTANOPIA_MAP
    lum = colors._relative_luminance
    gap = abs(lum(palette.station) - lum(palette.selected))
    assert gap >= SAME_AXIS_LUMINANCE_GAP, (
        f"protanopia station/selected markers differ by only {gap:.3f} in "
        f"luminance")


# ---------------------------------------------------------------------------
# Bundled font availability
# ---------------------------------------------------------------------------


def test_chrome_font_families_are_bundled():
    """The families the chrome depends on must ship with the package.

    The type ramp names "Space Grotesk" and "JetBrains Mono" first in its
    stacks. If those files were removed, Qt would silently substitute a platform
    face and the typography work would disappear -- most visibly in the frozen
    executable, where there is no dev environment to fall back on.
    """
    from sharpmod.resources import font_resolver

    names = font_resolver.font_names()
    assert any(n.startswith("SpaceGrotesk-") for n in names), (
        "Space Grotesk is the first UI family in FAMILY_UI_STACK but is not "
        "bundled")
    assert any(n.startswith("JetBrainsMono-") for n in names), (
        "JetBrains Mono is the first numeric family in FAMILY_MONO_STACK but "
        "is not bundled")


def test_packaging_spec_collects_the_bundled_fonts():
    """The frozen executable must carry the fonts.

    ``collect_all("sharpmod")`` misses package data for an editable install, so
    the spec globs the TTFs explicitly. Losing that glob would produce a build
    that looks correct in development and substitutes fonts once shipped.
    """
    from pathlib import Path

    spec = (Path(__file__).resolve().parents[2]
            / "packaging" / "sharpmod_gui.spec")
    if not spec.is_file():
        pytest.skip("packaging spec not present in this checkout")
    text = spec.read_text(encoding="utf-8")
    assert '"fonts", "*.ttf"' in text, (
        "sharpmod_gui.spec no longer globs the bundled TTFs; the frozen app "
        "would substitute fonts")
    assert "sharpmod/resources/fonts" in text, (
        "spec does not preserve the package-relative fonts destination that "
        "font_resolver expects")


# ---------------------------------------------------------------------------
# Availability chip roles
# ---------------------------------------------------------------------------


def test_availability_states_match_the_worker_module():
    """The state names are duplicated to keep sharpmod.theme Qt-free.

    ``sharpmod.gui_workers`` owns the states but pulls in the whole Qt widget
    stack, so :mod:`sharpmod.theme` restates them. This is the guard that stops
    the two lists drifting apart.
    """
    from sharpmod.gui_workers import AVAIL_STATES

    assert set(T.AVAIL_STATUS_ROLES) == set(AVAIL_STATES), (
        "sharpmod.theme.AVAIL_STATUS_ROLES and gui_workers.AVAIL_STATES "
        "disagree; a state would fall back to 'unknown' styling")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_every_availability_role_exists_on_the_theme(theme):
    for status, role in T.AVAIL_STATUS_ROLES.items():
        assert hasattr(theme, role), (
            f"availability state {status!r} maps to unknown role {role!r}")


#: States whose meanings are close enough that sharing a colour would mislead.
#: Luminance is deliberately *not* asserted here: for normal colour vision these
#: separate by hue, and green-vs-red at similar lightness is the conventional
#: status pair. The luminance requirement applies only to the protanopia theme,
#: where hue is unavailable -- see
#: :func:`test_protanopia_status_pairs_separate_without_hue`.
DISTINCT_STATUS_PAIRS = (
    # "still probing" vs "the requested cycle is missing, using an older one".
    # These shared amber before, conflating progress with a real warning.
    ("checking", "fallback"),
    ("available", "unavailable"),
    ("available", "fallback"),
    ("unknown", "checking"),
)


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
@pytest.mark.parametrize("first,second", DISTINCT_STATUS_PAIRS)
def test_distinct_availability_states_do_not_share_a_colour(
        theme, first, second):
    """States a user must tell apart may not resolve to the same value."""
    a = getattr(theme, T.AVAIL_STATUS_ROLES[first])
    b = getattr(theme, T.AVAIL_STATUS_ROLES[second])
    assert a.upper() != b.upper(), (
        f"{theme.name}: {first!r} and {second!r} both render {a}")


def test_protanopia_availability_pairs_separate_without_hue():
    """On the protanopia theme, same-axis states need a luminance gap.

    ``available`` is cyan and ``unavailable`` is amber, so they sit on opposite
    ends of the axis protanopia preserves and separate by hue. The pairs checked
    here are the ones that do *not*, so lightness has to carry them.
    """
    theme = T.PROTANOPIA_DARK
    lum = colors._relative_luminance

    for first, second in (("available", "unavailable"),
                          ("checking", "available")):
        a = getattr(theme, T.AVAIL_STATUS_ROLES[first])
        b = getattr(theme, T.AVAIL_STATUS_ROLES[second])
        gap = abs(lum(a) - lum(b))
        assert gap >= 0.05, (
            f"protanopia {first!r}/{second!r} differ by only {gap:.3f} in "
            f"luminance")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=THEME_IDS)
def test_availability_rules_are_generated_for_every_state(theme):
    """A missing rule would leave the chip on its default colour."""
    qss = T.build_chrome_qss(theme)
    for status in T.AVAIL_STATUS_ROLES:
        selector = f'[{T.PROP_AVAIL_STATUS}="{status}"]'
        assert qss.count(selector) >= 2, (
            f"{theme.name}: state {status!r} is missing a dot or text rule")
