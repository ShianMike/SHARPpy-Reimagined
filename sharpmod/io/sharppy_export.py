"""Export profiles to the shareable SHARPpy text sounding format."""

from __future__ import annotations

import os

__all__ = ["highlighted_profile", "export_profile_to_sharppy",
           "export_collection_to_sharppy"]


def highlighted_profile(prof_col):
    """Return the highlighted/current profile from a SHARPpy ProfCollection."""
    if prof_col is None:
        return None
    for getter in ("getHighlightedProf", "getCurrentProfs", "getProfile"):
        fn = getattr(prof_col, getter, None)
        if not callable(fn):
            continue
        try:
            prof = fn()
        except Exception:
            continue
        if isinstance(prof, dict):
            prof = next(iter(prof.values()), None)
        if prof is not None:
            return prof
    return None


def export_profile_to_sharppy(profile, out_path) -> str:
    """Write ``profile`` as SHARPpy ``%TITLE%``/``%RAW%`` text.

    The upstream profile writer is the canonical SHARPpy text export used by
    the SPC window. This wrapper gives the app and callers a stable, explicit
    function for sharing a sounding regardless of whether the original source
    was observed, model, private, or an edited in-memory profile.
    """
    if profile is None:
        raise ValueError("no profile is available to export")
    writer = getattr(profile, "toFile", None)
    if not callable(writer):
        raise TypeError("profile does not provide SHARPpy toFile() export")
    path = os.fspath(out_path)
    writer(path)
    return path


def export_collection_to_sharppy(prof_col, out_path) -> str:
    """Write the highlighted profile from ``prof_col`` as SHARPpy text."""
    return export_profile_to_sharppy(highlighted_profile(prof_col), out_path)
