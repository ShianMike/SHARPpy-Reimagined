"""Panel-owned declarations for the ordered SHARPpy render patch registry.

Patch implementations can be moved out of :mod:`sharpmod.render` one panel at
a time without changing their public names or their carefully tested install
order.  Keeping ownership here also makes cross-panel wrapper dependencies
visible instead of hiding them in one long bootstrap function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sharpmod.render_patch_registry import PatchSpec, RenderPatchError


@dataclass(frozen=True)
class PanelPatch:
    """One registry entry and the panel responsible for maintaining it."""

    panel: str
    name: str
    installer_name: str


# Order is behavior. In particular, label transparency wraps placement,
# box-mean wraps the final Skew-T plotData, and frame matching wraps all panel
# draw_frame replacements last.
PANEL_PATCHES = (
    PanelPatch(
        "skewt", "skewt.user-parcel-backend", "_install_user_parcel_acceleration"
    ),
    PanelPatch("skewt", "title.override", "_install_title_override"),
    PanelPatch("skewt", "skewt.title-shrink", "_install_skewt_title_shrink"),
    PanelPatch("skewt", "title.top", "_install_title_top"),
    PanelPatch("skewt", "barbs.custom", "_install_custom_barbs"),
    PanelPatch("hodo", "hodo.0500", "_install_hodo_0500"),
    PanelPatch("hodo", "hodo.zoom", "_install_hodo_zoom"),
    PanelPatch("hodo", "hodo.mean-wind-default", "_install_hodo_mean_wind_center"),
    PanelPatch("hodo", "hodo.interpolation-menu", "_install_hodo_interpolation_menu"),
    PanelPatch("hodo", "hodo.label-fit", "_install_hodo_label_fit"),
    PanelPatch("hodo", "hodo.locator", "_install_hodo_locator"),
    PanelPatch("hodo", "hodo.height-levels", "_install_hodo_height_levels"),
    PanelPatch("skewt", "skewt.level-labels", "_install_skewt_level_labels_fit"),
    PanelPatch("stp", "stp.condense", "_install_stp_condense"),
    PanelPatch("stp", "stp.label-rename", "_install_stp_label_rename"),
    PanelPatch("stp", "stp.xlabel-colors", "_install_stp_xlabel_colors"),
    PanelPatch("stp", "stp.bottom-margin", "_install_stp_bottom_margin"),
    PanelPatch("stp", "stp.box-shrink", "_install_stp_box_shrink"),
    PanelPatch("stp", "stp.prob-box-spacing", "_install_stp_prob_box_spacing"),
    PanelPatch(
        "conditional", "conditional-prob.fit", "_install_conditional_prob_panel_fit"
    ),
    PanelPatch("winter", "winter-text.fit", "_install_winter_text_fit"),
    PanelPatch("fire", "fire-text.fit", "_install_fire_text_fit"),
    PanelPatch("speed", "speed.0500", "_install_speed_0500"),
    PanelPatch("speed", "speed.title-cap", "_install_speed_title_cap"),
    PanelPatch("advection", "advection.font-cap", "_install_advection_font_cap"),
    PanelPatch("skewt", "skewt.mixratio-mask", "_install_skewt_mixratio_mask"),
    PanelPatch("skewt", "skewt.surface-label-mask", "_install_skewt_sfc_label_mask"),
    PanelPatch(
        "skewt",
        "skewt.effective-layer-label-fit",
        "_install_skewt_effective_layer_label_fit",
    ),
    PanelPatch(
        "skewt",
        "skewt.lapse-rate-label-placement",
        "_install_skewt_lapse_rate_label_placement",
    ),
    PanelPatch(
        "skewt",
        "skewt.lapse-rate-label-transparency",
        "_install_skewt_lapse_rate_label_transparency",
    ),
    PanelPatch(
        "hodo",
        "hodo.storm-motion-label-transparency",
        "_install_hodo_storm_motion_label_transparency",
    ),
    PanelPatch("skewt", "skewt.frame-on-top", "_install_skewt_frame_ontop"),
    PanelPatch("skewt", "skewt.box-mean-badge", "_install_skewt_box_mean_badge"),
    PanelPatch(
        "skewt", "skewt.isotherm-label-fit", "_install_skewt_isotherm_label_fit"
    ),
    PanelPatch("slinky", "slinky.title-fit", "_install_slinky_title_fit"),
    PanelPatch("tables", "tables.spacing", "_apply_table_spacing_patch"),
    PanelPatch("panels", "panels.match-skewt-frames", "_install_matching_panel_frames"),
)


def build_patch_specs(namespace: Mapping[str, object]) -> tuple[PatchSpec, ...]:
    """Resolve panel declarations against a render implementation namespace."""
    specs = []
    for patch in PANEL_PATCHES:
        installer = namespace.get(patch.installer_name)
        if not callable(installer):
            raise RenderPatchError(
                f"missing installer {patch.installer_name} for {patch.name}"
            )
        specs.append(PatchSpec(patch.name, installer))
    return tuple(specs)


def patch_names_for_panel(panel: str) -> tuple[str, ...]:
    """Return stable registry names owned by one panel."""
    return tuple(patch.name for patch in PANEL_PATCHES if patch.panel == panel)


__all__ = [
    "PANEL_PATCHES",
    "PanelPatch",
    "build_patch_specs",
    "patch_names_for_panel",
]
