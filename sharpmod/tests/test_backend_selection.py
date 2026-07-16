"""Backend selection, fallback, and diagnostic behavior."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from sharpmod import __version__ as sharpmod_version
from sharpmod.backends import selector
from sharpmod.backends.protocol import (
    BACKEND_API_VERSION,
    REQUIRED_RUST_CAPABILITIES,
)
from sharpmod.backends.selector import BackendUnavailableError


@pytest.fixture(autouse=True)
def _clean_selector_state(monkeypatch):
    monkeypatch.delenv("SHARPMOD_BACKEND", raising=False)
    selector.reset_backend_cache()
    yield
    selector.reset_backend_cache()


def _fake_rust_module(
    version=sharpmod_version,
    api_version=BACKEND_API_VERSION,
    missing_capabilities=(),
):
    attributes = {
        "__version__": version,
        "__backend_api_version__": api_version,
    }
    for name in REQUIRED_RUST_CAPABILITIES:
        if name not in missing_capabilities:
            attributes[name] = lambda *args, **kwargs: None
    return SimpleNamespace(**attributes)


def test_default_auto_falls_back_when_extension_is_missing(monkeypatch):
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: None)

    backend = selector.get_backend()
    info = selector.backend_info()

    assert backend.name == "python"
    assert info == {
        "requested_backend": "auto",
        "active_backend": "python",
        "rust_installed": False,
        "rust_version": None,
        "fallback_reason": "sharpmod_rs is not installed",
    }


def test_auto_selects_rust_when_extension_imports(monkeypatch):
    module = _fake_rust_module()
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(selector.importlib, "import_module", lambda _name: module)

    assert selector.get_backend().name == "rust"
    assert selector.backend_info() == {
        "requested_backend": "auto",
        "active_backend": "rust",
        "rust_installed": True,
        "rust_version": sharpmod_version,
        "fallback_reason": None,
    }


def test_auto_falls_back_on_binary_import_failure(monkeypatch):
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: object())

    def fail_import(_name):
        raise OSError("dependent DLL was not found")

    monkeypatch.setattr(selector.importlib, "import_module", fail_import)

    assert selector.get_backend().name == "python"
    info = selector.backend_info()
    assert info["rust_installed"] is True
    assert info["fallback_reason"] == (
        "sharpmod_rs failed to import: OSError: dependent DLL was not found")


def test_forced_python_never_imports_rust(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "python")
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: object())

    def unexpected_import(_name):
        raise AssertionError("forced Python mode imported sharpmod_rs")

    monkeypatch.setattr(selector.importlib, "import_module", unexpected_import)

    assert selector.get_backend().name == "python"
    assert selector.backend_info() == {
        "requested_backend": "python",
        "active_backend": "python",
        "rust_installed": True,
        "rust_version": None,
        "fallback_reason": None,
    }


def test_forced_rust_missing_extension_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "rust")
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: None)

    with pytest.raises(
        BackendUnavailableError,
        match=r"SHARPMOD_BACKEND=rust.*sharpmod_rs is not installed",
    ):
        selector.get_backend()


def test_forced_rust_import_failure_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "rust")
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: object())

    def fail_import(_name):
        raise ImportError("wrong architecture")

    monkeypatch.setattr(selector.importlib, "import_module", fail_import)

    with pytest.raises(
        BackendUnavailableError,
        match=r"SHARPMOD_BACKEND=rust.*ImportError: wrong architecture",
    ):
        selector.get_backend()


@pytest.mark.parametrize(
    ("module", "reason"),
    [
        (
            _fake_rust_module(api_version=BACKEND_API_VERSION + 1),
            rf"backend API mismatch: expected {BACKEND_API_VERSION}, "
            rf"found {BACKEND_API_VERSION + 1}",
        ),
        (
            _fake_rust_module(missing_capabilities=("interpolate_1d",)),
            r"missing required callable capabilities: interpolate_1d",
        ),
        (
            _fake_rust_module(version="0.0.0"),
            rf"package version mismatch: expected '{sharpmod_version}', "
            r"found '0.0.0'",
        ),
    ],
)
def test_auto_falls_back_when_rust_contract_is_incompatible(
    monkeypatch, module, reason,
):
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(selector.importlib, "import_module", lambda _name: module)

    assert selector.get_backend().name == "python"
    info = selector.backend_info()
    assert info["rust_installed"] is True
    assert info["rust_version"] == str(module.__version__)
    assert info["fallback_reason"] is not None
    assert re.search(reason, info["fallback_reason"])


@pytest.mark.parametrize(
    ("module", "reason"),
    [
        (
            _fake_rust_module(api_version=None),
            rf"backend API mismatch: expected {BACKEND_API_VERSION}, found None",
        ),
        (
            _fake_rust_module(missing_capabilities=("parse_sounding_rows",)),
            r"missing required callable capabilities: parse_sounding_rows",
        ),
        (
            _fake_rust_module(version="0.3.0"),
            r"package version mismatch",
        ),
    ],
)
def test_forced_rust_rejects_an_incompatible_contract(
    monkeypatch, module, reason,
):
    monkeypatch.setenv("SHARPMOD_BACKEND", "rust")
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(selector.importlib, "import_module", lambda _name: module)

    with pytest.raises(BackendUnavailableError, match=reason):
        selector.get_backend()


def test_invalid_backend_value_is_rejected(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "gpu")

    with pytest.raises(ValueError, match=r"auto, python, rust"):
        selector.get_backend()


def test_environment_value_is_trimmed_and_case_insensitive(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "  PYTHON  ")
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: None)

    assert selector.get_backend().name == "python"
    assert selector.backend_info()["requested_backend"] == "python"


def test_cache_reselects_when_environment_mode_changes(monkeypatch):
    monkeypatch.setattr(selector.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setenv("SHARPMOD_BACKEND", "python")
    python_backend = selector.get_backend()

    monkeypatch.setenv("SHARPMOD_BACKEND", "auto")
    auto_backend = selector.get_backend()

    assert python_backend.name == auto_backend.name == "python"
    assert selector.backend_info()["requested_backend"] == "auto"
