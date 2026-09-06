"""One unreachable download mirror must not end a model search.

Herbie lists several archives per model so the others can answer when one
cannot, but it shares a single ``try`` across the whole loop, so an exception
from any one source abandons the rest and the file is reported missing when it
was there all along. Reported and fixed upstream; patched here because a
released fix is months away and the symptom is a failed sounding.

Upstream report: https://github.com/blaylockbk/Herbie/issues/246
Upstream fix:    https://github.com/blaylockbk/Herbie/pull/554
"""

from __future__ import annotations

import logging

import pytest

from sharpmod import upstream_patches as up

AWS = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.t00z.wrfsfcf00.grib2"
AZURE = "https://noaahrrr.blob.core.windows.net/hrrr/hrrr.t00z.wrfsfcf00.grib2"
GOOGLE = "https://storage.googleapis.com/hrrr/hrrr.t00z.wrfsfcf00.grib2"

ALL_SOURCES = {"azure": AZURE, "aws": AWS, "google": GOOGLE}


def _herbie_class(*, reachable=(), raises=(), marker=False):
    """A stand-in Herbie whose search fails for named sources.

    Models the upstream shape that matters: one loop over ``SOURCES`` sharing a
    single ``try``, so the first raise ends the whole search.
    """

    class FakeHerbie:
        def __init__(self, sources=None, priority=None):
            self.SOURCES = dict(ALL_SOURCES if sources is None else sources)
            self.priority = priority
            self.attempts = []

        def _search(self):
            if self.priority is not None:
                self.SOURCES = {
                    name: self.SOURCES[name]
                    for name in self.priority
                    if name in self.SOURCES
                }
            for name, url in self.SOURCES.items():
                self.attempts.append(name)
                if name in raises:
                    raise raises[name]
                if name in reachable:
                    return (url, name)
            return (None, None)

        def find_grib(self):
            return self._search()

        def find_idx(self):
            return self._search()

    if marker:
        FakeHerbie._sign_azure_url = lambda self, url: url
    return FakeHerbie


class _Boom(Exception):
    """Stands for any of the real failures: TLS, DNS, refused, KeyError."""


# --------------------------------------------------------------------------- #
# The bug this exists for
# --------------------------------------------------------------------------- #
def test_the_unpatched_search_abandons_the_remaining_mirrors():
    """Sanity: without the patch, one raise loses the sources behind it."""
    Herbie = _herbie_class(reachable=("google",), raises={"azure": _Boom()})

    with pytest.raises(_Boom):
        Herbie().find_grib()


def test_a_failing_mirror_falls_through_to_a_working_one():
    Herbie = _herbie_class(reachable=("google",), raises={"azure": _Boom()})
    assert up.apply_herbie_source_fallback(Herbie) is True

    found, source = Herbie().find_grib()

    assert source == "google"
    assert found == GOOGLE


def test_the_index_search_is_repaired_too():
    Herbie = _herbie_class(reachable=("aws",), raises={"azure": _Boom()})
    up.apply_herbie_source_fallback(Herbie)

    _found, source = Herbie().find_idx()

    assert source == "aws"


def test_every_mirror_failing_reports_nothing_found():
    """The same empty answer Herbie gives for a file that is genuinely absent."""
    Herbie = _herbie_class(
        raises={"azure": _Boom(), "aws": _Boom(), "google": _Boom()})
    up.apply_herbie_source_fallback(Herbie)

    assert Herbie().find_grib() == (None, None)


def test_a_skipped_mirror_is_logged_with_the_upstream_reference(caplog):
    """A silent skip would be undiagnosable, and the note has to lead somewhere."""
    Herbie = _herbie_class(reachable=("google",), raises={"azure": _Boom()})
    up.apply_herbie_source_fallback(Herbie)

    with caplog.at_level(logging.WARNING, logger=up.__name__):
        Herbie().find_grib()

    assert any("azure" in record.getMessage() for record in caplog.records)
    assert any(up.HERBIE_SOURCE_FALLBACK_PR in record.getMessage()
               for record in caplog.records)


# --------------------------------------------------------------------------- #
# Staying out of the way
# --------------------------------------------------------------------------- #
def test_a_healthy_search_is_unchanged():
    """No mirror failing means the patch must not alter the answer."""
    Herbie = _herbie_class(reachable=("azure", "aws", "google"))
    plain = Herbie().find_grib()
    up.apply_herbie_source_fallback(Herbie)

    assert Herbie().find_grib() == plain


def test_the_caller_s_priority_order_is_kept():
    Herbie = _herbie_class(reachable=("aws", "google"))
    up.apply_herbie_source_fallback(Herbie)

    _found, source = Herbie(priority=["google", "aws"]).find_grib()

    assert source == "google"


def test_a_mirror_excluded_by_priority_is_never_tried():
    """Falling back must not reintroduce a source the caller ruled out."""
    Herbie = _herbie_class(reachable=("google",), raises={"azure": _Boom()})
    up.apply_herbie_source_fallback(Herbie)
    herbie = Herbie(priority=["google"])

    herbie.find_grib()

    assert "azure" not in herbie.attempts


def test_a_single_source_search_is_left_completely_alone():
    """With nothing to fall back to, the error is the useful answer."""
    Herbie = _herbie_class(raises={"azure": _Boom()})
    up.apply_herbie_source_fallback(Herbie)

    with pytest.raises(_Boom):
        Herbie(sources={"azure": AZURE}).find_grib()


def test_the_source_list_survives_the_search():
    """The object outlives the call, so the narrowing must not leak."""
    Herbie = _herbie_class(reachable=("google",), raises={"azure": _Boom()})
    up.apply_herbie_source_fallback(Herbie)
    herbie = Herbie()

    herbie.find_grib()

    assert herbie.SOURCES == ALL_SOURCES


def test_a_keyboard_interrupt_still_stops_the_search():
    """Skipping failures must not swallow a deliberate cancellation."""
    Herbie = _herbie_class(raises={"azure": KeyboardInterrupt()})
    up.apply_herbie_source_fallback(Herbie)

    with pytest.raises(KeyboardInterrupt):
        Herbie().find_grib()


# --------------------------------------------------------------------------- #
# Retiring itself
# --------------------------------------------------------------------------- #
def test_a_herbie_that_already_has_the_fix_is_not_patched():
    """Upgrading the dependency is what removes this, not us remembering to."""
    Herbie = _herbie_class(reachable=("google",),
                           raises={"azure": _Boom()}, marker=True)

    assert up.apply_herbie_source_fallback(Herbie) is False
    with pytest.raises(_Boom):
        Herbie().find_grib()  # upstream's own loop, untouched


def test_applying_twice_changes_nothing():
    """Both entry points call this, and neither knows about the other."""
    Herbie = _herbie_class(reachable=("google",), raises={"azure": _Boom()})

    assert up.apply_herbie_source_fallback(Herbie) is True
    assert up.apply_herbie_source_fallback(Herbie) is False

    _found, source = Herbie().find_grib()
    assert source == "google"


def test_an_unrecognisable_herbie_is_left_alone():
    """A rewritten upstream must not be half-patched into something worse."""

    class Stranger:
        pass

    assert up.apply_herbie_source_fallback(Stranger) is False


def test_no_class_means_no_work():
    assert up.apply_herbie_source_fallback(None) is False


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_the_model_loader_applies_the_patch(monkeypatch):
    """The repair is worthless if the code path that loads Herbie skips it."""
    from sharpmod.tools import model_extract

    seen = []
    monkeypatch.setattr(model_extract, "apply_herbie_source_fallback",
                        lambda cls: seen.append(cls) or True)

    model_extract._load_herbie_class()

    assert len(seen) == 1
    assert hasattr(seen[0], "find_grib")
