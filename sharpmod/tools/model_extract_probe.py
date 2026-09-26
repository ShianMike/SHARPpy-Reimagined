"""Bounded live-provider probes for model availability and recent surface-field contracts.

The helpers constrain Herbie requests and report whether the extraction path can
retrieve required fields; they support preflight diagnostics without running a full
point decode."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from sharpmod.providers import eccc_geomet
from sharpmod.providers import openmeteo
from sharpmod.models.model_surface import SURFACE_CONTRACT_VERSION
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.tools.era5_extract import _as_datetime
from sharpmod.tools.era5_extract import _merge_datasets
import threading
import time
from sharpmod.tools import model_extract as _api


class _BoundedHerbieRequests:
    """Thread-scoped timeout/cancellation proxy for Herbie's HTTP calls."""

    def __init__(
        self,
        delegate,
        *,
        owner_thread: int,
        request_timeout: float,
        deadline: float | None,
        cancelled,
    ):
        self._delegate = delegate
        self._owner_thread = int(owner_thread)
        self._request_timeout = max(0.1, float(request_timeout))
        self._deadline = deadline
        self._cancelled = cancelled

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def _owner_call(self) -> bool:
        return threading.get_ident() == self._owner_thread

    def _remaining(self) -> float:
        _api._probe_cancel_if_requested(self._cancelled)
        if self._deadline is None:
            return self._request_timeout
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("forecast-model availability probe timed out")
        return min(self._request_timeout, remaining)

    @staticmethod
    def _clamp_timeout(value, limit: float):
        if value is None:
            return limit
        if isinstance(value, (tuple, list)):
            return tuple(min(max(0.1, float(item)), limit) for item in value)
        return min(max(0.1, float(value)), limit)

    def _request(self, method: str, *args, **kwargs):
        if not self._owner_call():
            return getattr(self._delegate, method)(*args, **kwargs)
        limit = self._remaining()
        kwargs["timeout"] = self._clamp_timeout(kwargs.get("timeout"), limit)
        response = getattr(self._delegate, method)(*args, **kwargs)
        self._remaining()
        return response

    def get(self, *args, **kwargs):
        return self._request("get", *args, **kwargs)

    def head(self, *args, **kwargs):
        return self._request("head", *args, **kwargs)


@contextmanager
def _bounded_herbie_probe_requests(
    *,
    request_timeout: float | None,
    deadline_seconds: float | None,
    cancelled,
    requests_holder=None,
):
    """Bound only this probe thread without changing concurrent downloads."""

    if request_timeout is None and deadline_seconds is None:
        yield
        return
    if requests_holder is None:
        import herbie.core as requests_holder

    timeout = 5.0 if request_timeout is None else float(request_timeout)
    deadline = (
        None
        if deadline_seconds is None
        else time.monotonic() + max(0.1, float(deadline_seconds))
    )
    with _api._HERBIE_PROBE_REQUEST_LOCK:
        original = requests_holder.requests
        proxy = _api._BoundedHerbieRequests(
            original,
            owner_thread=threading.get_ident(),
            request_timeout=timeout,
            deadline=deadline,
            cancelled=cancelled,
        )
        requests_holder.requests = proxy
        try:
            yield
        finally:
            if requests_holder.requests is proxy:
                requests_holder.requests = original


def probe(
    model,
    run_time=None,
    fxx=0,
    member=None,
    open_subset=False,
    *,
    cancelled=None,
    request_timeout: float | None = None,
    deadline_seconds: float | None = None,
):
    """Return a live availability probe dict for one supported model."""
    _api._probe_cancel_if_requested(cancelled)
    config = _api.get_config(model)
    if config.unavailable_reason:
        return {
            "model": config.key,
            "label": config.label,
            "fxx": int(fxx),
            "available": False,
            "subset_opened": False,
            "surface_contract_complete": False,
            "error": "%s cannot produce a sounding: %s"
            % (config.label, config.unavailable_reason),
        }
    if config.key in _api.OPENMETEO_POINT_KEYS:
        if member is not None:
            return {
                "model": config.key,
                "label": config.label,
                "fxx": int(fxx),
                "available": False,
                "subset_opened": False,
                "error": "%s is deterministic and has no members"
                % config.label,
            }
        # Manifest-only. Open-Meteo requests are metered against the user's own
        # allowance, and the interface re-probes whenever a selection changes,
        # so a background availability check here would spend their quota on
        # keystrokes. Ingestion is confirmed when the sounding is fetched.
        return openmeteo.probe(
            config.key, run_time=run_time, fxx=fxx, cancelled=cancelled)

    if config.key in _api.ECCC_POINT_KEYS:
        if member is not None:
            return {
                "model": config.key,
                "label": config.label,
                "fxx": int(fxx),
                "available": False,
                "subset_opened": False,
                "error": "%s is deterministic and has no members"
                % config.label,
            }
        result = eccc_geomet.probe(
            config.key,
            run_time=run_time,
            fxx=fxx,
            cancelled=cancelled,
            request_timeout=request_timeout,
            deadline_seconds=deadline_seconds,
        )
        if open_subset:
            result["note"] = (
                "GeoMet availability is layer-based; point values are "
                "opened during extraction."
            )
        return result
    run_dt = _api._run_datetime(run_time, config)
    result = {
        "model": config.key,
        "label": config.label,
        "run": run_dt.strftime("%Y-%m-%d %H:%M"),
        "fxx": int(fxx),
        "available": False,
        "subset_opened": False,
        "surface_contract_complete": False,
        "surface_contract_present": [],
        "surface_contract_missing": [
            name for name, _expression in _api._SURFACE_CONTRACT_EXPRESSIONS
        ],
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
    }
    try:
        _api._probe_cancel_if_requested(cancelled)
        _api.require_runtime_dependencies()
        Herbie = _api._load_herbie_class()
        request_context = _api._bounded_herbie_probe_requests(
            request_timeout=request_timeout,
            deadline_seconds=deadline_seconds,
            cancelled=cancelled,
        )
        with request_context:
            _api._probe_cancel_if_requested(cancelled)
            H = _api._create_herbie(
                Herbie,
                run_dt.strftime("%Y-%m-%d %H:%M"),
                model=config.herbie_model,
                product=config.product,
                fxx=int(fxx),
                verbose=False,
                **_api._herbie_kwargs(config, member=member),
            )
            _api._probe_cancel_if_requested(cancelled)
            result["grib"] = str(H.grib)
            inv = H.inventory()
            _api._probe_cancel_if_requested(cancelled)
            result["inventory_rows"] = 0 if inv is None else int(len(inv))
            result["available"] = (
                H.grib is not None and inv is not None and len(inv) > 0
            )
            contract = _api.surface_contract_status(inv)
            result["surface_contract_complete"] = contract["complete"]
            result["surface_contract_present"] = list(contract["present"])
            result["surface_contract_missing"] = list(contract["missing"])
            result["surface_contract_version"] = SURFACE_CONTRACT_VERSION
            if (
                not contract["complete"]
                and int(fxx) != 0
                and tuple(contract["missing"]) == ("surface_height",)
                and _api._f000_publishes_surface_height(
                    Herbie,
                    config,
                    run_dt,
                    member=member,
                    cancelled=cancelled,
                )
            ):
                # Extraction completes this from the run's F000 invariant
                # terrain height, so this request remains usable.
                result["surface_contract_invariant_companion"] = True
                result["surface_contract_complete"] = True
                result["surface_contract_present"] = [
                    *contract["present"], "surface_height",
                ]
                result["surface_contract_missing"] = []
            if open_subset and result["available"]:
                _api._probe_cancel_if_requested(cancelled)
                ds = H.xarray(config.search, remove_grib=False)
                _api._probe_cancel_if_requested(cancelled)
                if isinstance(ds, list):
                    ds = _merge_datasets(ds)
                result["subset_opened"] = True
                result["data_vars"] = sorted(str(v) for v in ds.data_vars)
    except DownloadCancelled:
        raise
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


def probe_recent_surface_contract(
    model,
    *,
    reference_time=None,
    fxx=0,
    member=None,
    lookback_cycles=8,
    open_subset=False,
):
    """Probe completed cycles until one live inventory can be classified."""
    config = _api.get_config(model)
    limit = int(lookback_cycles)
    if limit < 1:
        raise ValueError("lookback_cycles must be at least 1")
    anchor = (
        datetime.now(timezone.utc)
        if reference_time is None
        else _as_datetime(reference_time)
    )
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    cursor = anchor.astimezone(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    cycles = set(int(hour) for hour in config.cycles)
    candidates = []
    while len(candidates) < limit:
        if cursor.hour in cycles:
            candidates.append(cursor)
        cursor -= timedelta(hours=1)

    attempts = []
    last = None
    for candidate in candidates:
        last = _api.probe(
            config.key,
            run_time=candidate,
            fxx=fxx,
            member=member,
            open_subset=open_subset,
        )
        attempts.append({
            "run": last.get("run", candidate.strftime("%Y-%m-%d %H:%M")),
            "available": bool(last.get("available")),
            "error": last.get("error"),
        })
        if last.get("available"):
            break
    if last is None:  # pragma: no cover - limit validation prevents this
        raise RuntimeError("no provider cycles were probed")
    last["attempted_runs"] = attempts
    last["lookback_cycles"] = limit
    return last
