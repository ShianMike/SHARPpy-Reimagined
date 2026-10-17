"""Input decoding, verified transport, and render configuration."""

from __future__ import annotations

from sharpmod.io import decoder as decoder_mod
from sharppy.viz.preferences import PrefDialog
from sutils.config import Config
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen
import certifi
import os
import ssl


class RenderError(RuntimeError):
    """Raised when rendering a single input fails.

    The failing input is always named so a caller can report exactly which
    sounding could not be rendered (Requirements 11.7, 15.5). No partial PNG is
    written when this is raised.
    """

    def __init__(self, infile: str, message: str,
                 cause: BaseException | None = None):
        self.infile = infile
        self.cause = cause
        super().__init__(f"failed to render {infile!r}: {message}")

def fetch_url(url: str, timeout: float = 30.0) -> bytes:
    """Fetch ``url`` over HTTPS with server-certificate verification enabled.

    Uses :func:`ssl.create_default_context` (verification on) passed as
    ``context=`` to :func:`urllib.request.urlopen`, with the ``certifi`` CA
    bundle. This is the modern replacement for the removed
    ``urlopen(cafile=...)`` shim (Requirement 11.6).
    """
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(url, timeout=timeout, context=context) as response:
            return decoder_mod._read_bounded(  # noqa: SLF001
                response, decoder_mod._max_remote_bytes())  # noqa: SLF001
    except (URLError, OSError, ValueError) as exc:
        raise RenderError(url, f"remote fetch failed: {exc}", cause=exc)

def build_config(out_dir: str) -> Config:
    """Build the renderer config with the selected, contrast-aware palette."""
    cfg_path = os.path.join(out_dir, "sharpmod_render.ini")
    config = Config(cfg_path)
    PrefDialog.initConfig(config)

    _apply_selected_color_style(config)

    config.initialize({("paths", "save_img"): out_dir,
                       ("paths", "save_txt"): out_dir,
                       ("paths", "load_txt"): out_dir})
    return config

def _decode_local_input(infile: str, display_name: str):
    """Decode one already-local input without repeating remote downloads."""
    if infile.lower().endswith(".npz"):
        prof_col, station_id = decoder_mod.load_npz(infile)
        return _accelerate_decoded_collection(prof_col), station_id

    last_err: BaseException | None = None
    for _name, cls in decoder_mod.getDecoders().items():
        try:
            dec = cls(infile)
            prof_col = dec.getProfiles()
            stn_id = dec.getStnId()
            decoder_mod.attach_json_sidecar(prof_col, infile)
            return _accelerate_decoded_collection(prof_col), stn_id
        except Exception as exc:  # noqa: BLE001 - try every decoder in turn
            last_err = exc
            continue
    raise RenderError(
        display_name,
        f"no decoder could read it: {last_err}",
        cause=last_err,
    )

def _accelerate_decoded_collection(prof_collection):
    from sharpmod.sharptab.accelerated_profile import (
        accelerate_profile_collection,
    )

    return accelerate_profile_collection(prof_collection)

def _remote_temp_suffix(url: str) -> str:
    suffix = os.path.splitext(urlparse(url).path)[1].lower()
    if suffix and len(suffix) <= 12 \
            and suffix[1:].replace("-", "").isalnum():
        return suffix
    return ".txt"


_LIGHT_GUIDE_COLOR_KEYS = {
    # These are intentionally subtle construction/grid lines, not text or
    # scientific traces.  Forcing them to text contrast would overwhelm the
    # sounding on a white canvas.
    "skew_itherm_color",
    "skew_adiab_color",
    "skew_mixr_color",
    "hodo_itach_color",
    "spd_itach_color",
}


def _color_style_preferences(style):
    """Return a complete, renderer-ready palette for ``style``.

    The vendored inverted preset predates a light-mode contrast pass and uses
    many bright colors that disappear on white.  Preserve the established
    standard/protanopia values while adapting the inverted scientific colors
    to a 4.5:1 contrast target.  Background guide lines remain deliberately
    subtle.
    """
    from sharppy.viz.preferences import PrefDialog
    from sharpmod.viz import colors

    palette = dict(PrefDialog._styles[style])
    if style in {"standard", "protanopia"}:
        # Preserve SHARPpy Reimagined's documented readable dark-theme amber
        # substitutions, which replace the legacy low-contrast browns.
        palette["alert_l1_color"] = colors.ALERT_L1_COLOR
        palette["alert_l2_color"] = colors.ALERT_L2_COLOR
        return palette

    bg_color = palette["bg_color"]
    fg_color = palette["fg_color"]
    for key, color in tuple(palette.items()):
        if key in {"bg_color", "fg_color"} or key in _LIGHT_GUIDE_COLOR_KEYS:
            continue
        palette[key] = colors.resolve_theme_color(
            color, bg_color, fg_color, minimum=4.5)
    return palette


def _apply_selected_color_style(config, style=None) -> None:
    """Apply one complete color style to a live SHARPpy config."""
    if config is None:
        return
    if style is None:
        try:
            style = config["preferences", "color_style"]
        except Exception:
            return
    if style not in ("standard", "inverted", "protanopia"):
        return
    try:
        config["preferences", "color_style"] = style
        for key, color in _color_style_preferences(style).items():
            config["preferences", key] = color
    except Exception:
        return
