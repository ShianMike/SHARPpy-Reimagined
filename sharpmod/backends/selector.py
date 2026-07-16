"""Single authority for Rust-primary backend selection and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import os
from threading import RLock

from sharpmod._version import __version__ as SHARPMOD_VERSION

from .protocol import BACKEND_API_VERSION, REQUIRED_RUST_CAPABILITIES
from .python_backend import PythonBackend
from .rust_backend import RustBackend


BACKEND_ENV = "SHARPMOD_BACKEND"
_VALID_BACKENDS = ("auto", "python", "rust")


class BackendUnavailableError(RuntimeError):
    """Raised when forced Rust mode cannot load the native extension."""


@dataclass(frozen=True)
class _Selection:
    requested_backend: str
    backend: object
    rust_installed: bool
    rust_version: str | None
    fallback_reason: str | None


_LOCK = RLock()
_SELECTION: _Selection | None = None


def _requested_backend() -> str:
    value = os.environ.get(BACKEND_ENV, "auto").strip().lower()
    if value not in _VALID_BACKENDS:
        choices = ", ".join(_VALID_BACKENDS)
        raise ValueError(
            f"invalid {BACKEND_ENV} value {value!r}; expected one of: {choices}")
    return value


def _rust_is_installed() -> bool:
    try:
        return importlib.util.find_spec("sharpmod_rs") is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _import_rust():
    try:
        return importlib.import_module("sharpmod_rs"), None
    except Exception as exc:
        reason = f"sharpmod_rs failed to import: {type(exc).__name__}: {exc}"
        return None, reason


def _validate_rust_contract(module) -> tuple[str | None, str | None]:
    """Return the extension version and any compatibility failure reason."""
    raw_version = getattr(module, "__version__", None)
    version = None if raw_version is None else str(raw_version)
    if version is None:
        return None, "sharpmod_rs does not report __version__"
    if version != SHARPMOD_VERSION:
        return version, (
            "sharpmod_rs package version mismatch: "
            f"expected {SHARPMOD_VERSION!r}, found {version!r}"
        )

    api_version = getattr(module, "__backend_api_version__", None)
    if type(api_version) is not int or api_version != BACKEND_API_VERSION:
        return version, (
            "sharpmod_rs backend API mismatch: "
            f"expected {BACKEND_API_VERSION}, found {api_version!r}"
        )

    missing = tuple(
        name for name in REQUIRED_RUST_CAPABILITIES
        if not callable(getattr(module, name, None))
    )
    if missing:
        return version, (
            "sharpmod_rs is missing required callable capabilities: "
            + ", ".join(missing)
        )
    return version, None


def _incompatible_selection(requested, installed, version, reason):
    if requested == "rust":
        raise BackendUnavailableError(
            f"{BACKEND_ENV}=rust requires a compatible sharpmod_rs extension; "
            f"{reason}"
        )
    return _Selection(
        requested, PythonBackend(), installed, version, reason)


def _select(requested: str) -> _Selection:
    installed = _rust_is_installed()
    if requested == "python":
        return _Selection(requested, PythonBackend(), installed, None, None)

    if not installed:
        reason = "sharpmod_rs is not installed"
        if requested == "rust":
            raise BackendUnavailableError(
                f"{BACKEND_ENV}=rust requires sharpmod_rs, but {reason}")
        return _Selection(requested, PythonBackend(), False, None, reason)

    module, reason = _import_rust()
    if module is None:
        return _incompatible_selection(
            requested, True, None, reason)

    version, reason = _validate_rust_contract(module)
    if reason is not None:
        return _incompatible_selection(
            requested, True, version, reason)
    return _Selection(requested, RustBackend(module), True, version, None)


def _selection() -> _Selection:
    global _SELECTION
    requested = _requested_backend()
    with _LOCK:
        if _SELECTION is None or _SELECTION.requested_backend != requested:
            _SELECTION = _select(requested)
        return _SELECTION


def get_backend():
    """Return the active backend selected by ``SHARPMOD_BACKEND``."""
    return _selection().backend


def backend_info() -> dict:
    """Return stable, serializable diagnostic information about selection."""
    selected = _selection()
    return {
        "requested_backend": selected.requested_backend,
        "active_backend": selected.backend.name,
        "rust_installed": selected.rust_installed,
        "rust_version": selected.rust_version,
        "fallback_reason": selected.fallback_reason,
    }


def reset_backend_cache():
    """Clear cached selection (primarily for tests and embedding applications)."""
    global _SELECTION
    with _LOCK:
        _SELECTION = None
