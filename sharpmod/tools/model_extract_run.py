"""End-to-end point extraction after request planning and dataset retrieval.

It prepares a local point neighborhood, merges provider datasets, decodes sounding
columns, and writes the portable artifact consumed by the GUI and CLI workflows."""

from __future__ import annotations

from datetime import timedelta
from sharpmod import backends as _backends
from sharpmod.providers import eccc_geomet
from sharpmod.providers import openmeteo
from sharpmod.export_paths import export_file_path
from sharpmod.providers.hrrr_zarr import HrrrZarrPointDataset
from sharpmod.models.model_surface import SURFACE_CONTRACT_VERSION
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.tools.era5_extract import ParameterRangeError
from sharpmod.tools.era5_extract import RetrievalError
from sharpmod.tools.era5_extract import _LAT_COORDS
from sharpmod.tools.era5_extract import _LEVEL_COORDS
from sharpmod.tools.era5_extract import _LON_COORDS
from sharpmod.tools.era5_extract import _atomic_write_json
from sharpmod.tools.era5_extract import _atomic_write_npz
from sharpmod.tools.era5_extract import _build_columns
from sharpmod.tools.era5_extract import _coord_values
from sharpmod.tools.era5_extract import _horizontal_indexers
from sharpmod.tools.era5_extract import _promote_scalar_level_coordinate
from sharpmod.tools.era5_extract import _quiet_remove
from sharpmod.tools.era5_extract import _require_profile_qc
from sharpmod.tools.era5_extract import _select_time
from sharpmod.tools.era5_extract import select_nearest_grid_point
import numpy as np
import os
from sharpmod.tools import model_extract as _api


def _point_neighborhood(ds, lat, lon, run_dt):
    """Slice one lazy xarray group to at most a 3-by-3 horizontal window."""
    ds_t, _selected_time = _select_time(ds, run_dt)
    _, lats = _coord_values(ds_t, _LAT_COORDS)
    _, lons = _coord_values(ds_t, _LON_COORDS)
    if lats is None or lons is None:
        raise RetrievalError(
            "cfgrib pressure group is missing latitude/longitude coordinates"
        )
    lon_req = lon
    try:
        if np.nanmin(lons) >= 0.0 and lon < 0.0:
            lon_req = lon + 360.0
    except Exception:
        pass
    index_tuple, selected_lat, selected_lon = select_nearest_grid_point(
        lats, lons, lat, lon_req
    )
    indexers, _dims = _horizontal_indexers(ds_t, index_tuple)
    slices = {}
    for dim, index in indexers.items():
        size = int(ds_t.sizes[dim])
        start = max(0, int(index) - 1)
        stop = min(size, int(index) + 2)
        slices[dim] = slice(start, stop)
    compact = ds_t.isel(slices) if slices else ds_t
    selected_lon = ((selected_lon + 180.0) % 360.0) - 180.0
    return compact, float(selected_lat), float(selected_lon)


def _merge_point_datasets(datasets, lat, lon, run_dt):
    """Merge compact lazy point neighborhoods instead of complete grids."""
    try:
        import xarray as xr
    except ImportError as exc:
        raise RetrievalError(
            "forecast model fallback decoding requires xarray"
        ) from exc

    compact = []
    selected = None
    for source in datasets:
        point, selected_lat, selected_lon = _api._point_neighborhood(
            source, lat, lon, run_dt
        )
        if not any(name in point.coords for name in _LEVEL_COORDS) \
                and "z" in point.data_vars:
            point = point.rename({"z": "surface_geopotential"})
        if selected is None:
            selected = (selected_lat, selected_lon)
        else:
            lon_delta = (
                (selected_lon - selected[1] + 180.0) % 360.0
            ) - 180.0
            if (
                abs(selected_lat - selected[0]) > 1.0e-6
                or abs(lon_delta) > 1.0e-6
            ):
                raise RetrievalError(
                    "cfgrib pressure groups use inconsistent horizontal grids"
                )
        compact.append(_promote_scalar_level_coordinate(point))
    if not compact:
        raise RetrievalError("cfgrib returned no pressure-level dataset")
    return xr.merge(compact, compat="override", join="outer")


def _decode_local_point(source, lat, lon):
    """Decode through the selected backend, with Python fallback in auto mode."""
    try:
        decoded = _backends.decode_grib_point(source.path, lat, lon)
    except Exception as rust_error:
        info = _backends.backend_info()
        if not (
            info["requested_backend"] == "auto"
            and info["active_backend"] == "rust"
        ):
            raise
        _api._LOGGER.info(
            "grib_decode.rust_fallback path=%s reason=%s",
            source.path,
            rust_error,
        )
        from sharpmod.backends.python_backend import PythonBackend
        return PythonBackend().decode_grib_point(source.path, lat, lon)
    if decoded.surface_merged:
        return decoded
    # The verified-surface merge is mandatory: without it ``extract`` refuses
    # the profile outright. The native decoder carries its own port of the
    # ground-row predicates, so a row that the shared Python contract accepts
    # -- the saturated-ground clamp in particular -- can still be rejected
    # there. Ask the contract owner for a second opinion before giving up a
    # sounding. This costs one extra point decode only on rows the native
    # decoder already refused, and it is a decode the caller would otherwise
    # never get any value from.
    info = _backends.backend_info()
    if info["active_backend"] != "rust":
        return decoded
    try:
        from sharpmod.backends.python_backend import PythonBackend
        repaired = PythonBackend().decode_grib_point(source.path, lat, lon)
    except Exception as python_error:
        _api._LOGGER.info(
            "grib_decode.surface_repair_failed path=%s reason=%s",
            source.path,
            python_error,
        )
        return decoded
    if not repaired.surface_merged:
        return decoded
    _api._LOGGER.info(
        "grib_decode.surface_repaired path=%s removed=%d",
        source.path,
        repaired.below_ground_levels_removed,
    )
    return repaired


def _xarray_point_columns(ds, lat, lon, run_dt, fxx, label):
    """Return the legacy xarray point result while materializing only columns."""
    ds_t, selected_time = _select_time(ds, run_dt)
    _, lats = _coord_values(ds_t, _LAT_COORDS)
    _, lons = _coord_values(ds_t, _LON_COORDS)
    if lats is None or lons is None:
        raise RetrievalError(
            "%s dataset is missing latitude/longitude coordinates" % label
        )

    lon_req = lon
    try:
        if np.nanmin(lons) >= 0.0 and lon < 0.0:
            lon_req = lon + 360.0
    except Exception:
        pass
    index_tuple, glat, glon = select_nearest_grid_point(
        lats, lons, lat, lon_req
    )
    glon = ((glon + 180.0) % 360.0) - 180.0
    cols, n_levels = _build_columns(ds_t, index_tuple, latitude=glat)
    valid_dt = _api._selected_valid(ds_t, selected_time, run_dt, fxx)
    return cols, n_levels, glat, glon, valid_dt, ds_t


def extract(model, lat, lon, run_time=None, fxx=0, out_path=None, loc=None,
            member=None, dataset=None, download_dir=None,
            source_grib=None, source_fields=None, source_transport=None,
            progress_callback=None, cancelled=None):
    """Extract a public forecast-model point sounding to ``out_path``.

    Parameters mirror the CLI: choose a supported ``model`` key, a latitude and
    longitude, a model run time/cycle, and a forecast hour.
    """
    config = _api._require_selectable(_api.get_config(model))
    lat = float(lat)
    lon = float(lon)
    fxx = int(fxx if fxx is not None else config.default_fxx)
    _api._validate_lat_lon(lat, lon)
    if not _api.point_in_domain(config, lat, lon):
        raise ParameterRangeError(
            "%s covers %s (%s); requested point %.4f, %.4f is outside "
            "that domain" % (
                config.label, config.domain, config.domain_bounds, lat, lon))

    if config.key in _api.OPENMETEO_POINT_KEYS:
        if member is not None:
            raise RetrievalError(
                "%s is deterministic and does not accept --member"
                % config.label
            )
        return openmeteo.extract(
            config.key,
            lat,
            lon,
            run_time=run_time,
            fxx=fxx,
            out_path=out_path,
            loc=loc,
            dataset=dataset,
            progress_callback=progress_callback,
            cancelled=cancelled,
        )

    if config.key in _api.ECCC_POINT_KEYS:
        if member is not None:
            raise RetrievalError(
                "%s is deterministic and does not accept --member"
                % config.label
            )
        return eccc_geomet.extract(
            config.key,
            lat,
            lon,
            run_time=run_time,
            fxx=fxx,
            out_path=out_path,
            loc=loc,
            dataset=dataset,
            progress_callback=progress_callback,
            cancelled=cancelled,
        )

    run_dt = _api._run_datetime(run_time, config)
    if out_path is None:
        out_path = str(
            export_file_path(
                "%s_point_%.2fN_%.2fE_%s_f%03d.npz"
                % (
                    config.key.replace("-", "_"),
                    lat,
                    lon,
                    run_dt.strftime("%Y%m%d%H"),
                    fxx,
                )
            )
        )

    owns_dataset = dataset is None
    if owns_dataset:
        retrieve_kwargs = {
            "member": member,
            "download_dir": download_dir,
            "lat": lat,
            "lon": lon,
        }
        if progress_callback is not None:
            retrieve_kwargs["progress_callback"] = progress_callback
        if cancelled is not None:
            retrieve_kwargs["cancelled"] = cancelled
        ds, H = _api._retrieve_dataset(config, run_dt, fxx, **retrieve_kwargs)
    else:
        ds = dataset
        H = None

    selected_dataset = None
    decoder_backend = "xarray/cfgrib"
    surface_merged = False
    surface_pressure_hpa = None
    below_ground_levels_removed = 0
    try:
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")
        if isinstance(ds, _api.DecodedModelPointDataset):
            _api._emit_progress(progress_callback, "decoding")
            decoder_backend = ds.backend
            decoded = ds.decoded
            cols = decoded.as_dict()
            surface_merged = bool(getattr(decoded, "surface_merged", False))
            below_ground_levels_removed = (
                int(getattr(decoded, "below_ground_levels_removed", 0))
            )
            if decoded.surface_relative_vorticity is not None:
                cols["surface_relative_vorticity"] = (
                    decoded.surface_relative_vorticity
                )
                cols["_surface_vorticity_source"] = ds.vorticity_source
            n_levels = int(decoded.pres.size)
            glat = decoded.selected_lat
            glon = decoded.selected_lon
            valid_dt = ds.valid_time
            _api._emit_progress(progress_callback, "extracting")
        elif isinstance(ds, HrrrZarrPointDataset):
            _api._emit_progress(progress_callback, "decoding")
            decoder_backend = "HRRR Zarr direct point decoder"
            decoded = ds.decoded
            cols = decoded.as_dict()
            surface_merged = bool(getattr(decoded, "surface_merged", False))
            below_ground_levels_removed = (
                int(getattr(decoded, "below_ground_levels_removed", 0))
            )
            if decoded.surface_relative_vorticity is not None:
                cols["surface_relative_vorticity"] = (
                    decoded.surface_relative_vorticity
                )
                cols["_surface_vorticity_source"] = (
                    "absolute-vorticity pressure-level field minus Coriolis"
                )
            n_levels = int(decoded.pres.size)
            glat = decoded.selected_lat
            glon = decoded.selected_lon
            valid_dt = ds.valid_time
            _api._emit_progress(progress_callback, "extracting")
        elif isinstance(ds, _api._LocalGribDataset):
            _api._emit_progress(progress_callback, "decoding")
            decoder_backend = "direct GRIB point decoder"
            try:
                decoded = _api._decode_local_point(ds, lat, lon)
            except Exception as exc:
                if _api._direct_grib_required():
                    raise RetrievalError(
                        "direct GRIB point decoding failed for %s: %s"
                        % (config.label, exc)
                    ) from exc
                _api._LOGGER.info(
                    "grib_decode.xarray_fallback model=%s path=%s reason=%s",
                    config.key,
                    ds.path,
                    exc,
                )
                decoder_backend = "cfgrib/xarray point fallback"
                fallback = ds.fallback_point_dataset(lat, lon, run_dt)
                try:
                    (
                        cols,
                        n_levels,
                        glat,
                        glon,
                        valid_dt,
                        selected_dataset,
                    ) = _api._xarray_point_columns(
                        fallback, lat, lon, run_dt, fxx, config.label
                    )
                finally:
                    fallback.close()
            else:
                # Surface vorticity is an optional enrichment: only NSTP reads
                # it, and no other parameter is affected by its absence. It is
                # resolved here, *after* the decode has succeeded and inside its
                # own guard, because ``surface_wind_vorticity`` raises when it
                # cannot produce a value. Sharing the decode's ``try`` meant
                # that raise discarded a perfectly good profile and fell through
                # to the slower cfgrib/xarray path -- which sets no vorticity at
                # all, so the one parameter the fallback existed to rescue was
                # lost anyway, and every other value came from the slow path for
                # nothing.
                surface_vorticity = decoded.surface_relative_vorticity
                surface_vorticity_source = (
                    "direct pressure-level vorticity field"
                )
                if surface_vorticity is None:
                    if _api._direct_grib_required():
                        raise RetrievalError(
                            "the direct decoder found no usable vorticity value"
                        )
                    try:
                        surface_vorticity = ds.surface_wind_vorticity(
                            lat, lon, run_dt
                        )
                    except Exception as vorticity_exc:
                        # NSTP will report missing; everything else is intact.
                        _api._LOGGER.info(
                            "grib_decode.vorticity_unavailable model=%s "
                            "reason=%s",
                            config.key,
                            vorticity_exc,
                        )
                        surface_vorticity = None
                        surface_vorticity_source = ""
                    else:
                        surface_vorticity_source = (
                            "targeted horizontal wind-gradient fallback"
                        )
                        decoder_backend = (
                            "direct GRIB point decoder + targeted wind stencil"
                        )

                cols = decoded.as_dict()
                surface_merged = bool(
                    getattr(decoded, "surface_merged", False)
                )
                below_ground_levels_removed = (
                    int(getattr(
                        decoded,
                        "below_ground_levels_removed",
                        0,
                    ))
                )
                if surface_vorticity is not None:
                    cols["surface_relative_vorticity"] = (
                        surface_vorticity
                    )
                    cols["_surface_vorticity_source"] = (
                        surface_vorticity_source
                    )
                n_levels = int(decoded.pres.size)
                glat = decoded.selected_lat
                glon = decoded.selected_lon
                valid_dt = run_dt + timedelta(hours=fxx)
            _api._emit_progress(progress_callback, "extracting")
        else:
            _api._emit_progress(progress_callback, "extracting")
            (
                cols,
                n_levels,
                glat,
                glon,
                valid_dt,
                selected_dataset,
            ) = _api._xarray_point_columns(
                ds, lat, lon, run_dt, fxx, config.label
            )
    finally:
        if owns_dataset:
            if isinstance(ds, _api._LocalGribDataset):
                ds.close()
            else:
                try:
                    if selected_dataset is not None:
                        selected_dataset.close()
                finally:
                    if selected_dataset is not ds:
                        ds.close()
    surface_merged = bool(cols.pop("_surface_merged", surface_merged))
    below_ground_levels_removed = int(cols.pop(
        "_below_ground_levels_removed",
        below_ground_levels_removed,
    ))
    surface_pressure_hpa = cols.pop("_surface_pressure_hpa", None)
    if surface_merged and surface_pressure_hpa is None:
        surface_pressure_hpa = float(np.asarray(cols["pres"]).reshape(-1)[0])
    if not surface_merged:
        raise RetrievalError(
            "%s point sounding has no verified surface merge; refusing "
            "a pressure ladder that may contain below-ground levels"
            % config.label
        )
    qc = _require_profile_qc(cols, config.label)
    run_str = run_dt.strftime("%Y-%m-%d %H:%M")
    valid_str = valid_dt.strftime("%Y-%m-%d %H:%M")
    loc_label = loc or "%s %.2f, %.2f" % (config.label, glat, glon)

    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": cols["omeg"], "uwnd": cols["u"], "vwnd": cols["v"],
        "lat": glat, "lon": glon, "loc": loc_label, "model": config.label,
        "run": run_str, "valid": valid_str, "fxx": fxx, "observed": False,
    }
    if "surface_relative_vorticity" in cols:
        arrays["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    meta = {
        "model": config.label,
        "model_key": config.key,
        "loc": loc_label,
        "requested_lat": lat,
        "requested_lon": lon,
        "selected_lat": glat,
        "selected_lon": glon,
        "run": run_str,
        "valid": valid_str,
        "fxx": fxx,
        "observed": False,
        "npz": os.path.abspath(out_path),
        "levels": int(n_levels),
        "herbie_model": config.herbie_model,
        "product": config.product,
        "backend": decoder_backend,
        "decoder": "forecast pressure-level column",
        "surface_merged": surface_merged,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
        "below_ground_levels_removed": below_ground_levels_removed,
        "qc_valid": qc.valid,
        "qc_valid_level_count": qc.valid_level_count,
        "qc_issues": list(qc.issues),
        "cache_hit": False,
    }
    if surface_pressure_hpa is not None:
        meta["surface_pressure_hpa"] = float(surface_pressure_hpa)
    if member is not None:
        meta["member"] = str(member)
    elif "member" in config.kwargs:
        meta["member"] = str(config.kwargs["member"])
    if H is not None:
        source_grib = getattr(
            H, "_sharpmod_source_url", getattr(H, "grib", "")
        )
        source_fields = getattr(H, "_sharpmod_fields", source_fields)
        source_transport = getattr(H, "_sharpmod_transport", source_transport)
    if source_grib:
        meta["source_grib"] = str(source_grib)
    if source_fields:
        meta["fields"] = list(source_fields)
    if source_transport:
        meta["transport"] = str(source_transport)
    if "surface_relative_vorticity" in cols:
        meta["surface_relative_vorticity"] = cols["surface_relative_vorticity"]
        meta["surface_vorticity_source"] = cols.get(
            "_surface_vorticity_source",
            "direct pressure-level vorticity field",
        )

    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model download cancelled")
    _api._emit_progress(progress_callback, "writing")
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
        _quiet_remove(out_path)
        raise
    _api._emit_progress(progress_callback, "complete")
    return out_path
