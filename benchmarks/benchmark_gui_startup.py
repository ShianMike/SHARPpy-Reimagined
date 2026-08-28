"""Time the interface work the desktop app does before a user can act.

The existing benchmarks cover decoding. This one covers the chrome: theme
generation, application setup, picker construction, and each step of composing a
sounding viewer. Those are the paths the 0.9.0 redesign added to, and the ones a
user waits on.

Reports raw elapsed medians only. It makes no speed claims and compares nothing
across machines -- the point is attribution, so a slow step can be found rather
than guessed at.

Run:

    python benchmarks/benchmark_gui_startup.py
    python benchmarks/benchmark_gui_startup.py --repeat 7 --json out.json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

# Offscreen so the harness is runnable in CI and does not depend on a desktop.
# Compositing is excluded either way: every measurement below is construction
# and layout, not presentation.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@dataclass
class Sample:
    """Elapsed times for one named operation."""

    name: str
    unit: str
    samples: list[float] = field(default_factory=list)

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples) * 1000.0

    @property
    def min_ms(self) -> float:
        return min(self.samples) * 1000.0

    @property
    def max_ms(self) -> float:
        return max(self.samples) * 1000.0


def _time(fn: Callable[[], object], repeat: int) -> list[float]:
    """Elapsed seconds for ``repeat`` calls, with GC quiet during timing."""
    elapsed: list[float] = []
    for _ in range(repeat):
        gc.collect()
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            start = time.perf_counter()
            fn()
            elapsed.append(time.perf_counter() - start)
        finally:
            if was_enabled:
                gc.enable()
    return elapsed


def _theme_samples(repeat: int) -> list[Sample]:
    """Pure-Python token and style-sheet generation, with no Qt involved."""
    from sharpmod import theme

    out: list[Sample] = []
    for name, value in theme.THEMES.items():
        out.append(Sample(
            f"build_chrome_qss[{name}]", "ms",
            _time(lambda v=value: theme.build_chrome_qss(v), repeat),
        ))
    return out


def _application_samples(repeat: int) -> tuple[object, list[Sample]]:
    """Application-wide setup: fonts, palette, and the style sheet."""
    from qtpy.QtWidgets import QApplication

    from sharpmod import gui_theme
    from sharpmod.theme import DEFAULT_THEME_NAME, THEMES

    app = QApplication.instance() or QApplication([])
    theme = THEMES[DEFAULT_THEME_NAME]

    # First call registers the bundled fonts; later calls reuse them. Both are
    # reported, because only the first is on the cold-start path.
    first = _time(lambda: gui_theme.apply_theme(app, theme=theme), 1)
    rest = _time(lambda: gui_theme.apply_theme(app, theme=theme), repeat)
    return app, [
        Sample("apply_theme[cold, registers fonts]", "ms", first),
        Sample("apply_theme[warm]", "ms", rest),
    ]


def _picker_samples(app, repeat: int) -> list[Sample]:
    """Constructing and laying out the picker window."""
    from sharpmod import gui_picker

    def build() -> None:
        win = gui_picker.PickerWindow()
        win.show()
        for _ in range(4):
            app.processEvents()
        win.close()
        win.deleteLater()
        app.processEvents()

    return [Sample("PickerWindow construct + first layout", "ms",
                   _time(build, repeat))]


def _viewer_samples(app, repeat: int) -> list[Sample]:
    """Composing a sounding viewer, attributed step by step.

    Each ``_install_*`` step is timed separately so an expensive one is
    identifiable instead of hidden inside a single total.
    """
    import tempfile

    from sharpmod import gui_viewer, render as render_mod
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        print("  (skipping viewer: HRRR example unavailable)", file=sys.stderr)
        return []

    render_mod.install_font(app)
    render_mod.install_render_patches()
    prof_col, _ = render_mod.decode(str(example))
    config = render_mod.build_config(tempfile.mkdtemp())

    totals: dict[str, list[float]] = {}

    def record(name: str, fn: Callable[[], object]) -> None:
        gc.collect()
        start = time.perf_counter()
        fn()
        totals.setdefault(name, []).append(time.perf_counter() - start)

    for _ in range(repeat):
        win = None
        try:
            gc.collect()
            start = time.perf_counter()
            win, _controller = compose_window(config, prof_col, mount=True)
            totals.setdefault("compose_window(mount=True)", []).append(
                time.perf_counter() - start)

            record("render layout passes", lambda: (
                render_mod.align_top_row(win),
                render_mod.apply_layout_compensation(win.spc_widget),
                [app.processEvents() for _ in range(4)],
                render_mod._grow_for_family_panels(win),
                [app.processEvents() for _ in range(4)],
                render_mod.enlarge_canvas(win),
                [app.processEvents() for _ in range(6)],
            ))

            record("_install_data_inspector",
                   lambda: gui_viewer._install_data_inspector(win, prof_col))
            record("_install_view_controls",
                   lambda: gui_viewer._install_view_controls(win))
            record("_install_sounding_sidebar",
                   lambda: gui_viewer._install_sounding_sidebar(win))
            record("_install_help_menu",
                   lambda: gui_viewer._install_help_menu(win))
            record("_fit_window_to_screen",
                   lambda: gui_viewer._fit_window_to_screen(app, win))
            record("_bind_view_controls",
                   lambda: gui_viewer._bind_view_controls(win))

            panel = getattr(win, "_sharpmod_sidebar", None)
            if panel is not None:
                # Fires on every focus/time/member change, so it is the one
                # repeated cost the sidebar introduces.
                record("sidebar refresh (per state change)", panel.refresh)
        finally:
            if win is not None:
                win.close()
                win.deleteLater()
            app.processEvents()
            gc.collect()

    return [Sample(name, "ms", values) for name, values in totals.items()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=5,
                        help="samples per operation (default: 5)")
    parser.add_argument("--json", type=str, default=None,
                        help="also write the raw samples to this path")
    args = parser.parse_args(argv)

    samples: list[Sample] = []
    samples += _theme_samples(args.repeat)
    app, application = _application_samples(args.repeat)
    samples += application
    samples += _picker_samples(app, args.repeat)
    samples += _viewer_samples(app, args.repeat)

    width = max(len(s.name) for s in samples)
    print(f"\npython {platform.python_version()} on "
          f"{platform.system()} {platform.machine()}")
    print(f"repeat={args.repeat}, Qt platform={os.environ['QT_QPA_PLATFORM']}\n")
    print(f"{'operation'.ljust(width)}   median      min      max")
    print("-" * (width + 30))
    for sample in samples:
        print(f"{sample.name.ljust(width)}  "
              f"{sample.median_ms:8.2f} {sample.min_ms:8.2f} "
              f"{sample.max_ms:8.2f}")
    print()

    if args.json:
        payload = {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
            "repeat": args.repeat,
            "operations": [
                {"name": s.name, "median_ms": s.median_ms,
                 "min_ms": s.min_ms, "max_ms": s.max_ms,
                 "samples_s": s.samples}
                for s in samples
            ],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
