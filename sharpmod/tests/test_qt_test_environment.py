"""Regression coverage for the shared Qt test process configuration."""

from __future__ import annotations

import os


def test_qt_tests_default_to_offscreen_platform():
    """GUI tests must not create native Windows windows during pytest runs."""
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
