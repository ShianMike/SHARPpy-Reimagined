"""Provider selection and NOMADS geographic-subset routing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import quote, urlencode, urlparse, unquote

from sharpmod.model_transport import DownloadCancelled, _valid_grib


class SourceRoutingUnavailable(RuntimeError):
    """An optional provider/subregion route failed and should be bypassed."""


NOMADS_ENDPOINTS = {
    "hrrr": "filter_hrrr_2d.pl",
    "rap": "filter_rap.pl",
    "nam": "filter_nam.pl",
    "nam-3km-conus": "filter_nam.pl",
    "gfs": "filter_gfs_0p25.pl",
    "gefs": "filter_gefs_atmos_0p50a.pl",
}

_MIRROR_NAMES = {"aws", "google", "azure", "ecmwf"}
_PROVIDER_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_PROVIDER_LOCK = threading.RLock()
_NOMADS_LOCK = threading.RLock()
_LAST_NOMADS_REQUEST = 0.0


def nomads_supported(config) -> bool:
    return str(getattr(config, "key", "")) in NOMADS_ENDPOINTS


def _nomads_directory(source_url: str) -> tuple[str, str]:
    path = unquote(urlparse(source_url).path)
    parts = [part for part in path.split("/") if part]
    date_index = next(
        (
            index for index, part in enumerate(parts)
            if re.match(r"^[a-z0-9_]+\.\d{8}$", part, re.IGNORECASE)
        ),
        None,
    )
    if date_index is None or date_index >= len(parts) - 1:
        raise SourceRoutingUnavailable(
            "NOMADS source URL does not contain a dated model directory"
        )
    return "/" + "/".join(parts[date_index:-1]), parts[-1]


def build_nomads_subset_url(
    config,
    source_url: str,
    lat: float,
    lon: float,
    fields,
    *,
    margin: float = 0.15,
) -> str:
    """Build a throttled GRIB-filter query for a small point neighborhood."""
    key = str(getattr(config, "key", ""))
    endpoint = NOMADS_ENDPOINTS.get(key)
    if endpoint is None:
        raise SourceRoutingUnavailable(
            f"NOMADS geographic subsets are not configured for {key}"
        )
    directory, filename = _nomads_directory(source_url)
    lat = float(lat)
    lon = ((float(lon) + 180.0) % 360.0) - 180.0
    margin = max(0.05, float(margin))
    top = min(90.0, lat + margin)
    bottom = max(-90.0, lat - margin)
    left = max(-180.0, lon - margin)
    right = min(180.0, lon + margin)
    query = [
        ("file", filename),
        ("all_lev", "on"),
        ("subregion", ""),
        ("toplat", f"{top:.4f}"),
        ("leftlon", f"{left:.4f}"),
        ("rightlon", f"{right:.4f}"),
        ("bottomlat", f"{bottom:.4f}"),
        ("dir", directory),
    ]
    for field in dict.fromkeys(str(value).upper() for value in fields):
        query.append((f"var_{field}", "on"))
    return (
        "https://nomads.ncep.noaa.gov/cgi-bin/"
        + quote(endpoint, safe="._-")
        + "?"
        + urlencode(query)
    )


def choose_provider(candidates, probe, *, reference_url=None):
    """Choose the fastest candidate whose content length matches the reference."""
    candidates = dict(candidates)
    if not candidates:
        return None
    reference_name = next(
        (name for name, url in candidates.items() if url == reference_url),
        next(iter(candidates)),
    )
    results = {}
    for name, url in candidates.items():
        try:
            ok, size, elapsed = probe(url)
            if ok and int(size) > 0:
                results[name] = (int(size), float(elapsed))
        except Exception:
            pass
    if not results:
        return reference_name, candidates[reference_name]

    reference = results.get(reference_name)
    if reference is not None:
        expected_size = reference[0]
    else:
        counts = {}
        for size, _elapsed in results.values():
            counts[size] = counts.get(size, 0) + 1
        expected_size = max(counts, key=lambda size: (counts[size], size))
    compatible = [
        (elapsed, name)
        for name, (size, elapsed) in results.items()
        if size == expected_size
    ]
    if not compatible:
        return reference_name, candidates[reference_name]
    _elapsed, selected_name = min(compatible)
    return selected_name, candidates[selected_name]


def _probe_http_range(url: str) -> tuple[bool, int, float]:
    try:
        import requests
        started = time.monotonic()
        with requests.get(
            url,
            headers={"Range": "bytes=0-65535"},
            stream=True,
            timeout=(2.5, 6),
        ) as response:
            if response.status_code != 206:
                return False, 0, 99.0
            value = response.headers.get("Content-Range", "")
            total = int(value.rsplit("/", 1)[1])
            # Consume the small probe so elapsed time includes data transfer.
            for _chunk in response.iter_content(chunk_size=65536):
                break
        return True, total, time.monotonic() - started
    except Exception:
        return False, 0, 99.0


def select_herbie_provider(herbie, *, ttl_seconds: float = 6 * 3600):
    """Select and remember the fastest compatible cloud mirror."""
    if os.environ.get("SHARPMOD_PROVIDER_RACING", "1").strip().lower() \
            in {"0", "false", "no", "off"}:
        return getattr(herbie, "grib_source", None)
    sources = {
        name: str(url)
        for name, url in getattr(herbie, "SOURCES", {}).items()
        if name in _MIRROR_NAMES
        and str(url).startswith(("http://", "https://"))
    }
    if len(sources) < 2:
        return getattr(herbie, "grib_source", None)
    cache_key = (
        str(getattr(herbie, "model", "")),
        str(getattr(herbie, "product", "")),
    )
    now = time.time()
    with _PROVIDER_LOCK:
        cached = _PROVIDER_CACHE.get(cache_key)
    if cached is not None and cached[0] > now and cached[1] in sources:
        selected_name = cached[1]
    else:
        results = {}
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as executor:
            futures = {
                executor.submit(_probe_http_range, url): url
                for url in sources.values()
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        selected_name, _selected_url = choose_provider(
            sources,
            lambda url: results[url],
            reference_url=str(getattr(herbie, "grib", "")),
        )
        with _PROVIDER_LOCK:
            _PROVIDER_CACHE[cache_key] = (now + float(ttl_seconds), selected_name)
    herbie.grib_source = selected_name
    herbie.grib = sources[selected_name]
    return selected_name


def _wait_for_nomads_slot(cancelled=None) -> None:
    global _LAST_NOMADS_REQUEST
    while True:
        with _NOMADS_LOCK:
            remaining = 10.0 - (time.monotonic() - _LAST_NOMADS_REQUEST)
            if remaining <= 0:
                _LAST_NOMADS_REQUEST = time.monotonic()
                return
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")
        time.sleep(min(0.2, remaining))


def download_nomads_subset(
    herbie,
    config,
    search: str,
    fields,
    lat: float,
    lon: float,
    *,
    save_dir=None,
    session=None,
    cancelled=None,
    progress=None,
    throttle: bool = True,
) -> tuple[Path, int, str]:
    """Download one small server-side geographic GRIB subset atomically."""
    source = getattr(herbie, "SOURCES", {}).get("nomads")
    if not source:
        raise SourceRoutingUnavailable("Herbie model has no NOMADS source")
    url = build_nomads_subset_url(config, str(source), lat, lon, fields)
    if save_dir is not None:
        herbie.save_dir = Path(save_dir).expanduser()
    output = Path(herbie.get_localFilePath(search))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and _valid_grib(output):
        return output, output.stat().st_size, url
    if throttle:
        _wait_for_nomads_slot(cancelled)

    owned_session = session is None
    if owned_session:
        import requests
        session = requests.Session()
    fd, temporary = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent
    )
    downloaded = 0
    try:
        os.close(fd)
        with session.get(url, stream=True, timeout=(10, 120)) as response:
            if int(getattr(response, "status_code", 0)) != 200:
                raise SourceRoutingUnavailable(
                    f"NOMADS subset returned HTTP {response.status_code}"
                )
            total = int(response.headers.get("Content-Length", 0) or 0)
            with open(temporary, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled(
                            "forecast-model download cancelled"
                        )
        if not _valid_grib(Path(temporary)):
            raise SourceRoutingUnavailable(
                "NOMADS returned a non-GRIB or incomplete subset"
            )
        os.replace(temporary, output)
        return output, downloaded, url
    except (DownloadCancelled, SourceRoutingUnavailable):
        raise
    except Exception as exc:
        raise SourceRoutingUnavailable(
            "NOMADS geographic subset failed: %s" % exc
        ) from exc
    finally:
        try:
            os.remove(temporary)
        except OSError:
            pass
        if owned_session:
            close = getattr(session, "close", None)
            if callable(close):
                close()
