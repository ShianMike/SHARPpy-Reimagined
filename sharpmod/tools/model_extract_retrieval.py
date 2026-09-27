"""Provider-specific dataset retrieval and GRIB-message assembly for point-model
extraction.

It selects source URLs and required companion fields before ``model_extract_run``
decodes the requested point; provider capability checks and run orchestration remain
separate."""

from __future__ import annotations

from contextlib import redirect_stdout
from pathlib import Path
from sharpmod.providers import eccc_geomet
from sharpmod.providers import openmeteo
from sharpmod.providers import rrfs_nomads
from sharpmod.providers.hrrr_zarr import ZarrBackendUnavailable
from sharpmod.providers.hrrr_zarr import fetch_hrrr_zarr_point
from sharpmod.models.model_fields import CFS_SURFACE_SEARCH
from sharpmod.models.model_fields import build_noaa_search
from sharpmod.models.model_fields import supports_noaa_surface_merge
from sharpmod.models.model_sources import SourceRoutingUnavailable
from sharpmod.models.model_sources import download_nomads_subset
from sharpmod.models.model_sources import nomads_supported
from sharpmod.models.model_sources import select_herbie_provider
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.models.model_transport import OptimizedTransportUnavailable
from sharpmod.models.model_transport import _valid_grib
from sharpmod.models.model_transport import download_herbie_subset
from sharpmod.models.model_transport import download_herbie_subset_fallback
from sharpmod.models.model_transport import range_worker_count
from sharpmod.tools.era5_extract import RetrievalError
from sharpmod.tools.era5_extract import _merge_datasets
from sharpmod.tools.era5_extract import _quiet_remove
from types import SimpleNamespace
import io
import os
import shutil
import tempfile
from sharpmod.tools import model_extract as _api


def _combine_grib_payloads(paths, output_path):
    """Atomically concatenate complete GRIB-message streams."""
    paths = tuple(Path(path).resolve(strict=True) for path in paths)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=output.name + ".",
        suffix=".tmp",
        dir=output.parent,
    )
    try:
        with os.fdopen(fd, "wb") as destination:
            for path in paths:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
        if not _valid_grib(Path(temporary)):
            raise RetrievalError("combined model GRIB failed completeness checks")
        os.replace(temporary, output)
    except BaseException:
        _quiet_remove(temporary)
        raise
    return output.resolve()


def _cfs_surface_companion(
    Herbie,
    config,
    run_dt,
    fxx,
    member,
    download_dir,
    cancelled,
):
    """Download CFS ground fields from its separate flux product."""
    kwargs = dict(_api._herbie_kwargs(config, member=member))
    kwargs["kind"] = "flxf"
    companion = _api._create_herbie(
        Herbie,
        run_dt.strftime("%Y-%m-%d %H:%M"),
        model=config.herbie_model,
        product=config.product,
        fxx=int(fxx),
        verbose=False,
        **kwargs,
    )
    if companion.grib is None:
        raise RetrievalError("CFS surface companion product is unavailable")
    inventory = companion.inventory(CFS_SURFACE_SEARCH).copy()
    if not _api._inventory_has_surface_contract(inventory):
        raise RetrievalError(
            "CFS surface companion does not expose the verified ground fields"
        )
    path, transferred = _api.download_herbie_subset(
        companion,
        CFS_SURFACE_SEARCH,
        inventory=inventory,
        save_dir=download_dir,
        cancelled=cancelled,
        workers=range_worker_count(default=4),
    )
    local_path = _api._local_grib_path(path)
    if local_path is None:
        raise RetrievalError("CFS surface companion download is incomplete")
    fields = tuple(dict.fromkeys(
        str(value).upper()
        for value in inventory.get("variable", ())
    ))
    return local_path, int(transferred or 0), str(companion.grib), fields


def _f000_publishes_surface_height(
    Herbie,
    config,
    run_dt,
    member=None,
    *,
    cancelled=None,
):
    """Return whether this run's F000 file carries the invariant terrain height."""
    search, _fields = _api._invariant_height_plan(config)
    try:
        _api._probe_cancel_if_requested(cancelled)
        companion = _api._create_herbie(
            Herbie,
            run_dt.strftime("%Y-%m-%d %H:%M"),
            model=config.herbie_model,
            product=config.product,
            fxx=0,
            verbose=False,
            **_api._herbie_kwargs(config, member=member),
        )
        _api._probe_cancel_if_requested(cancelled)
        if companion.grib is None:
            return False
        available = len(companion.inventory(search)) > 0
        _api._probe_cancel_if_requested(cancelled)
        return available
    except DownloadCancelled:
        raise
    except Exception:
        return False


def _surface_height_companion(
    Herbie,
    config,
    run_dt,
    member,
    download_dir,
    cancelled,
):
    """Download the run's step-0 terrain height for a later forecast hour.

    Terrain height never changes through a run, and ECMWF open data
    (``z:sfc``) and GEFS (``HGT:surface``) publish it only in the F000 file.
    Every later forecast hour needs that one message to complete the verified
    ground row.
    """
    search, fields = _api._invariant_height_plan(config)
    companion = _api._create_herbie(
        Herbie,
        run_dt.strftime("%Y-%m-%d %H:%M"),
        model=config.herbie_model,
        product=config.product,
        fxx=0,
        verbose=False,
        **_api._herbie_kwargs(config, member=member),
    )
    if companion.grib is None:
        raise RetrievalError(
            "%s F000 surface-height companion is unavailable" % config.label
        )
    inventory = companion.inventory(search).copy()
    if len(inventory) == 0:
        raise RetrievalError(
            "%s F000 file does not publish the invariant surface height "
            "needed for a verified ground row" % config.label
        )
    try:
        path, transferred = _api.download_herbie_subset(
            companion,
            search,
            inventory=inventory,
            save_dir=download_dir,
            cancelled=cancelled,
            workers=range_worker_count(default=4),
        )
    except OptimizedTransportUnavailable as exc:
        # A companion must never be the reason a whole sounding fails. Retain
        # the permissive compatibility route without entering Herbie's
        # synchronous, cancellation-blind ``download`` implementation.
        _api._LOGGER.info(
            "model_transport.companion_fallback model=%s run=%s reason=%s",
            config.key, run_dt.isoformat(), exc,
        )
        path, transferred = download_herbie_subset_fallback(
            companion,
            search,
            inventory=inventory,
            save_dir=download_dir,
            cancelled=cancelled,
        )
    local_path = _api._local_grib_path(path)
    if local_path is None:
        raise RetrievalError(
            "%s surface-height companion download is incomplete" % config.label
        )
    return local_path, int(transferred or 0), str(companion.grib), fields


def _retrieve_rrfs_dataset(config, run_dt, fxx, *, download_dir,
                           progress_callback, cancelled):
    """Fetch and combine RRFS pressure and ground GRIB over NOMADS.

    RRFS is the one product this project locates itself rather than through
    Herbie, whose ``rrfs`` template still resolves to a retired AWS prefix and
    whose product list predates the prslev/2dfld split. The native GRIB runtime
    is still required, because the assembled file is decoded the same way as
    every other model's.

    The two products are always fetched together. The ground file is not an
    optional enrichment but the only published source of the verified surface
    row, so a failure there is fatal rather than something to route around --
    unlike the invariant-height companion, which has a working fallback.
    """
    _api.require_runtime_dependencies()
    combined_path = Path(download_dir or Path.cwd()) / (
        f"{config.key}-{run_dt:%Y%m%d%H}-f{int(fxx):03d}"
        "-verified-surface.grib2"
    )
    # A warm hit must not touch the network at all. The component subsets are
    # deleted after they are combined, so re-running the fetch to re-derive the
    # field list would re-transfer up to 350 MB; the sidecar records it instead.
    if combined_path.exists() and _valid_grib(combined_path):
        recorded = rrfs_nomads.read_provenance(combined_path)
        if recorded is not None:
            _api._emit_progress(progress_callback, "cached")
            return _api._LocalGribDataset(combined_path), SimpleNamespace(
                grib=recorded["source_url"].split(";")[0],
                _sharpmod_source_url=recorded["source_url"],
                _sharpmod_fields=recorded["fields"],
                _sharpmod_search=build_noaa_search(
                    recorded["pressure_fields"]
                ),
                _sharpmod_transport=(
                    f"{rrfs_nomads.TRANSPORT}+surface-companion"
                ),
            )

    _api._emit_progress(progress_callback, "downloading")
    try:
        pressure, surface = rrfs_nomads.fetch_pair(
            config.key,
            config.kwargs.get("domain"),
            run_dt,
            int(fxx),
            download_dir=download_dir,
            cancelled=cancelled,
        )
    except DownloadCancelled:
        raise
    except rrfs_nomads.RrfsUnavailable as exc:
        raise RetrievalError(
            "no %s data for run %s F%03d: %s"
            % (config.label, run_dt.isoformat(), int(fxx), exc)
        ) from exc
    except (ValueError, OSError, OptimizedTransportUnavailable) as exc:
        raise RetrievalError(
            "failed to retrieve %s data for %s F%03d: %s"
            % (config.label, run_dt.isoformat(), int(fxx), exc)
        ) from exc

    fields = tuple(dict.fromkeys((*pressure.fields, *surface.fields)))
    if not supports_noaa_surface_merge(fields):
        raise RetrievalError(
            "%s did not publish a complete verified-surface contract "
            "(fetched: %s)" % (config.label, ", ".join(fields))
        )
    # Pressure levels lead so the combined stream matches the message order
    # every other verified-surface product uses.
    combined_path = _api._combine_grib_payloads(
        (pressure.path, surface.path), combined_path
    )
    source_url = f"{pressure.source_url};{surface.source_url}"
    rrfs_nomads.write_provenance(
        combined_path,
        fields=fields,
        source_url=source_url,
        pressure_fields=pressure.fields,
    )
    # The combined stream is the only payload the decoder reads. Keeping the
    # components as well would double a 3-km forecast hour to about 700 MB and
    # let roughly four of them fill the managed cache budget, and it would buy
    # nothing: pruning removes a whole model-hour entry, so a surviving
    # component could never be reused on its own.
    for component in (pressure.path, surface.path):
        if component != combined_path:
            _quiet_remove(component)
    transferred = pressure.transferred_bytes + surface.transferred_bytes
    _api._emit_progress(progress_callback, "decoding", transferred)
    source = SimpleNamespace(
        grib=pressure.source_url,
        _sharpmod_source_url=source_url,
        _sharpmod_fields=fields,
        _sharpmod_search=build_noaa_search(pressure.fields),
        _sharpmod_transport=(
            f"{rrfs_nomads.TRANSPORT}+surface-companion"
        ),
    )
    # A companion-completed payload is always decoded from the local file, the
    # same way the CFS and invariant-height routes are: the combined stream is
    # the only place both halves of the ground contract exist together.
    return _api._LocalGribDataset(combined_path), source


def _retrieve_dataset(config, run_dt, fxx, member=None, download_dir=None,
                      progress_callback=None, cancelled=None, lat=None,
                      lon=None):
    """Fetch a Herbie pressure-level subset for ``config``.

    The point providers are intercepted first. ``extract`` returns early for
    them, but the GUI's model-hour cache calls this helper directly to populate
    a shared entry, so a provider missing from here reaches Herbie and fails
    with ``module 'herbie.models' has no attribute ...`` -- naming a SHARPpy key
    Herbie was never asked about.
    """
    if config.key in _api.POINT_PROVIDER_KEYS:
        if lat is None or lon is None:
            raise RetrievalError(
                "%s uses a point-only provider and requires lat/lon"
                % config.label
            )
        if config.key in _api.ECCC_POINT_KEYS:
            dataset = eccc_geomet.fetch_point(
                config.key,
                float(lat),
                float(lon),
                run_time=run_dt,
                fxx=int(fxx),
                progress_callback=progress_callback,
                cancelled=cancelled,
            )
            capability = eccc_geomet.get_capability(config.key)
            source = SimpleNamespace(
                grib=eccc_geomet.GEOMET_URL,
                _sharpmod_source_url=eccc_geomet.GEOMET_URL,
                _sharpmod_fields=capability.fields,
                _sharpmod_transport="wms-getfeatureinfo-point",
            )
            return dataset, source

        dataset = openmeteo.fetch_point(
            config.key,
            float(lat),
            float(lon),
            run_time=run_dt,
            fxx=int(fxx),
            progress_callback=progress_callback,
            cancelled=cancelled,
        )
        # The dataset already publishes the three ``_sharpmod_*`` attributes the
        # hour cache duck-types on; they are mirrored onto a source object so
        # this returns the same shape as every other branch.
        source = SimpleNamespace(
            grib=dataset._sharpmod_source_url,
            _sharpmod_source_url=dataset._sharpmod_source_url,
            _sharpmod_fields=dataset._sharpmod_fields,
            _sharpmod_transport=dataset._sharpmod_transport,
        )
        return dataset, source

    _api._emit_progress(progress_callback, "locating")
    if config.key in _api.DIRECT_GRIB_KEYS:
        return _api._retrieve_rrfs_dataset(
            config, run_dt, fxx,
            download_dir=download_dir,
            progress_callback=progress_callback,
            cancelled=cancelled,
        )
    if _api.hrrr_zarr_candidate(config, fxx, lat, lon):
        mode = os.environ.get("SHARPMOD_HRRR_BACKEND", "auto").strip().lower()
        _api._emit_progress(progress_callback, "downloading")
        try:
            dataset, source = fetch_hrrr_zarr_point(
                run_dt,
                int(fxx),
                float(lat),
                float(lon),
                cache_dir=download_dir,
                cancelled=cancelled,
            )
            _api._emit_progress(
                progress_callback,
                "decoding",
                getattr(source, "downloaded_bytes", 0),
            )
            return dataset, source
        except ZarrBackendUnavailable as exc:
            if mode == "zarr":
                raise RetrievalError(
                    "forced HRRR Zarr retrieval failed: %s" % exc
                ) from exc
            _api._LOGGER.info(
                "hrrr_zarr.fallback run=%s fxx=%03d reason=%s",
                run_dt.isoformat(), int(fxx), exc,
            )
    _api.require_runtime_dependencies()
    Herbie = _api._load_herbie_class()

    try:  # pragma: no cover - live network / cache path
        H = _api._create_herbie(
            Herbie,
            run_dt.strftime("%Y-%m-%d %H:%M"),
            model=config.herbie_model,
            product=config.product,
            fxx=int(fxx),
            verbose=False,
            **_api._herbie_kwargs(config, member=member),
        )
        if H.grib is None:
            raise RetrievalError(
                "no %s GRIB for run %s F%03d"
                % (config.label, run_dt.isoformat(), int(fxx)))
        # Keep Herbie's xarray wrapper quiet if it consults its download cache.
        # Windows GUI/worker streams can use CP1252, where Herbie's Unicode
        # status glyphs otherwise raise before decoding starts.
        xarray_kwargs = {"remove_grib": False, "verbose": False}
        if download_dir is not None:
            xarray_kwargs["save_dir"] = os.fspath(download_dir)
        search, selected_fields, planned_inventory = _api._planned_model_search(
            H, config)
        contract_complete = _api._inventory_has_surface_contract(planned_inventory)
        missing_surface = () if contract_complete else tuple(
            _api.surface_contract_status(planned_inventory)["missing"]
        )
        # Terrain height is time-invariant, and ECMWF open data and GEFS
        # publish it only in the run's F000 file. When that single field is
        # all that is missing, complete the contract from the analysis file
        # instead of refusing.
        needs_invariant_height = (
            not contract_complete
            and int(fxx) != 0
            and missing_surface == ("surface_height",)
        )
        needs_cfs_surface = not contract_complete and not needs_invariant_height
        if needs_cfs_surface and config.key != "cfs":
            raise RetrievalError(
                "%s currently publishes no complete verified-surface "
                "contract (surface pressure/height, 2-m thermodynamics, and "
                "10-m winds); refusing a pressure-only sounding (missing: %s)"
                % (config.label, ", ".join(missing_surface) or "unknown")
            )
        expected_bytes = _api._subset_download_bytes(planned_inventory)
        _api._emit_progress(progress_callback, "downloading", expected_bytes)
        # Herbie 2026.3.0 unconditionally prints an emoji when it creates its
        # download directory, even with ``verbose=False``.  Capturing stdout
        # keeps that third-party status message from crashing CP1252 Windows
        # GUI and worker processes; retrieval exceptions still propagate.
        transport = None
        local_path = None
        source_url = str(H.grib)
        if lat is not None and lon is not None and selected_fields \
                and _api._point_backends_enabled() and nomads_supported(config) \
                and _api._prefer_nomads_subset(expected_bytes):
            try:
                _path, transferred_bytes, source_url = download_nomads_subset(
                    H,
                    config,
                    search,
                    selected_fields,
                    float(lat),
                    float(lon),
                    save_dir=download_dir,
                    cancelled=cancelled,
                )
                local_path = _api._local_grib_path(_path)
                transport = "nomads-subregion"
                if transferred_bytes:
                    expected_bytes = int(transferred_bytes)
            except SourceRoutingUnavailable as exc:
                _api._LOGGER.info(
                    "model_sources.nomads_fallback model=%s run=%s fxx=%03d "
                    "reason=%s",
                    config.key, run_dt.isoformat(), int(fxx), exc,
                )
        if transport is None:
            if cancelled is not None and cancelled():
                raise DownloadCancelled("forecast-model download cancelled")
            try:
                _api.select_herbie_provider(H)
            except Exception as exc:
                _api._LOGGER.info(
                    "model_sources.provider_fallback model=%s reason=%s",
                    config.key, exc,
                )
            source_url = str(H.grib)
            transport = "optimized-ranges"
            try:
                _path, transferred_bytes = _api.download_herbie_subset(
                    H,
                    search,
                    inventory=planned_inventory,
                    save_dir=download_dir,
                    cancelled=cancelled,
                    workers=range_worker_count(default=4),
                )
                local_path = _api._local_grib_path(_path)
                if transferred_bytes:
                    expected_bytes = int(transferred_bytes)
            except OptimizedTransportUnavailable as exc:
                transport = "compat-ranges"
                _api._LOGGER.info(
                    "model_transport.fallback model=%s run=%s fxx=%03d "
                    "reason=%s",
                    config.key, run_dt.isoformat(), int(fxx), exc,
                )
                downloaded, transferred_bytes = (
                    _api.download_herbie_subset_fallback(
                        H,
                        search,
                        inventory=planned_inventory,
                        save_dir=download_dir,
                        cancelled=cancelled,
                    )
                )
                local_path = _api._local_grib_path(downloaded)
                if transferred_bytes:
                    expected_bytes = int(transferred_bytes)
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")
        if local_path is None:
            try:
                local_path = _api._local_grib_path(H.get_localFilePath(search))
            except Exception:
                pass
        companion_fields = ()
        if needs_cfs_surface:
            (
                companion_path,
                companion_bytes,
                companion_url,
                companion_fields,
            ) = _api._cfs_surface_companion(
                Herbie,
                config,
                run_dt,
                fxx,
                member,
                download_dir,
                cancelled,
            )
            if local_path is None:
                raise RetrievalError("CFS pressure-level download is incomplete")
            combined_name = (
                f"cfs-f{int(fxx):03d}-verified-surface.grib2"
            )
            combined_dir = Path(download_dir or local_path.parent)
            local_path = _api._combine_grib_payloads(
                (local_path, companion_path),
                combined_dir / combined_name,
            )
            expected_bytes += companion_bytes
            source_url = f"{source_url};{companion_url}"
            transport = f"{transport}+surface-companion"
        elif needs_invariant_height:
            (
                companion_path,
                companion_bytes,
                companion_url,
                companion_fields,
            ) = _api._surface_height_companion(
                Herbie,
                config,
                run_dt,
                member,
                download_dir,
                cancelled,
            )
            if local_path is None:
                raise RetrievalError(
                    "%s pressure-level download is incomplete" % config.label
                )
            combined_name = (
                f"{config.key}-{run_dt:%Y%m%d%H}-f{int(fxx):03d}"
                "-verified-surface.grib2"
            )
            combined_dir = Path(download_dir or local_path.parent)
            local_path = _api._combine_grib_payloads(
                (local_path, companion_path),
                combined_dir / combined_name,
            )
            expected_bytes += companion_bytes
            source_url = f"{source_url};{companion_url}"
            transport = f"{transport}+invariant-height-companion"
        H._sharpmod_fields = selected_fields
        if companion_fields:
            H._sharpmod_fields = tuple(dict.fromkeys(
                (*selected_fields, *companion_fields)
            ))
        H._sharpmod_search = search
        H._sharpmod_transport = transport
        H._sharpmod_source_url = source_url

        if local_path is not None and (
            _api._direct_grib_enabled()
            or needs_cfs_surface
            or needs_invariant_height
        ):
            return _api._LocalGribDataset(local_path), H

        _api._emit_progress(progress_callback, "decoding", expected_bytes)
        with redirect_stdout(io.StringIO()):
            ds = H.xarray(search, **xarray_kwargs)
        if isinstance(ds, list):
            ds = _merge_datasets(ds)
        return ds, H
    except (RetrievalError, DownloadCancelled):
        raise
    except Exception as exc:  # pragma: no cover - live failure path
        raise RetrievalError(
            "failed to retrieve %s data for %s F%03d: %s"
            % (config.label, run_dt.isoformat(), int(fxx), exc)) from exc
