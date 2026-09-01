"""Open-Meteo access resolution and the secret-safe transport boundary.

Every Open-Meteo request this application makes originates on the user's own
machine against that user's own allowance. There is no SHARPpy-hosted relay and
no credential in the repository, the package, CI, logs, cache metadata, or
generated soundings. Three access modes are supported:

``free-direct``
    The public host, no key. "Their own API" here means the request consumes the
    user's own IP allowance.
``customer-direct``
    The customer host, using a key the user supplies at runtime through
    ``SHARPMOD_OPENMETEO_API_KEY``.
``self-hosted``
    A URL the user operates themselves. A customer key is never forwarded to it.

This module is the only place a credential is read, held, or attached to a
query. It is deliberately Qt-free and imports ``requests`` lazily so the catalog
and normalization layers stay testable without a network stack.

The secret is wrapped in :class:`_Secret` rather than stored as a plain string
so that it redacts itself through ``repr``, ``str``, f-strings, ``dataclasses``
reprs, and ``dataclasses.asdict``. Redaction that depends on every call site
remembering to redact is not redaction.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from sharpmod.tools.era5_extract import ParameterRangeError, RetrievalError

#: Placeholder substituted for a credential anywhere text may be surfaced.
REDACTED = "<redacted>"

#: Environment variable carrying the user's own subscription key. Read at
#: request time, never persisted by this application.
API_KEY_ENV = "SHARPMOD_OPENMETEO_API_KEY"
#: Optional explicit mode override; otherwise the mode is inferred.
MODE_ENV = "SHARPMOD_OPENMETEO_MODE"
#: Base URL for a user-operated Open-Meteo instance.
SELF_HOSTED_URL_ENV = "SHARPMOD_OPENMETEO_URL"

MODE_FREE = "free-direct"
MODE_CUSTOMER = "customer-direct"
MODE_SELF_HOSTED = "self-hosted"
MODES = (MODE_FREE, MODE_CUSTOMER, MODE_SELF_HOSTED)

#: Exact official hosts. Compared literally before a key is ever attached, so a
#: typo, a redirect, or a tampered override cannot send the credential
#: somewhere else.
FREE_HOST = "single-runs-api.open-meteo.com"
CUSTOMER_HOST = "customer-single-runs-api.open-meteo.com"

FREE_BASE_URL = "https://%s" % FREE_HOST
CUSTOMER_BASE_URL = "https://%s" % CUSTOMER_HOST

#: Single Runs path. The run is an explicit query parameter, not a path segment.
FORECAST_PATH = "/v1/forecast"

#: Connect and read timeouts, matching the convention used by the HRRR Zarr
#: reader so provider behaviour is consistent across the application.
DEFAULT_TIMEOUT = (5.0, 30.0)

#: Only these are retried, and only for idempotent GETs.
RETRY_STATUS = (429, 500, 502, 503, 504)

_APIKEY_QUERY_RE = re.compile(r"(apikey=)[^&\s\"']+", re.IGNORECASE)


class OpenMeteoAccessError(RetrievalError):
    """Raised when access is misconfigured, before any request is attempted.

    Distinct from a transport failure so the interface can tell "you need to
    finish setting this up" apart from "the service did not answer".
    """


class _Secret:
    """A string that refuses to print itself.

    ``__slots__`` keeps it out of ``__dict__``-based introspection, and every
    stringification path returns the placeholder. Callers must ask for
    :meth:`reveal` explicitly, which makes the few legitimate uses greppable.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = str(value)

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return REDACTED

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("_Secret", self._value))


def redact(text: Any) -> str:
    """Return ``text`` with any ``apikey`` query value replaced.

    Applied to messages, URLs, and exception text before they reach a log, a
    status bar, a sidecar, or a support bundle. ``requests`` puts the full
    request URL in several exception messages, so this runs on the way out of
    every failure path rather than only where a URL is built by hand.
    """
    return _APIKEY_QUERY_RE.sub(r"\1" + REDACTED, str(text))


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _normalise_base_url(url: str) -> str:
    text = str(url or "").strip().rstrip("/")
    if not text:
        raise OpenMeteoAccessError("Open-Meteo base URL is empty")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"}:
        raise OpenMeteoAccessError(
            "Open-Meteo base URL must be http or https: %s" % redact(text))
    if not parts.hostname:
        raise OpenMeteoAccessError(
            "Open-Meteo base URL has no host: %s" % redact(text))
    return text


@dataclass(frozen=True)
class OpenMeteoAccess:
    """How this installation reaches Open-Meteo, and with whose allowance."""

    mode: str
    base_url: str
    #: Never printed. Excluded from comparison so two accesses differing only by
    #: credential do not silently compare equal or unequal by secret value.
    api_key: _Secret | None = field(default=None, repr=False, compare=False)

    @property
    def host(self) -> str:
        return _host_of(self.base_url)

    @property
    def is_customer(self) -> bool:
        return self.mode == MODE_CUSTOMER

    @property
    def sends_credential(self) -> bool:
        """Whether a request under this access carries a credential."""
        return self.is_customer and self.api_key is not None

    @property
    def forecast_url(self) -> str:
        return self.base_url + FORECAST_PATH

    def describe(self) -> str:
        """Return a human-readable, secret-free summary for UI and logs."""
        if self.mode == MODE_FREE:
            return ("free direct (no key; uses this machine's public "
                    "Open-Meteo allowance)")
        if self.mode == MODE_CUSTOMER:
            state = "key configured" if self.api_key else "key missing"
            return "customer direct (%s, %s)" % (self.host, state)
        return "self-hosted (%s; no key forwarded)" % self.host

    def credential_scope(self) -> str:
        """Return a stable, one-way identifier for usage accounting.

        Usage counters must be able to tell two customer credentials apart
        without storing either. The digest is truncated because it only needs to
        separate accounts on one machine, not resist collision attacks, and a
        shorter value is less tempting to treat as an identifier elsewhere.
        Not surfaced in normal logs or UI.
        """
        if self.mode == MODE_CUSTOMER and self.api_key is not None:
            digest = hashlib.sha256(
                self.api_key.reveal().encode("utf-8")).hexdigest()
            return "customer-%s" % digest[:16]
        if self.mode == MODE_SELF_HOSTED:
            digest = hashlib.sha256(self.host.encode("utf-8")).hexdigest()
            return "self-hosted-%s" % digest[:16]
        return "free"

    def require_ready(self) -> None:
        """Raise when this access cannot be used as configured."""
        if self.mode == MODE_CUSTOMER and self.api_key is None:
            raise OpenMeteoAccessError(
                "Open-Meteo customer access needs your own subscription key. "
                "Set %s in the environment before launching, or leave it unset "
                "to use free direct access." % API_KEY_ENV)

    def authenticated_params(
            self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Return ``params`` plus the credential, if and only if it is allowed.

        The host is re-checked here rather than trusted from construction. This
        is the last point before the query leaves the process, so it is the
        right place to be certain: a credential is attached only for customer
        mode, only when one exists, and only when the target is the exact
        official customer host.
        """
        self.require_ready()
        query = dict(params)
        if not self.sends_credential:
            return query
        if self.host != CUSTOMER_HOST:
            raise OpenMeteoAccessError(
                "refusing to send an Open-Meteo key to %s; the only permitted "
                "credentialed host is %s" % (self.host, CUSTOMER_HOST))
        query["apikey"] = self.api_key.reveal()  # type: ignore[union-attr]
        return query

    def redacted_params(
            self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Return ``params`` safe to log, cache, or attach to an error."""
        query = dict(params)
        if "apikey" in query:
            query["apikey"] = REDACTED
        return query


def resolve_access(
        *,
        mode: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        environ: Mapping[str, str] | None = None,
) -> OpenMeteoAccess:
    """Resolve the access this process should use.

    Explicit arguments win over the environment so tests and the CLI can be
    deterministic. Mode is inferred when not stated: a self-hosted URL implies
    self-hosted, a key implies customer, and everything else is free.

    A key supplied for free or self-hosted mode is an error rather than being
    quietly dropped. Silently ignoring it would leave a user believing their
    subscription was in use, and silently honouring it would send a credential
    to a host that never asked for one.
    """
    env = os.environ if environ is None else environ

    key_text = api_key if api_key is not None else env.get(API_KEY_ENV, "")
    key_text = str(key_text or "").strip()

    url_text = base_url if base_url is not None \
        else env.get(SELF_HOSTED_URL_ENV, "")
    url_text = str(url_text or "").strip()

    requested = str(
        mode if mode is not None else env.get(MODE_ENV, "") or "").strip()
    requested = requested.lower()
    if requested and requested not in MODES:
        raise OpenMeteoAccessError(
            "unknown Open-Meteo access mode %r; expected one of %s"
            % (requested, ", ".join(MODES)))

    if not requested:
        if url_text:
            requested = MODE_SELF_HOSTED
        elif key_text:
            requested = MODE_CUSTOMER
        else:
            requested = MODE_FREE

    if requested == MODE_SELF_HOSTED:
        if not url_text:
            raise OpenMeteoAccessError(
                "Open-Meteo self-hosted access needs a base URL; set %s"
                % SELF_HOSTED_URL_ENV)
        if key_text:
            raise OpenMeteoAccessError(
                "refusing to forward an Open-Meteo key to a self-hosted "
                "instance; unset %s or switch to customer access"
                % API_KEY_ENV)
        return OpenMeteoAccess(MODE_SELF_HOSTED, _normalise_base_url(url_text))

    if requested == MODE_CUSTOMER:
        # Constructed even without a key so the interface can report "customer
        # mode, key missing" instead of failing at import or silently demoting
        # the user to free access.
        return OpenMeteoAccess(
            MODE_CUSTOMER, CUSTOMER_BASE_URL,
            _Secret(key_text) if key_text else None)

    if key_text:
        raise OpenMeteoAccessError(
            "free Open-Meteo access does not accept a key, and sending one to "
            "%s would leak it; unset %s or set %s=%s"
            % (FREE_HOST, API_KEY_ENV, MODE_ENV, MODE_CUSTOMER))
    return OpenMeteoAccess(MODE_FREE, FREE_BASE_URL)


def build_session(access: OpenMeteoAccess, *, max_workers: int = 1):
    """Return a ``requests.Session`` with bounded retry for idempotent GETs.

    Retries cover transient connection and ``5xx`` faults plus ``429``, which is
    the status Open-Meteo uses for rate limiting. Client errors are never
    retried: a ``400`` means the request contract is wrong and repeating it
    only spends more of the user's allowance.
    """
    import requests
    from requests.adapters import HTTPAdapter

    try:
        from urllib3.util.retry import Retry
    except ImportError:  # pragma: no cover - urllib3 ships with requests
        Retry = None  # type: ignore[assignment]

    session = requests.Session()
    if Retry is not None:
        retries = Retry(
            total=2, connect=2, read=2, backoff_factor=0.3,
            status_forcelist=RETRY_STATUS,
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        pool = max(1, int(max_workers))
        session.mount(
            "https://",
            HTTPAdapter(max_retries=retries, pool_connections=pool,
                        pool_maxsize=pool))
        session.mount(
            "http://",
            HTTPAdapter(max_retries=retries, pool_connections=pool,
                        pool_maxsize=pool))
    session.headers.update({"Accept": "application/json"})
    return session


#: Signature of the injectable transport used by the fetch layer. Tests supply
#: their own so no unit test touches the network.
RequestJson = Callable[..., dict]


def fetch_json(
        access: OpenMeteoAccess,
        params: Mapping[str, Any],
        *,
        session: Any = None,
        timeout: tuple[float, float] | float = DEFAULT_TIMEOUT,
        request_get: Callable[..., Any] | None = None,
) -> dict:
    """Perform one Open-Meteo GET and return the decoded JSON object.

    The credential is attached here and nowhere else. Every failure path routes
    its message through :func:`redact` before raising, because ``requests``
    embeds the full request URL -- credential included -- in several of its own
    exception messages.
    """
    access.require_ready()
    query = access.authenticated_params(params)
    url = access.forecast_url

    owns_session = session is None and request_get is None
    if owns_session:
        session = build_session(access)
    try:
        if request_get is not None:
            response = request_get(url, params=query, timeout=timeout)
        else:
            response = session.get(url, params=query, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - re-raised redacted below
        raise RetrievalError(
            "Open-Meteo request failed: %s: %s"
            % (type(exc).__name__, redact(exc))) from None
    finally:
        if owns_session and session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - closing must not mask a failure
                pass

    return _decode(access, response)


def _decode(access: OpenMeteoAccess, response: Any) -> dict:
    """Validate one response and return its JSON object."""
    status = int(getattr(response, "status_code", 0) or 0)
    reason = getattr(response, "reason", "") or ""

    if status == 429:
        raise OpenMeteoRateLimited(
            "Open-Meteo rate limit reached (429). This allowance is shared "
            "with any other Open-Meteo use from %s."
            % ("this machine" if not access.is_customer else "this key"),
            retry_after=_retry_after(response))
    if status in (401, 403):
        raise OpenMeteoAccessError(
            "Open-Meteo refused the credential (%d %s). Check that %s holds a "
            "current subscription key." % (status, redact(reason), API_KEY_ENV))
    if status >= 400:
        raise RetrievalError(
            "Open-Meteo returned %d %s: %s"
            % (status, redact(reason), redact(_error_reason(response))))

    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            content_type = str(headers.get("Content-Type", "") or "")
        except Exception:  # noqa: BLE001 - header mappings vary
            content_type = ""
    if content_type and "json" not in content_type.lower():
        raise RetrievalError(
            "Open-Meteo returned %s instead of JSON" % redact(content_type))

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RetrievalError(
            "Open-Meteo response was not valid JSON: %s" % redact(exc)) from None
    if not isinstance(payload, dict):
        raise RetrievalError(
            "Open-Meteo response was %s, expected a JSON object"
            % type(payload).__name__)
    # The API reports contract errors in-band with HTTP 200 in some cases.
    if payload.get("error"):
        raise RetrievalError(
            "Open-Meteo rejected the request: %s"
            % redact(payload.get("reason") or "unspecified error"))
    return payload


def _error_reason(response: Any) -> str:
    """Return the provider's own explanation, when it supplies one."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not always JSON
        text = str(getattr(response, "text", "") or "")
        return text[:200]
    if isinstance(body, dict):
        return str(body.get("reason") or body.get("error") or "")[:200]
    return str(body)[:200]


def _retry_after(response: Any) -> float:
    """Return the provider's requested wait in seconds, clamped."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0.0
    try:
        raw = headers.get("Retry-After")
    except Exception:  # noqa: BLE001
        return 0.0
    try:
        return min(300.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


class OpenMeteoRateLimited(RetrievalError):
    """Raised on a provider ``429`` so callers can back off deliberately."""

    def __init__(self, message: str, *, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after))


__all__ = [
    "API_KEY_ENV",
    "CUSTOMER_BASE_URL",
    "CUSTOMER_HOST",
    "DEFAULT_TIMEOUT",
    "FORECAST_PATH",
    "FREE_BASE_URL",
    "FREE_HOST",
    "MODES",
    "MODE_CUSTOMER",
    "MODE_ENV",
    "MODE_FREE",
    "MODE_SELF_HOSTED",
    "REDACTED",
    "SELF_HOSTED_URL_ENV",
    "OpenMeteoAccess",
    "OpenMeteoAccessError",
    "OpenMeteoRateLimited",
    "ParameterRangeError",
    "RetrievalError",
    "build_session",
    "fetch_json",
    "redact",
    "resolve_access",
]
