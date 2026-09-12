"""Fast regression coverage for lazy portable-sounding decoding."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from sharppy.sharptab import profile as sp_profile

from sharpmod.io.decoder import load_npz
from sharpmod.sharptab.accelerated_profile import (
    AcceleratedConvectiveProfile,
)


SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "soundings"
    / "hrrr_point_36.68N_95.66W_f018.npz"
)


@pytest.mark.skipif(not SAMPLE.exists(), reason="no portable NPZ sample")
def test_npz_decode_defers_convective_upgrade_and_preserves_surface_scalar(
    tmp_path,
):
    """Batch loads stay cheap while first viewer access retains source data."""
    with np.load(SAMPLE, allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    payload["surface_relative_vorticity"] = np.asarray(8.0e-5)
    sounding = tmp_path / "portable_with_vorticity.npz"
    np.savez_compressed(sounding, **payload)

    collection, _station_id = load_npz(sounding)
    raw = next(iter(collection._profs.values()))[0]

    assert not isinstance(raw, sp_profile.ConvectiveProfile)
    assert collection._target_type is AcceleratedConvectiveProfile
    assert raw.surface_relative_vorticity == pytest.approx(8.0e-5)

    upgraded = collection.getHighlightedProf()
    assert isinstance(upgraded, AcceleratedConvectiveProfile)
    assert upgraded.surface_relative_vorticity == pytest.approx(8.0e-5)
