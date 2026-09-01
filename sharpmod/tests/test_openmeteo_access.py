"""Open-Meteo access resolution and credential containment.

Every request this application makes to Open-Meteo is paid for by the user, out
of their own IP allowance or their own subscription. These tests exist to hold
two lines: a credential must never reach a host that did not issue it, and must
never appear in anything that gets written, logged, or shown.

No test here touches the network.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from sharpmod import openmeteo_access as access

SECRET = "om-live-key-3f9c2a17"


def test_no_environment_means_free_direct():
    resolved = access.resolve_access(environ={})
    assert resolved.mode == access.MODE_FREE
    assert resolved.host == access.FREE_HOST
    assert resolved.api_key is None
    assert not resolved.sends_credential
    assert resolved.credential_scope() == "free"


def test_a_key_in_the_environment_selects_customer_direct():
    resolved = access.resolve_access(
        environ={access.API_KEY_ENV: SECRET})
    assert resolved.mode == access.MODE_CUSTOMER
    assert resolved.host == access.CUSTOMER_HOST
    assert resolved.sends_credential


def test_customer_mode_without_a_key_is_reported_not_demoted():
    """Silently falling back to free access would misreport whose quota is used."""
    resolved = access.resolve_access(
        mode=access.MODE_CUSTOMER, environ={})
    assert resolved.mode == access.MODE_CUSTOMER
    assert resolved.api_key is None
    assert not resolved.sends_credential
    assert "key missing" in resolved.describe()
    with pytest.raises(access.OpenMeteoAccessError) as excinfo:
        resolved.require_ready()
    assert access.API_KEY_ENV in str(excinfo.value)


def test_free_mode_refuses_a_key_rather_than_ignoring_it():
    """Sending it would leak it; dropping it would mislead the user."""
    with pytest.raises(access.OpenMeteoAccessError) as excinfo:
        access.resolve_access(
            mode=access.MODE_FREE, environ={access.API_KEY_ENV: SECRET})
    assert SECRET not in str(excinfo.value)


def test_self_hosted_never_forwards_a_customer_key():
    with pytest.raises(access.OpenMeteoAccessError):
        access.resolve_access(
            environ={access.SELF_HOSTED_URL_ENV: "https://om.example.test",
                     access.API_KEY_ENV: SECRET})


def test_self_hosted_needs_a_url():
    with pytest.raises(access.OpenMeteoAccessError):
        access.resolve_access(mode=access.MODE_SELF_HOSTED, environ={})


@pytest.mark.parametrize("url", [
    "ftp://om.example.test", "not-a-url", "https://", "",
])
def test_a_malformed_self_hosted_url_is_refused(url):
    with pytest.raises(access.OpenMeteoAccessError):
        access.resolve_access(
            mode=access.MODE_SELF_HOSTED, base_url=url, environ={})


def test_an_unknown_mode_is_refused():
    with pytest.raises(access.OpenMeteoAccessError):
        access.resolve_access(mode="proxy-through-sharppy", environ={})


# --------------------------------------------------------------------------- #
# Credential containment
# --------------------------------------------------------------------------- #
def _customer():
    return access.resolve_access(environ={access.API_KEY_ENV: SECRET})


def test_the_credential_is_attached_only_for_the_customer_host():
    resolved = _customer()
    query = resolved.authenticated_params({"latitude": 1.0})
    assert query["apikey"] == SECRET

    # The host is re-checked at the boundary, not trusted from construction, so
    # a tampered or mistyped base URL cannot carry the key somewhere else.
    diverted = dataclasses.replace(
        resolved, base_url="https://evil.example.test")
    with pytest.raises(access.OpenMeteoAccessError) as excinfo:
        diverted.authenticated_params({"latitude": 1.0})
    assert SECRET not in str(excinfo.value)


def test_free_access_attaches_nothing():
    resolved = access.resolve_access(environ={})
    assert "apikey" not in resolved.authenticated_params({"latitude": 1.0})


@pytest.mark.parametrize("render", [repr, str, "{}".format, lambda v: f"{v}"])
def test_the_credential_never_renders(render):
    resolved = _customer()
    assert SECRET not in render(resolved)
    assert SECRET not in render(resolved.api_key)


def test_the_credential_survives_neither_asdict_nor_json():
    """``asdict`` recurses, so the secret must redact itself, not rely on a filter."""
    resolved = _customer()
    blob = json.dumps(dataclasses.asdict(resolved), default=str)
    assert SECRET not in blob
    assert access.REDACTED in blob


def test_describe_and_scope_leak_nothing():
    resolved = _customer()
    assert SECRET not in resolved.describe()
    scope = resolved.credential_scope()
    assert SECRET not in scope
    assert scope.startswith("customer-")
    # Stable, so usage counters can distinguish two keys across restarts.
    assert scope == _customer().credential_scope()
    other = access.resolve_access(
        environ={access.API_KEY_ENV: SECRET + "x"}).credential_scope()
    assert other != scope


def test_redacted_params_are_safe_to_log():
    resolved = _customer()
    query = resolved.redacted_params(
        resolved.authenticated_params({"latitude": 1.0}))
    assert query["apikey"] == access.REDACTED


@pytest.mark.parametrize("text", [
    "https://host/v1/forecast?lat=1&apikey=%s&b=2",
    "HTTPError for url: https://host/v1/forecast?apikey=%s",
    "apikey=%s",
])
def test_redact_scrubs_a_credential_from_any_text(text):
    assert SECRET not in access.redact(text % SECRET)
    assert access.REDACTED in access.redact(text % SECRET)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
class _Response:
    def __init__(self, payload=None, *, status=200, reason="OK",
                 content_type="application/json", text="", headers=None):
        self.status_code = status
        self.reason = reason
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": content_type}
        if headers:
            self.headers.update(headers)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_fetch_json_returns_the_decoded_object():
    resolved = access.resolve_access(environ={})
    seen = {}

    def request_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = dict(params or {})
        return _Response({"latitude": 1.0})

    assert access.fetch_json(resolved, {"latitude": 1.0},
                             request_get=request_get) == {"latitude": 1.0}
    assert seen["url"] == access.FREE_BASE_URL + access.FORECAST_PATH


def test_a_transport_failure_is_reported_without_the_credential():
    resolved = _customer()

    def request_get(url, params=None, timeout=None):
        # requests puts the full URL, credential included, in its own messages.
        raise OSError(
            "Failed to reach https://host/v1/forecast?apikey=%s" % SECRET)

    with pytest.raises(access.RetrievalError) as excinfo:
        access.fetch_json(resolved, {}, request_get=request_get)
    assert SECRET not in str(excinfo.value)
    assert access.REDACTED in str(excinfo.value)


def test_a_rate_limit_carries_its_retry_after():
    resolved = access.resolve_access(environ={})

    def request_get(url, params=None, timeout=None):
        return _Response(status=429, reason="Too Many Requests",
                         headers={"Retry-After": "42"})

    with pytest.raises(access.OpenMeteoRateLimited) as excinfo:
        access.fetch_json(resolved, {}, request_get=request_get)
    assert excinfo.value.retry_after == pytest.approx(42.0)


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_credential_points_at_the_environment_variable(status):
    resolved = _customer()

    def request_get(url, params=None, timeout=None):
        return _Response(status=status, reason="Forbidden")

    with pytest.raises(access.OpenMeteoAccessError) as excinfo:
        access.fetch_json(resolved, {}, request_get=request_get)
    assert access.API_KEY_ENV in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


def test_an_in_band_error_is_raised_even_with_status_200():
    resolved = access.resolve_access(environ={})

    def request_get(url, params=None, timeout=None):
        return _Response({"error": True, "reason": "Invalid model"})

    with pytest.raises(access.RetrievalError) as excinfo:
        access.fetch_json(resolved, {}, request_get=request_get)
    assert "Invalid model" in str(excinfo.value)


def test_a_non_json_body_is_refused():
    resolved = access.resolve_access(environ={})

    def request_get(url, params=None, timeout=None):
        return _Response({"ok": 1}, content_type="text/html")

    with pytest.raises(access.RetrievalError):
        access.fetch_json(resolved, {}, request_get=request_get)


def test_a_json_array_is_refused():
    resolved = access.resolve_access(environ={})

    def request_get(url, params=None, timeout=None):
        return _Response([1, 2, 3])

    with pytest.raises(access.RetrievalError):
        access.fetch_json(resolved, {}, request_get=request_get)


def test_customer_mode_without_a_key_never_reaches_the_transport():
    resolved = access.resolve_access(mode=access.MODE_CUSTOMER, environ={})

    def request_get(url, params=None, timeout=None):
        raise AssertionError("must not send a request without a credential")

    with pytest.raises(access.OpenMeteoAccessError):
        access.fetch_json(resolved, {}, request_get=request_get)
