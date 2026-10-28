"""Trailing updraft-helicity maxima use the right layer and complete intervals."""

from datetime import datetime, timezone
import re

import numpy as np
import pytest

from sharpmod.bridge import fields
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import hrrr_field as hf, hrrr_products as hp


RUN = datetime(2026, 10, 28, tzinfo=timezone.utc)
UH_MODELS = ("hrrr", "rrfs-a", "nam-3km-conus", *(f"refs-m{i}" for i in range(1, 6)), *hp.HRW_MODELS)


@pytest.mark.parametrize("hours", [1, 3])
def test_two_to_five_km_products_share_the_supplied_colour_table(hours):
    product = hp.PRODUCTS[f"uh-2-5km-{hours}h"]
    assert product.label == f"{hours}-hr Maximum Updraft Helicity: 2-5 km AGL"
    assert product.max_hours == hours
    assert product.fields == (hp.F_UH25,)
    palette = product.palette
    assert palette is hp.PRODUCTS["uh-2-5km-1h"].palette
    assert (palette.minimum, palette.maximum, palette.floor, palette.units) == (0, 400, 10, "m^2/s^2")
    assert palette.stepped
    assert palette.tick_values() == (0, *range(10, 201, 10), *range(220, 401, 20))
    swatches = {10: "#e1e1e1", 49.99: "#696969", 50: "#37536a", 100: "#e9dd96",
                150: "#dc4110", 200: "#73088a", 290: "#da99e7", 300: "#e9bec3",
                390: "#b2445a", 500: "#b2445a"}
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hf.colourize(np.array(list(swatches)), palette), expected)
    assert hf.colourize(np.array([-1, 0, 9.99, 10, np.nan]), palette)[:, 3].tolist() == [0, 0, 0, 255, 0]


@pytest.mark.parametrize("model", UH_MODELS)
def test_catalog_and_request_require_a_complete_same_run_interval(model, monkeypatch):
    def inventory(_url):
        return hf.parse_inventory(
            "1:0:d=2026102800:TMP:2 m above ground:3 hour fcst:\n"
            "2:100:d=2026102800:MXUPHL:5000-2000 m above ground:2-3 hour max fcst:\n")
    monkeypatch.setattr(hf, "fetch_inventory", inventory)
    for hour in (0, 1, 2, 3, 6):
        request = {"model": model, "run_time": RUN.isoformat(), "forecast_hour": hour}
        products = {p["key"] for p in fields.catalog(request)["products"]}
        for hours in (1, 3):
            key = f"uh-2-5km-{hours}h"
            assert (key in products) == (hour >= hours)
            if hour < hours:
                with pytest.raises(BridgeError, match=f"starts at F{hours:03d}"):
                    fields._request({**request, "product": key})
            else:
                assert fields._request({**request, "product": key})[-1] == hour


@pytest.mark.parametrize("model", ["hrrr", "nam-3km-conus", "hrw-wrf-arw"])
@pytest.mark.parametrize("hours", [1, 3])
def test_fetch_uses_exact_hourly_maxima_and_preserves_missing_cells(model, hours, monkeypatch):
    product = next(p for p in hp.available_products(model) if p.key == f"uh-2-5km-{hours}h")
    monkeypatch.setattr(hf, "_INVENTORY_CACHE", {})
    monkeypatch.setattr(hf, "_load_eccodes", lambda: None)
    decoded = {4: [120, 25, np.nan, 10], 5: [80, 175, 10, np.nan], 6: [40, 50, 20, 30]}
    monkeypatch.setattr(hf, "decode_message", lambda data, *_args, **_kwargs:
                        np.array([decoded[int(data.strip())]], dtype=np.float32))
    downloaded = []

    def opener(url, _timeout, _limit, byte_range=None):
        hour = int(re.search(r"(?:wrfsfcf|hiresf|\.f)(\d+)", url)[1])
        if byte_range is None:
            return (
                f"1:0:d=2026102800:MXUPHL:5000-2000 m above ground:0-{hour} hour max fcst:\n"
                f"2:16:d=2026102800:MXUPHL:3000-0 m above ground:{hour - 1}-{hour} hour max fcst:\n"
                f"3:32:d=2026102800:MXUPHL:5000-2000 m above ground:{hour - 1}-{hour} hour max fcst:\n"
                f"4:48:d=2026102800:TMP:2 m above ground:{hour} hour fcst:\n"
            ).encode()
        assert byte_range == (32, 47), "a run maximum or different layer was selected"
        downloaded.append(hour)
        return str(hour).encode().ljust(16)

    result = hf._download_fields(product, RUN, 6, opener=opener, timeout=1,
                                should_cancel=None, model=model)
    assert sorted(downloaded) == list(range(7 - hours, 7))
    np.testing.assert_array_equal(product.derive(result),
                                  [[120, 175, np.nan, np.nan]] if hours == 3 else [decoded[6]])
    with pytest.raises(hf.HrrrFieldUnavailable, match="complete forecast hours"):
        hf._download_fields(product, RUN, hours - 1, opener=opener, timeout=1,
                            should_cancel=None, model=model)


def test_three_hour_maximum_rejects_a_gap_instead_of_returning_partial_data(monkeypatch):
    monkeypatch.setattr(hf, "_load_eccodes", lambda: None)
    monkeypatch.setattr(hf, "_INVENTORY_CACHE", {})
    def opener(url, *_args):
        hour = int(re.search(r"wrfsfcf(\d+)", url)[1])
        if hour == 5:
            raise hf.HrrrFieldUnavailable("missing F005")
        return f"1:0:d=2026102800:MXUPHL:5000-2000 m above ground:{hour - 1}-{hour} hour max fcst:\n".encode()
    with pytest.raises(hf.HrrrFieldUnavailable, match="missing F005"):
        hf._download_fields(hp.PRODUCTS["uh-2-5km-3h"], RUN, 6,
                            opener=opener, timeout=1, should_cancel=None)
