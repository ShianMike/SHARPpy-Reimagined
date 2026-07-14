"""Ordered, version-gated SHARPpy render monkeypatch installation."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from sharpmod import render
from sharpmod.render_patch_registry import (
    PatchSpec,
    RenderPatchError,
    UnsupportedSHARPpyVersion,
    apply_patch_registry,
    detected_sharppy_version,
    validate_sharppy_version,
)


def test_installed_sharppy_version_is_explicitly_supported():
    detected = detected_sharppy_version()

    assert detected == "1.4.0a5"
    assert validate_sharppy_version() == detected


def test_unsupported_sharppy_stops_before_mutation():
    called = []

    with pytest.raises(UnsupportedSHARPpyVersion, match="9.9"):
        apply_patch_registry(
            [PatchSpec("example", lambda: called.append(True))],
            sharppy_version="9.9",
        )

    assert called == []


def test_registry_validates_all_names_before_installing_any_patch():
    called = []
    patches = [
        PatchSpec("same", lambda: called.append("first")),
        PatchSpec("same", lambda: called.append("second")),
    ]

    with pytest.raises(RenderPatchError, match="duplicate"):
        apply_patch_registry(patches, sharppy_version="1.4.0a5")

    assert called == []


def test_registry_preserves_order_and_reports_installed_names():
    called = []
    patches = [
        PatchSpec("first", lambda: called.append("first")),
        PatchSpec("second", lambda: called.append("second")),
    ]

    installed = apply_patch_registry(
        patches, sharppy_version="1.4.0a5")

    assert called == ["first", "second"]
    assert installed == ("first", "second")


def test_renderer_declares_one_named_spec_per_patch_installer():
    patches = render.render_patch_specs()
    names = [patch.name for patch in patches]

    assert len(patches) == 28
    assert len(names) == len(set(names))
    assert names[0] == "title.override"
    assert names[-1] == "tables.spacing"


def test_sounding_title_omits_calendar_date_but_keeps_utc_hours():
    class Collection:
        metadata = {
            "model": "HRRR",
            "run": datetime(2026, 7, 14, 0),
            "base_time": datetime(2026, 7, 14, 0),
            "lat": 41.54,
            "lon": -92.93,
        }

        def getMeta(self, key):  # noqa: N802 - upstream API shape
            return self.metadata.get(key)

        def getCurrentDate(self):  # noqa: N802 - upstream API shape
            return datetime(2026, 7, 14, 6)

    render._install_title_override()
    from sharppy.viz.skew import plotSkewT

    title = plotSkewT.getPlotTitle(SimpleNamespace(prof=None), Collection())

    assert title == (
        "   HRRR Run: 00Z F006  Valid: 06Z"
        "  @41.54\N{DEGREE SIGN}N 92.93\N{DEGREE SIGN}W"
    )
    assert "2026" not in title
