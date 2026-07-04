"""Integration tests for UWyo fetch over a mocked transport (task 11.5).

Exercises :meth:`sharpmod.io.uwyo_decoder.UWyo_Decoder.fetch` end-to-end
against a **recorded / mocked** HTTPS response -- ``urllib.request.urlopen`` is
monkeypatched so no real network call is ever made. Covers Requirement 7:

* **7.1** the request is issued over a verified-HTTPS transport with the
  documented 30-second timeout, and a well-formed response decodes to a
  populated :class:`Profile`;
* **7.5** a "no data for this station/time" response raises
  :class:`StationTimeUnavailableError` (no partial Profile);
* **7.6** an unreachable service / timeout raises :class:`RetrievalError`
  (no partial Profile).
"""

from __future__ import annotations

import socket
import ssl
from datetime import datetime
from urllib.error import URLError

import numpy as np
import numpy.ma as ma
import pytest

from sharpmod.io import uwyo_decoder as uwyo
from sharpmod.io.uwyo_decoder import (
    RetrievalError,
    StationTimeUnavailableError,
    UWyo_Decoder,
)
from sharpmod.tests.uwyo_fixtures import UNAVAILABLE_PAGE, render_uwyo_text

WHEN = datetime(2014, 6, 16, 0)
CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")


class _FakeResponse:
    """Minimal ``urlopen`` return value usable as a context manager."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def _install_urlopen(monkeypatch, handler, captured):
    """Monkeypatch ``uwyo_decoder.urlopen`` with a capturing fake.

    ``handler(url)`` returns the payload bytes to serve, or raises to simulate a
    transport error. Every call's ``url`` / ``timeout`` / ``context`` are
    recorded in ``captured``.
    """
    def _fake_urlopen(url, timeout=None, context=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["context"] = context
        result = handler(url)
        return _FakeResponse(result)

    monkeypatch.setattr(uwyo, "urlopen", _fake_urlopen)


# --------------------------------------------------------------------------- #
# 7.1 successful fetch: verified HTTPS + 30 s timeout + populated Profile
# --------------------------------------------------------------------------- #
def test_fetch_success_decodes_populated_profile(monkeypatch):
    """Req 7.1: a well-formed response decodes to a populated Profile."""
    page = render_uwyo_text(
        pres=[1000.0, 925.0, 850.0, 700.0, 500.0, 300.0],
        hght=[110.0, 780.0, 1480.0, 3050.0, 5760.0, 9500.0],
        tmpc=[24.0, 20.0, 15.0, 4.0, -12.0, -40.0],
        dwpc=[20.0, 16.0, 10.0, -6.0, -25.0, -55.0],
        wdir=[160.0, 180.0, 210.0, 240.0, 270.0, 290.0],
        wspd=[10.0, 22.0, 35.0, 48.0, 70.0, 95.0],
    ).encode("utf-8")

    captured = {}
    _install_urlopen(monkeypatch, lambda url: page, captured)

    prof = UWyo_Decoder().fetch("72558", WHEN)

    # Populated core arrays covering all six reported levels.
    for name in CORE_FIELDS:
        arr = np.asarray(ma.asarray(getattr(prof, name)))
        assert arr.size == 6, f"{name!r} decoded {arr.size} levels, expected 6"
        valid = np.asarray(ma.asarray(getattr(prof, name)).compressed(),
                           dtype=float)
        assert valid.size > 0, f"{name!r} has no valid values"


def test_fetch_uses_https_with_verification_and_30s_timeout(monkeypatch):
    """Req 7.1/7.6: the transport is verified HTTPS with a 30 s timeout."""
    page = render_uwyo_text(
        pres=[1000.0, 850.0, 700.0, 500.0, 300.0],
        hght=[110.0, 1480.0, 3050.0, 5760.0, 9500.0],
        tmpc=[24.0, 15.0, 4.0, -12.0, -40.0],
        dwpc=[20.0, 10.0, -6.0, -25.0, -55.0],
        wdir=[160.0, 210.0, 240.0, 270.0, 290.0],
        wspd=[10.0, 35.0, 48.0, 70.0, 95.0],
    ).encode("utf-8")

    captured = {}
    _install_urlopen(monkeypatch, lambda url: page, captured)

    UWyo_Decoder().fetch("72558", WHEN)

    # HTTPS endpoint.
    assert captured["url"].startswith("https://"), captured["url"]
    # The documented 30-second timeout is passed through verbatim.
    assert captured["timeout"] == UWyo_Decoder.FETCH_TIMEOUT == 30
    # Server-certificate verification is on (a default SSL context).
    ctx = captured["context"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# --------------------------------------------------------------------------- #
# 7.5 missing station/time -> StationTimeUnavailableError (no partial Profile)
# --------------------------------------------------------------------------- #
def test_fetch_missing_station_time_raises_typed_error(monkeypatch):
    """Req 7.5: a 'no data' response raises StationTimeUnavailableError."""
    captured = {}
    _install_urlopen(
        monkeypatch, lambda url: UNAVAILABLE_PAGE.encode("utf-8"), captured)

    with pytest.raises(StationTimeUnavailableError):
        UWyo_Decoder().fetch("72558", datetime(1900, 1, 1, 0))


# --------------------------------------------------------------------------- #
# 7.6 unreachable service / timeout -> RetrievalError (no partial Profile)
# --------------------------------------------------------------------------- #
def test_fetch_unreachable_service_raises_retrieval_error(monkeypatch):
    """Req 7.6: an unreachable service raises RetrievalError."""
    def _boom(url):
        raise URLError("name or service not known")

    captured = {}
    _install_urlopen(monkeypatch, _boom, captured)

    with pytest.raises(RetrievalError):
        UWyo_Decoder().fetch("72558", WHEN)


def test_fetch_timeout_raises_retrieval_error(monkeypatch):
    """Req 7.6: a transport timeout raises RetrievalError."""
    def _timeout(url):
        raise socket.timeout("timed out")

    captured = {}
    _install_urlopen(monkeypatch, _timeout, captured)

    with pytest.raises(RetrievalError):
        UWyo_Decoder().fetch("72558", WHEN)


def test_fetch_url_timeout_reason_raises_retrieval_error(monkeypatch):
    """Req 7.6: a URLError wrapping a timeout also raises RetrievalError."""
    def _url_timeout(url):
        raise URLError(socket.timeout("timed out"))

    captured = {}
    _install_urlopen(monkeypatch, _url_timeout, captured)

    with pytest.raises(RetrievalError):
        UWyo_Decoder().fetch("72558", WHEN)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
