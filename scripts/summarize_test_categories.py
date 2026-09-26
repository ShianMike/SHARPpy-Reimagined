#!/usr/bin/env python3
"""Show independently checked test areas from a completed pytest run.

The fast lane remains the sole owner of these tests and of coverage/timing
budgets. This script turns its JUnit report into focused GitHub Actions jobs
without rerunning Qt tests or changing pytest's fixture/process behavior.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


GROUPS = {
    "visualization": "Data visualization",
    "charts": "Charts & soundings",
    "maps": "Maps & overlays",
    "analysis": "Analysis & animation",
    "exports": "Exports & rendering",
    "interface": "GUI & workflows",
    "science": "Scientific calculations",
    "providers": "Data & providers",
    "platform": "Backends & packaging",
}

CHILDREN = {
    "visualization": ("charts", "maps", "analysis", "exports"),
}


def descendants(group: str) -> set[str]:
    """Return leaf groups belonging to a branch of the Actions test tree."""

    children = CHILDREN.get(group)
    return {group} if children is None else set().union(*(descendants(child) for child in children))


def category_for_module(module: str) -> str:
    """Assign every fast-lane test module to exactly one displayed group."""

    if module.startswith(("test_viz_", "test_skewt_", "test_inset_", "test_custom_panel_")) or module in {
        "test_box_field_viz",
        "test_mpl_display",
    }:
        return "charts"
    if module.startswith(("test_gui_map_", "test_gui_locator_", "test_locator_", "test_map_")) or module in {
        "test_conus_county_tiles",
        "test_gui_basemap_cache",
        "test_gui_box_map",
        "test_gui_box_picker",
        "test_gui_domain_outline",
        "test_gui_environmental_context",
        "test_gui_palette_preview",
        "test_gui_picker_overlay_product",
        "test_gui_radar_markers",
        "test_gui_storm_reports_overlay",
        "test_hrrr_products_palettes",
        "test_lambert_grid",
    }:
        return "maps"
    if module.startswith("test_gui_timeline_") or module in {
        "test_animation_exports",
        "test_gui_analysis_workspace",
        "test_gui_animation",
        "test_gui_comparison_charts",
        "test_gui_ensemble_thresholds",
        "test_gui_model_compare",
        "test_gui_overlay_controls",
        "test_gui_trend_tracks",
        "test_timeline_frames",
    }:
        return "analysis"
    if module.startswith("test_gui_") or module == "test_qt_test_environment":
        return "interface"
    if module.startswith(("test_export_", "test_render_")) or module in {
        "test_packaging_render_smoke",
        "test_sharppy_export",
    }:
        return "exports"
    if module.startswith((
        "test_era5_", "test_hrrr_", "test_model_", "test_openmeteo",
        "test_rrfs_", "test_uwyo_",
    )) or module in {
        "test_eccc_geomet",
        "test_igra_provider",
        "test_observations",
        "test_point_vorticity_extractors",
        "test_radar_mosaic",
        "test_satellite_context",
        "test_spc_outlook",
        "test_storm_reports",
        "test_surface_observations",
    }:
        return "providers"
    if module.startswith((
        "test_derived_", "test_ecape_", "test_hazard_", "test_params_",
        "test_profile_", "test_winds_",
    )) or module in {
        "test_accelerated_convective_profile",
        "test_box_analysis",
        "test_box_mean",
        "test_box_sounding",
        "test_colors_tier_property",
        "test_dcp_zero_factor",
        "test_ensemble_members",
        "test_ensemble_thresholds",
        "test_fire_ventilation",
        "test_interp",
        "test_metric_controls",
        "test_comparison",
        "test_t22_marker_semantics",
        "test_reference_agreement_property",
        "test_scalar_pressure_merge",
        "test_winter_snow_ratio",
    }:
        return "science"
    if module.startswith(("test_backend_", "test_python_backend", "test_rust_backend")) or module in {
        "test_console_encoding",
        "test_installation_reference",
        "test_release_packaging",
        "test_sharppy_compat_installer",
        "test_sounding_parameter_guide",
        "test_test_lane_runner",
        "test_test_performance_budget",
        "test_upstream_patches",
        "test_upstream_warnings",
        "test_versioning",
    }:
        return "platform"
    if module in {
        "test_analysis_sessions",
        "test_collection_identity",
        "test_coordinate_input",
        "test_mounted_profile_refresh",
        "test_recent_destinations",
        "test_saved_box_regions",
        "test_saved_locations",
    }:
        return "interface"
    return "providers"


@dataclass(frozen=True)
class Case:
    module: str
    name: str
    seconds: float
    result: str


def read_cases(path: Path) -> list[Case]:
    root = ET.parse(path).getroot()
    cases = []
    for item in root.iter("testcase"):
        classname = item.get("classname", "")
        module = classname.removeprefix("sharpmod.tests.").split(".", 1)[0]
        if not classname.startswith("sharpmod.tests.") or not module:
            continue
        result = "failed" if item.find("failure") is not None or item.find("error") is not None else (
            "skipped" if item.find("skipped") is not None else "passed"
        )
        cases.append(Case(module, item.get("name", "<unnamed>"), float(item.get("time", "0")), result))
    return cases


def _cell(value: str) -> str:
    """Keep test names from breaking Markdown tables or rendering as HTML."""

    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("|", "&#124;")
            .replace("`", "&#96;").replace("\n", " ").replace("\r", " "))


def render_summary(group: str, cases: list[Case], *, source_result: str = "success") -> str:
    title = GROUPS[group]
    leaves = descendants(group)
    selected = [case for case in cases if category_for_module(case.module) in leaves]
    failed = [case for case in selected if case.result == "failed"]
    skipped = sum(case.result == "skipped" for case in selected)
    passed = len(selected) - len(failed) - skipped
    status = "FAIL" if failed or not selected else "PASS"
    noun = "test" if len(selected) == 1 else "tests"
    lines = [
        f"## {title} — {status}",
        "",
        f"**{len(selected)} {noun}** · {passed} passed · {len(failed)} failed · {skipped} skipped",
        "",
        "Results are taken from the Python 3.13 fast lane; these tests are not rerun here.",
    ]
    if source_result != "success":
        lines.extend(("", f"Source fast lane: **{_cell(source_result)}**. Check its coverage and timing gate separately."))
    if not selected:
        lines.extend(("", "No tests matched this group. Check test collection and category rules."))
    else:
        if group in CHILDREN:
            lines.extend((
                "", "| Branch | Passed | Failed | Skipped | Tests |",
                "| --- | ---: | ---: | ---: | ---: |",
            ))
            for child in CHILDREN[group]:
                child_leaves = descendants(child)
                items = [case for case in selected if category_for_module(case.module) in child_leaves]
                lines.append(
                    f"| {GROUPS[child]} | {sum(c.result == 'passed' for c in items)} | "
                    f"{sum(c.result == 'failed' for c in items)} | "
                    f"{sum(c.result == 'skipped' for c in items)} | {len(items)} |"
                )
        modules: dict[str, list[Case]] = defaultdict(list)
        for case in selected:
            modules[case.module].append(case)
        lines.extend((
            "", "<details>", f"<summary>Test modules ({len(modules)})</summary>", "",
            "| Test module | Passed | Failed | Skipped | Sum of test durations |",
            "| --- | ---: | ---: | ---: | ---: |",
        ))
        for module, items in sorted(modules.items()):
            lines.append(
                f"| `{module}.py` | {sum(c.result == 'passed' for c in items)} | "
                f"{sum(c.result == 'failed' for c in items)} | "
                f"{sum(c.result == 'skipped' for c in items)} | "
                f"{sum(c.seconds for c in items):.1f}s |"
            )
        lines.extend(("", "</details>"))
        if failed:
            lines.extend(("", "### Failed tests", ""))
            lines.extend(f"- `{_cell(case.module)}.py::{_cell(case.name)}`" for case in failed[:20])
        lines.extend(("", "### Slowest tests", "", "| Test | Duration |", "| --- | ---: |"))
        for case in sorted(selected, key=lambda c: c.seconds, reverse=True)[:5]:
            lines.append(f"| `{_cell(case.module)}.py::{_cell(case.name)}` | {case.seconds:.1f}s |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    parser.add_argument("--group", choices=tuple(GROUPS), required=True)
    parser.add_argument("--source-result", default="success")
    args = parser.parse_args(argv)
    try:
        cases = read_cases(args.junit)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"Cannot read test results from {args.junit}: {exc}", file=sys.stderr)
        return 2
    summary = render_summary(args.group, cases, source_result=args.source_result)
    print(summary, end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    leaves = descendants(args.group)
    selected = [case for case in cases if category_for_module(case.module) in leaves]
    return 0 if selected and all(case.result != "failed" for case in selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
