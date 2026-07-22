from types import SimpleNamespace

from sharpmod.gui_cache import entry_reusable, format_size, parse_spatial_point


def test_format_size_uses_binary_units():
    assert format_size(0) == "0 B"
    assert format_size(1536) == "1.5 KiB"
    assert format_size(2 * 1024 ** 3) == "2.0 GiB"


def test_parse_spatial_point_accepts_only_coordinate_keys():
    assert parse_spatial_point("35.2500,-97.5000") == (35.25, -97.5)
    assert parse_spatial_point("full-grid") is None
    assert parse_spatial_point("95,0") is None


def test_entry_reusable_accepts_grib_or_portable_sounding():
    assert entry_reusable(SimpleNamespace(valid_grib=True, valid_sounding=False))
    assert entry_reusable(SimpleNamespace(valid_grib=False, valid_sounding=True))
    assert not entry_reusable(
        SimpleNamespace(valid_grib=False, valid_sounding=False)
    )
