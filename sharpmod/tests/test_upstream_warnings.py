"""Strict, narrow warning-policy regression tests."""

from __future__ import annotations

import warnings

from sharpmod.upstream_warnings import (
    known_herbie_deprecations,
    known_metpy_bounds_warning,
    known_sharppy_numerical_warnings,
    xarray_new_combine_defaults,
)


def _messages(caught):
    return [str(item.message) for item in caught]


def test_sharppy_guard_suppresses_only_documented_numerical_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with known_sharppy_numerical_warnings():
            warnings.warn_explicit(
                "divide by zero encountered in scalar divide",
                RuntimeWarning,
                "params.py",
                1,
                module="sharppy.sharptab.params",
            )
            warnings.warn("new numerical warning", RuntimeWarning)

    assert _messages(caught) == ["new numerical warning"]


def test_metpy_guard_suppresses_only_exact_bounds_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with known_metpy_bounds_warning():
            warnings.warn(
                "Interpolation point out of data bounds encountered",
                UserWarning,
            )
            warnings.warn("different MetPy warning", UserWarning)

    assert _messages(caught) == ["different MetPy warning"]


def test_herbie_guard_suppresses_only_pinned_deprecations():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with known_herbie_deprecations():
            warnings.warn(
                "Timestamp.utcnow is deprecated and will be removed",
                DeprecationWarning,
            )
            warnings.warn("new Herbie deprecation", DeprecationWarning)

    assert _messages(caught) == ["new Herbie deprecation"]


def test_xarray_context_enables_and_restores_new_combine_defaults():
    import xarray as xr

    option = "use_new_combine_kwarg_defaults"
    if option not in xr.get_options():
        return
    original = xr.get_options()[option]
    with xarray_new_combine_defaults():
        assert xr.get_options()[option] is True
    assert xr.get_options()[option] is original
