"""Windows console encoding regressions."""

from __future__ import annotations

import io

import pytest

from sharpmod import console


class _Stream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


def test_windows_streams_are_utf8_and_non_throwing(monkeypatch):
    stdout = _Stream()
    stderr = _Stream()
    monkeypatch.setattr(console.sys, "platform", "win32")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    changed = console.configure_windows_unicode_streams(stdout, stderr)

    assert changed == ("stdout", "stderr")
    assert stdout.calls == [{
        "encoding": "utf-8",
        "errors": "backslashreplace",
    }]
    assert stderr.calls == stdout.calls
    assert console.os.environ["PYTHONIOENCODING"] == \
        "utf-8:backslashreplace"


def test_missing_windowed_streams_are_ignored(monkeypatch):
    monkeypatch.setattr(console.sys, "platform", "win32")

    assert console.configure_windows_unicode_streams(
        stdout=object(), stderr=object()) == ()


def test_cp1252_stream_can_write_herbie_status_glyph_after_configuration(
        monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        stdout.write("✅")
    monkeypatch.setattr(console.sys, "platform", "win32")

    console.configure_windows_unicode_streams(stdout, stderr)

    stdout.write("✅")
    stdout.flush()
    assert stdout.encoding.casefold().replace("-", "") == "utf8"
