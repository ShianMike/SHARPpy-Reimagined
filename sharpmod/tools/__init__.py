"""SHARPpy Reimagined data-extraction tools.

Point-sounding extractors (UWyo, ERA5, WRF-ARW, HRRR) that write the fork's
``.npz`` point-sounding input format, plus a small helper to render a written
``.npz`` to a PNG through the shared headless renderer ("open it in app").
"""

from __future__ import annotations

__all__ = ["render_npz"]


def render_npz(npz_path: str, png_path: str | None = None, **kwargs) -> str:
    """Render a ``.npz`` point sounding to a PNG via the headless renderer.

    Thin convenience wrapper around :func:`sharpmod.render.render` so every
    extractor CLI can offer an ``--render`` option that opens the freshly
    written sounding in the app. Imported lazily because the renderer pulls in
    Qt/PySide6 and the vendored ``sharppy`` widgets.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` point sounding to render.
    png_path : str, optional
        Output PNG path. Defaults to the ``.npz`` stem with a ``.png`` suffix.

    Returns
    -------
    str
        The written PNG path.
    """
    import os

    from sharpmod.render import render

    if png_path is None:
        png_path = os.path.splitext(npz_path)[0] + ".png"
    return render(npz_path, png_path, **kwargs)
