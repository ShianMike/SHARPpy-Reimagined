"""Array normalization shared by Python and Rust backend adapters."""

from __future__ import annotations

import numpy as np
import numpy.ma as ma


def _is_scalar_shape(shape) -> bool:
    return tuple(shape) == ()


def prepare_broadcast_pair(left, right, *, missing=None):
    """Broadcast two numeric inputs and represent all missing values as NaN."""
    left_ma = ma.asanyarray(left, dtype=float)
    right_ma = ma.asanyarray(right, dtype=float)
    try:
        shape = np.broadcast_shapes(left_ma.shape, right_ma.shape)
    except ValueError as exc:
        raise ValueError(
            f"inputs could not be broadcast together: "
            f"{left_ma.shape} and {right_ma.shape}"
        ) from exc

    left_data = np.broadcast_to(
        np.asarray(left_ma.filled(np.nan), dtype=np.float64), shape)
    right_data = np.broadcast_to(
        np.asarray(right_ma.filled(np.nan), dtype=np.float64), shape)
    left_mask = np.broadcast_to(ma.getmaskarray(left_ma), shape)
    right_mask = np.broadcast_to(ma.getmaskarray(right_ma), shape)
    invalid = (
        left_mask | right_mask
        | ~np.isfinite(left_data) | ~np.isfinite(right_data)
    )
    if missing is not None:
        invalid = invalid | (left_data == missing) | (right_data == missing)

    if np.any(invalid):
        left_out = np.array(left_data, dtype=np.float64, copy=True, order="C")
        right_out = np.array(right_data, dtype=np.float64, copy=True, order="C")
        left_out[invalid] = np.nan
        right_out[invalid] = np.nan
    else:
        # Preserve a zero-copy read-only view for the common case where an
        # already-contiguous float64 array needs no missing-value rewrite.
        left_out = np.ascontiguousarray(left_data, dtype=np.float64)
        right_out = np.ascontiguousarray(right_data, dtype=np.float64)
    return left_out.reshape(-1), right_out.reshape(-1), shape


def restore_array(values, shape):
    """Restore a flat native result to the public scalar/masked-array form."""
    array = np.asarray(values, dtype=np.float64).reshape(shape)
    if _is_scalar_shape(shape):
        value = float(array[()])
        return ma.masked if np.isnan(value) else value
    # Backend kernels already return fresh owned arrays.  Build only the mask;
    # copying their data again roughly doubles the Python/Rust boundary cost
    # for ordinary profiles.
    return ma.masked_invalid(array, copy=False)


def restore_pair(left, right, shape):
    return restore_array(left, shape), restore_array(right, shape)


def prepare_interpolation(target, coordinate, values, *, missing=None):
    """Normalize interpolation inputs while preserving legacy target behavior."""
    coordinate_ma = ma.asanyarray(coordinate, dtype=float)
    values_ma = ma.asanyarray(values, dtype=float)
    if coordinate_ma.shape != values_ma.shape:
        raise ValueError(
            "coordinate and values must have the same shape; "
            f"got {coordinate_ma.shape} and {values_ma.shape}")

    coordinate_data = np.asarray(
        coordinate_ma.filled(np.nan), dtype=np.float64).reshape(-1)
    values_data = np.asarray(
        values_ma.filled(np.nan), dtype=np.float64).reshape(-1)
    invalid = (
        ma.getmaskarray(coordinate_ma).reshape(-1)
        | ma.getmaskarray(values_ma).reshape(-1)
        | ~np.isfinite(coordinate_data)
        | ~np.isfinite(values_data)
    )
    if missing is not None:
        invalid = (
            invalid
            | (coordinate_data == missing)
            | (values_data == missing)
        )
    if np.any(invalid):
        coordinate_data = np.array(coordinate_data, copy=True, order="C")
        values_data = np.array(values_data, copy=True, order="C")
        coordinate_data[invalid] = np.nan
        values_data[invalid] = np.nan
    else:
        coordinate_data = np.ascontiguousarray(coordinate_data)
        values_data = np.ascontiguousarray(values_data)

    # The existing interpolation code ignores a MaskedArray target's mask and
    # uses its underlying data. Preserve that compatibility quirk here.
    target_ma = ma.asanyarray(target, dtype=float)
    target_shape = target_ma.shape
    target_data = np.asarray(
        ma.getdata(target_ma), dtype=np.float64).reshape(-1)
    target_data = np.ascontiguousarray(target_data)
    if missing is not None:
        target_data = np.array(target_data, copy=True)
        target_data[target_data == missing] = np.nan
    return target_data, coordinate_data, values_data, target_shape


def prepare_1d(value, *, name: str):
    """Return a contiguous float64 vector with masks represented by NaN."""
    array = ma.asanyarray(value, dtype=float)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional; got shape {array.shape}")
    return np.ascontiguousarray(
        np.asarray(array.filled(np.nan), dtype=np.float64))


def prepare_qc_columns(columns, names):
    arrays = tuple(
        prepare_1d(value, name=name)
        for value, name in zip(columns, names)
    )
    lengths = {array.size for array in arrays}
    if len(lengths) > 1:
        details = ", ".join(
            f"{name}={array.size}" for name, array in zip(names, arrays))
        raise ValueError(f"sounding columns must have the same length; {details}")
    return arrays


def missing_mask(array, missing):
    mask = ~np.isfinite(array)
    if missing is not None:
        mask = mask | (array == missing)
    return mask
