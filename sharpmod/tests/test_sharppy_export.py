"""Tests for explicit SHARPpy text sounding export helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sharpmod.io.sharppy_export import (
    export_collection_to_sharppy,
    export_profile_to_sharppy,
    highlighted_profile,
)


class _ExportableProfile:
    def toFile(self, file_name):
        Path(file_name).write_text(
            "%TITLE%\nTEST   260705/0000\n%RAW%\n%END%\n",
            encoding="utf-8",
        )


def test_export_profile_to_sharppy_writes_canonical_text(tmp_path):
    out = tmp_path / "shared_sounding.txt"

    written = export_profile_to_sharppy(_ExportableProfile(), out)

    assert written == str(out)
    text = out.read_text(encoding="utf-8")
    assert "%TITLE%" in text
    assert "%RAW%" in text
    assert "%END%" in text


def test_export_collection_to_sharppy_uses_highlighted_profile(tmp_path):
    prof = _ExportableProfile()
    prof_col = SimpleNamespace(getHighlightedProf=lambda: prof)
    out = tmp_path / "highlighted.txt"

    assert highlighted_profile(prof_col) is prof
    export_collection_to_sharppy(prof_col, out)

    assert out.read_text(encoding="utf-8").startswith("%TITLE%")


def test_export_profile_to_sharppy_rejects_unexportable_profile(tmp_path):
    with pytest.raises(TypeError):
        export_profile_to_sharppy(SimpleNamespace(), tmp_path / "bad.txt")
