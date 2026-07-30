"""Narrow compatibility guards for warnings owned by scientific dependencies.

These contexts intentionally match exact upstream warning messages. They keep
known edge-case noise from leaking through SHARPpy Reimagined while allowing
new or differently worded warnings to remain visible and fail strict tests.
"""

from __future__ import annotations

from contextlib import contextmanager
import warnings


_SHARPPY_NUMERICAL_WARNINGS = (
    (
        r"divide by zero encountered in scalar divide",
        r"sharppy\.sharptab\.(?:params|winds)",
    ),
    (
        r"invalid value encountered in scalar multiply",
        r"sharppy\.sharptab\.winds",
    ),
    (
        r"divide by zero encountered in log10",
        r"sharppy\.sharptab\.thermo",
    ),
    (
        r"invalid value encountered in divide",
        r"numpy\.ma\.extras",
    ),
)


@contextmanager
def known_sharppy_numerical_warnings():
    """Contain understood SHARPpy/NumPy edge warnings around oracle calls."""
    with warnings.catch_warnings():
        for message, module in _SHARPPY_NUMERICAL_WARNINGS:
            warnings.filterwarnings(
                "ignore",
                message=message,
                category=RuntimeWarning,
                module=module,
            )
        yield


@contextmanager
def known_metpy_bounds_warning():
    """Contain MetPy's expected out-of-bounds parcel interpolation warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Interpolation point out of data bounds encountered",
            category=UserWarning,
        )
        yield


@contextmanager
def known_herbie_deprecations():
    """Contain deprecations fixed upstream but not yet in the pinned Herbie."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                r"Herbie: Custom model templates in the config path is "
                r"deprecated\..*"
            ),
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r"Timestamp\.utcnow is deprecated.*",
            category=DeprecationWarning,
        )
        yield


@contextmanager
def known_netcdf4_numpy_shape_deprecation():
    """Contain netCDF4's NumPy 2.5 shape assignment in fixture writes."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                r"Setting the shape on a NumPy array has been deprecated "
                r"in NumPy 2\.5\."
            ),
            category=DeprecationWarning,
        )
        yield


@contextmanager
def xarray_new_combine_defaults():
    """Use xarray's future merge defaults while cfgrib catches up."""
    import xarray as xr

    option = "use_new_combine_kwarg_defaults"
    if option in xr.get_options():
        with xr.set_options(**{option: True}):
            yield
        return

    # Compatibility for the oldest xarray accepted by non-GRIB extras. This
    # exact warning is the only one hidden; all other FutureWarnings escape.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                r"In a future version of xarray the default value for compat "
                r"will change.*"
            ),
            category=FutureWarning,
            module=r"cfgrib\.xarray_store",
        )
        yield
