"""Regenerate the three sounding captures embedded in the README.

The primary capture intentionally uses archived, time-matched data: the
bundled HRRR point sounding, the SPC categorical outlook valid at that hour,
and the HRRR significant-tornado-parameter field from the same model run.
The two palette captures use the bundled OAX observed sounding and require no
network access.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication

from sharpmod import hrrr_field, map_overlays, render as render_mod, spc_outlook
from sharpmod.gui_settings import _write_config_preferences
from sharpmod.viz.SPCWindow import compose_window
from sharpmod.viz.hodo_locator import overlay_label_at_point


ROOT = Path(__file__).resolve().parents[1]
HRRR_SOURCE = (
    ROOT / "examples" / "soundings" / "hrrr_point_36.68N_95.66W_f018.npz"
)
OAX_SOURCE = ROOT / "examples" / "soundings" / "14061619.OAX"
MAIN_OUTPUT = ROOT / "examples" / "example_sounding.png"
VERSIONED_OUTPUT = ROOT / "docs" / "images" / "v1.1.0"

HRRR_RUN = datetime(2026, 6, 25, 6, tzinfo=timezone.utc)
HRRR_VALID = datetime(2026, 6, 26, 0, tzinfo=timezone.utc)
HRRR_FXX = 18
HRRR_LAT = 36.675168663242175
HRRR_LON = -95.65655745938363


def _complete_metadata(collection, station_id: str) -> None:
    """Apply the same decoder-metadata fallbacks as the public renderer."""
    has = lambda key: key in collection._meta  # noqa: E731, SLF001
    base = (
        collection.getMeta("base_time")
        if has("base_time")
        else collection.getCurrentDate()
    )
    observed = collection.getMeta("observed") if has("observed") else True
    if not has("loc"):
        collection.setMeta("loc", station_id)
    if not has("run"):
        collection.setMeta("run", base)
    if not has("model"):
        collection.setMeta("model", "Archive" if observed else "Model")
    render_mod._resolve_location_title(collection)


def _render_capture(
    source: Path,
    destination: Path,
    *,
    style: str,
    chart: str,
    overlays: tuple[object, ...] = (),
) -> None:
    """Compose, configure, and atomically save one real sounding window."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    render_mod.install_font(app)
    render_mod._apply_sars_match_color()
    render_mod.install_render_patches()

    collection, station_id = render_mod.decode(str(source))
    _complete_metadata(collection, station_id)
    for overlay in overlays:
        map_overlays.attach_locator_overlay(collection, overlay)

    with tempfile.TemporaryDirectory(prefix="sharpmod-readme-") as config_dir:
        config = render_mod.build_config(config_dir)
        _write_config_preferences(config, {"color_style": style})

        scale = render_mod._png_image_scale(render_mod.PNG_IMAGE_HD)
        with render_mod._target_density_pixmaps(scale):
            window, controller = compose_window(
                config,
                collection,
                mount=True,
            )
            try:
                mounted = window.sharpmod_products
                if mounted.blocked:
                    raise RuntimeError(
                        "README sounding products failed to mount: "
                        + "; ".join(mounted.blocked)
                    )
                mounted.streamwiseness.setChart(chart)
                render_mod._apply_render_parcel(
                    window,
                    render_mod.DEFAULT_RENDER_PARCEL,
                )
                render_mod.rebrand_version_label(window)
                render_mod.align_top_row(window)
                render_mod.apply_layout_compensation(window.spc_widget)
                render_mod._grow_for_family_panels(window)
                render_mod.enlarge_canvas(window)

                for _ in range(6):
                    app.processEvents()

                with tempfile.NamedTemporaryFile(
                    suffix=".png",
                    dir=destination.parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                try:
                    saved = render_mod.save_widget_png(
                        window.spc_widget,
                        str(temporary_path),
                        image_mode=render_mod.PNG_IMAGE_HD,
                    )
                    if not saved or temporary_path.stat().st_size == 0:
                        raise RuntimeError(f"Qt did not save {destination}")
                    os.replace(temporary_path, destination)
                finally:
                    temporary_path.unlink(missing_ok=True)
            finally:
                window.close()
                controller.close()
                app.processEvents()

    print(f"wrote {destination.relative_to(ROOT)}")


def regenerate(*, skip_main: bool = False) -> None:
    """Regenerate every README sounding image."""
    if not skip_main:
        outlook = spc_outlook.fetch_layer(HRRR_VALID, product="cat")
        if outlook is None:
            raise RuntimeError("no archived SPC outlook covers the main sounding")
        badge = overlay_label_at_point((outlook,), HRRR_LAT, HRRR_LON)
        if badge is None:
            raise RuntimeError("the main sounding is outside every SPC risk area")

        field = hrrr_field.fetch_field(
            "stp",
            run=HRRR_RUN,
            fxx=HRRR_FXX,
            size=(2048, 1024),
            opacity=0.78,
        )
        if field is None:
            raise RuntimeError("the matching HRRR STP field was not available")

        print(f"main locator overlays: SPC {badge[0]} + {field.title}")
        _render_capture(
            HRRR_SOURCE,
            MAIN_OUTPUT,
            style="standard",
            chart="srw",
            overlays=(field, outlook),
        )

    _render_capture(
        OAX_SOURCE,
        VERSIONED_OUTPUT / "sounding-theta-light-mode.png",
        style="inverted",
        chart="theta",
    )
    _render_capture(
        OAX_SOURCE,
        VERSIONED_OUTPUT / "sounding-streamwiseness-protanopia.png",
        style="protanopia",
        chart="streamwiseness",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-main",
        action="store_true",
        help="regenerate only the two network-free palette examples",
    )
    args = parser.parse_args()
    regenerate(skip_main=args.skip_main)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
