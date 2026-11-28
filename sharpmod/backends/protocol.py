"""Shared types for the Rust-primary and portable Python backends.

The protocol contains numerical, row-processing, point-GRIB and migrated
map/weather operations. GUI objects, SHARPpy widgets and profile construction
remain outside this boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .grib import DecodedPoint


# Increment this only when the Python/native calling contract changes in an
# incompatible way.  It is deliberately separate from the package version:
# package versions keep wheels in lockstep, while this value describes the
# shape of the extension API itself.
BACKEND_API_VERSION = 7

# Large batches may use every available CPU; Rust also respects process limits.
# Keep facade and adapter defaults together, including on Python 3.11/3.12.
DEFAULT_BATCH_THREADS = max(1, getattr(os, "process_cpu_count", os.cpu_count)() or 1)
DEFAULT_BATCH_PARALLEL_THRESHOLD = 8

REQUIRED_RUST_CAPABILITIES = (
    "wind_to_components",
    "components_to_wind",
    "interpolate_1d",
    "basic_sounding_qc",
    "pressure_sort_dedup_indices",
    "parse_sounding_rows",
    "profile_kinematics",
    "profile_parcels",
    "profile_convective_parcels",
    "lift_parcel",
    "profile_dcape",
    "profile_thermodynamics",
    "ecape_building_blocks",
    "decode_grib_point",
    "decode_grib_points",
    "GribDownloadPoint",
    "set_grib_inventory_cache_enabled",
    "clear_grib_inventory_cache",
    "grib_inventory_cache_info",
    "profile_batch_analysis",
    "radar_velocity_tilts",
    "radar_sites",
    "clear_weather_cache",
    "weather_alerts",
    "contour_segments",
    "pack_wind_grid",
    "fetch_storm_reports",
    "parse_storm_reports",
    "parse_surface_stations",
    "parse_surface_observations",
    "select_surface_observation",
    "fetch_surface_observations",
    "sample_radar_radials",
    "fetch_model_bytes",
    "fetch_satellite_bytes",
    "fetch_iem_json",
    "iem_sounding_columns",
    "draw_locator_lines",
    "acquire_ensemble_members",
    "parse_ecmwf_inventory",
    "interpolate_height_grid",
    "parcel_maximum_level",
    "parcel_trajectory",
    "profile_thermo",
    "profile_wind_layers",
    "igra_header",
    "igra_headers",
    "igra_level",
    "igra_sounding_rows",
    "igra_dewpoint_from_relative_humidity",
    "match_sars_hail",
    "match_sars_supercell",
    "profile_sars",
    "populate_severe_indices",
    "mmp_linear_predictor",
    "validate_portable_archive",
    "portable_profile_array",
    "portable_scalar",
    "validate_portable_pair",
    "write_portable_pair",
    "open_model_sounding",
    "open_era5_sounding",
    "open_wrf_sounding",
    "wrf_point_columns",
    "fetch_openmeteo_point",
    "fetch_geomet_point",
    "geomet_point_value",
    "fetch_hrrr_zarr_native",
    "hrrr_pressure_plan",
    "hrrr_surface_plan",
    "decode_hrrr_zarr_point",
    "hrrr_zarr_point_dataset",
    "download_herbie_compat",
    "download_nomads_native",
    "apply_sounding_edits",
    "sounding_original_values",
    "sounding_interactive_geometry",
    "choose_noaa_fields",
    "choose_ifs_fields",
    "build_noaa_search",
    "build_ifs_search",
    "supports_noaa_surface_merge",
    "supports_ifs_surface_merge",
    "plan_model_ranges",
    "parallelize_model_range_plan",
    "parse_content_range_details",
    "valid_grib_file",
    "rrfs_domain_for",
    "rrfs_build_url",
    "rrfs_release_candidates",
    "rrfs_remember_release",
    "rrfs_forget_release",
    "rrfs_parse_idx",
    "rrfs_select_records",
    "rrfs_pressure_field_plan",
    "rrfs_write_provenance",
    "rrfs_read_provenance",
    "fast_effective_stp_cin",
    "max_thetae_vertical_velocity",
    "derived_dcp",
    "derived_ncape_ncin",
    "derived_ehi",
    "derived_vgp",
    "derived_hpi",
    "derived_peskov",
    "derived_peskov_profile",
    "derived_lscp",
    "derived_nstp",
    "derived_moshe",
    "classify_hazard_inputs",
    "kuchera_snow_liquid_ratio",
    "column_max_temperature_c",
    "fire_ventilation_rate",
    "box_region_from_corners",
    "box_region_from_bounds",
    "box_region_contains",
    "plan_box_lattice",
    "box_transect_column",
    "box_transect_columns",
    "box_field_statistics",
    "box_envelope_stats",
    "sanitize_profile_keep_mask",
    "clean_column",
    "thin_profile_indices",
    "live_cache_digest",
    "live_cache_ttl",
    "live_cache_read",
    "live_cache_write",
    "frame_cache_read_gzip",
    "frame_cache_compress_gzip",
    "storage_scan_files",
    "batch_safe_identifier",
    "batch_contained_path",
    "batch_file_sha256",
    "batch_json_fingerprint",
    "batch_load_manifest",
    "overlay_rings_from_geometry",
    "overlay_bounds_of",
    "overlay_shape_contains",
    "overlay_looks_like_image",
    "overlay_format_age",
    "overlay_raster_intersects",
    "geo_boundary_draw_order",
    "geo_boundary_width",
    "geo_normalise_label_density",
    "geo_labels_visible_for_span",
    "geo_declutter_labels",
    "geo_km_per_pixel_at_latitude",
    "geo_pick_scale_length",
    "geo_format_scale_length",
    "geo_luminance",
    "geo_contrast_ratio",
    "locator_normalise_bounds",
    "locator_area_to_map_box",
    "locator_centred_bounds",
    "locator_fit_bounds",
    "locator_parse_overlay_spec",
    "locator_enforce_overlay_exclusivity",
    "inspection_haversine_km",
    "inspection_format_field_value",
    "era5_great_circle_distance_km",
    "era5_select_nearest_grid_point_1d",
    "era5_select_nearest_grid_point_2d",
    "era5_dewpoint_from_rh",
    "era5_dewpoint_from_specific_humidity",
    "era5_surface_relative_vorticity",
    "era5_east_west_distance_m",
    "era5_north_south_distance_m",
    "isotherm_pres_and_hght",
    "prepare_vertical_interpolator",
    "NativeVerticalInterpolator",
    "native_six_km_cape_eligible",
    "timeline_forecast_hour_range",
    "timeline_normalize_state",
    "timeline_frame_describe",
    "timeline_coverage_summary",
    "animation_frame_caption",
    "animation_plan_frames",
    "ensemble_validate_ledger",
    "color_parse_rgb",
    "color_contrast_ratio",
    "color_resolve_theme",
    "color_semantic_palette",
    "color_is_missing",
    "color_common_gradient",
    "color_tier_for_param",
    "box_field_ramp_rgb",
    "box_field_normalize",
    "box_field_scale_range",
    "box_field_cell_vertices",
    "hodo_label_for_height",
    "hodo_prepare_height_wind_samples",
    "hodo_layout_height_level_markers",
    "skewt_fit_rect",
    "skewt_surface_label_rect",
    "skewt_layout_surface_labels",
    "skewt_centered_rect",
    "unit_split_value_suffix",
    "openmeteo_redact",
    "openmeteo_host_of",
    "openmeteo_normalise_base_url",
    "openmeteo_credential_scope",
    "openmeteo_resolve_access",
    "openmeteo_retry_after_seconds",
    "openmeteo_namespaced_key",
    "openmeteo_api_model_for_key",
    "openmeteo_is_selectable_api_model",
    "openmeteo_hourly_variables",
    "openmeteo_normalise_longitude",
    "openmeteo_point_in_domain",
    "openmeteo_floor_cycle_epoch",
    "openmeteo_select_time_index",
    "openmeteo_derive_surface_height",
    "openmeteo_weighted_units",
    "box_export_normalize_lon180",
    "box_export_format_number",
    "box_export_cell_steps",
    "box_export_cell_geometry",
    "box_export_csv_rows",
    "box_export_summarize",
    "igra2_resolve_station_index",
    "igra2_select_nearest_header_index",
    "igra2_y2d_candidate_years",
    "era5_grid_surface_relative_vorticity",
    "era5_select_surface_level_index",
    "era5_normalize_surface_pressures_hpa",
    "era5_normalize_surface_scalars",
    "render_bounded_float",
    "render_bounded_int",
    "render_normalise_png_mode",
    "render_map_copy_axis",
    "render_scale_dimensions",
    "render_logical_extent",
    "hour_cache_canonical_key_parts",
    "hour_cache_directory_prefix",
    "ifs_validate_request",
    "ifs_nearest_cycle_hour",
    "hodo_locator_point_valid",
    "hodo_locator_requested_distinct",
    "hodo_locator_normalise_box",
    "hodo_locator_zoom_bounds",
    "hodo_locator_raster_source_rect",
    "hodo_locator_raster_draw_rects",
    "hodo_locator_decode_county_tile",
    "hodo_locator_county_tile_coords",
    "hodo_locator_filter_global_lines",
    "hodo_locator_inset_rect",
    "hodo_locator_marker_position",
    "hodo_overlay_raster_copy_range",
    "hodo_overlay_clamp_opacity",
    "hodo_overlay_project_shape_rings",
    "hodo_overlay_project_marker_point",
    "hodo_overlay_timing_text",
    "hodo_overlay_strip_coordinate_label",
    "skew_hgz_band_pixels",
    "skew_interp_env_on_parcel",
    "skew_cape_fill_polygons",
    "inset_thetae_readout",
    "inset_srwinds_readout",
    "inset_slinky_nearest_height",
    "inset_readout_box_rect",
    "board_format_i0",
    "board_format_f1",
    "board_format_f2",
    "board_format_dirspd",
    "board_format_uv_dirspd",
    "board_format_temp",
    "board_format_pwat",
    "board_barb_components",
    "board_cinh_legacy_tier",
    "board_cape3_tier",
    "ship_scale_fraction",
    "ship_format_value",
    "custom_panel_normalise_unit",
    "custom_panel_format_number",
    "export_compose_layout",
    "export_composition_theme_hex",
    "export_valid_png_file",
    "patch_registry_validate_specs",
    "patch_registry_validate_version",
    "box_extract_sequence_hours",
    "observed_safe_token",
    "profile_clamp_scalar_in_range",
    "profile_clamp_vector_in_range",
    "progress_validate_id",
    "progress_stage_phase",
    "progress_window_snapshot",
    "bridge_validate_param_keys",
    "bridge_validate_text",
    "bridge_validate_number",
    "bridge_validate_integer",
    "bridge_validate_choice",
    "backend_validate_sfc_index",
    "spc_cig_level",
    "spc_is_qualifier_label",
    "spc_convective_day_start_epoch",
    "spc_outlook_day_offset",
    "spc_format_product_days",
    "spc_covers_location",
    "spc_candidates_for",
    "spc_parse_stamp_epoch",
    "spc_feature_style",
    "spc_display_label",
    "spc_validate_colour",
    "report_magnitude_text",
    "report_is_significant",
    "report_normalise_filter",
    "report_window_label",
    "report_count_label",
    "report_marker_radius",
    "report_marker_ring",
    "report_normalise_view",
    "report_build_url",
    "report_cache_key",
    "report_window_is_settled",
    "report_layer_shape_matches",
    "geomet_to_rotated",
    "geomet_to_geographic",
    "geomet_rotated_contains",
    "geomet_rotated_outline",
    "geomet_point_in_bounds",
    "geomet_floor_cycle_epoch",
    "geomet_feature_info_bbox",
    "geomet_parse_dimension_defaults",
    "radar_select_scan_epoch",
    "radar_mosaic_covers",
    "radar_mosaic_frame_size",
    "radar_service_exception_text",
    "radar_site_bounds",
    "radar_site_distance_km",
    "radar_site_nearest_index",
    "radar_site_frame_size",
    "surface_wind_state",
    "surface_wind_uv_kt",
    "surface_declutter_indices",
    "surface_station_ring",
    "surface_ring_radii",
    "surface_format_observation_text",
    "surface_format_subtitle",
    "surface_write_csv",
    "overlay_marker_hits_select",
    "overlay_format_description",
    "overlay_marker_hit_label",
    "place_needs_town_name",
    "place_from_address_fields",
    "place_is_us_address",
    "place_parse_record_fields",
    "place_area_proxy_radius_km",
    "place_point_in_polygon",
    "uwyo_pressure_spaced_indices",
    "uwyo_subsample_indices",
    "uwyo_looks_unavailable",
    "uwyo_parse_metadata",
    "uwyo_clean_station_name",
    "uwyo_search_stations",
    "uwyo_resolve_station_index",
    "box_mean_model_label",
    "box_mean_badge_lines",
    "box_mean_describe",
    "box_mean_reference_member_index",
    "box_mean_interpolate_log_pressure",
    "box_mean_nanmean_stack",
    "box_mean_speed_direction",
    "box_mean_strictly_stacked_mask",
    "box_mean_circular_centre",
    "threshold_validate_condition",
    "threshold_validate_definition",
    "threshold_derive_member_state",
    "threshold_evaluate_member_conditions",
    "threshold_result_counts_reconcile",
    "threshold_validate_resume_members",
    "bridge_decode_request_frame",
    "bridge_check_request_params",
    "bridge_encode_frame_check",
    "bridge_validate_rfc3339_time",
    "bridge_format_iso_utc",
    "model_longitude_segments",
    "model_domain_rect_intersects",
    "model_domain_rect_contains",
    "model_outline_sample_points",
    "model_outline_vertex_in_bounds",
    "model_filter_forecast_hours",
    "sharptab_lapse_rate_from_bounds",
    "sharptab_lapse_rate_agl",
    "sharptab_lapse_rate_pres",
    "export_validate_filename",
    "export_probe_writable_dir",
)


@dataclass(frozen=True)
class QualityControlResult:
    """Deterministic result returned by basic sounding-profile QC.

    ``valid_level_count`` counts rows with usable pressure and height.  Missing
    thermodynamic or wind values do not remove an otherwise structural level.
    """

    valid: bool
    valid_level_count: int
    issues: tuple[str, ...]


@dataclass(frozen=True)
class KinematicLayer:
    """One surface-to-height layer from a profile kinematics workspace.

    Missing quantities are represented by ``NaN`` inside the backend contract.
    The SharpTab adapter converts those values to its masked ``MISSING``
    sentinel when exposing existing public functions.
    """

    top_agl: float
    top_pressure: float
    pressure_shear_u: float
    pressure_shear_v: float
    height_shear_u: float
    height_shear_v: float
    mean_u: float
    mean_v: float
    mean_npw_u: float
    mean_npw_v: float
    srh_total: float
    srh_positive: float
    srh_negative: float
    storm_relative_mean_u: float
    storm_relative_mean_v: float


@dataclass(frozen=True)
class ProfileKinematics:
    """Coarse-grained surface-layer diagnostics computed in one backend call."""

    storm_motion: tuple[float, float, float, float]
    layers: tuple[KinematicLayer, ...]

    def layer(self, top_agl: float, *, tolerance: float = 1.0e-6):
        """Return the requested AGL layer or ``None`` when it was not computed."""
        target = float(top_agl)
        for layer in self.layers:
            if abs(layer.top_agl - target) <= tolerance:
                return layer
        return None


@dataclass(frozen=True)
class ParcelDiagnostics:
    """One undiluted parcel ascent from the thermodynamic workspace."""

    start_pressure: float
    start_height: float
    start_temperature: float
    start_dewpoint: float
    lcl_pressure: float
    lcl_height: float
    lfc_pressure: float
    lfc_height: float
    el_pressure: float
    el_height: float
    cape: float
    cin: float
    cape_3km: float
    cape_6km: float


@dataclass(frozen=True)
class ParcelWorkspace:
    """Surface, most-unstable, and 100-hPa mixed-layer parcel results."""

    surface: ParcelDiagnostics
    most_unstable: ParcelDiagnostics
    mixed_layer: ParcelDiagnostics

    def parcel(self, kind: str) -> ParcelDiagnostics:
        """Return a parcel by its short or descriptive name."""
        key = str(kind).strip().lower().replace("-", "_")
        mapping = {
            "sb": self.surface,
            "surface": self.surface,
            "mu": self.most_unstable,
            "most_unstable": self.most_unstable,
            "ml": self.mixed_layer,
            "mixed_layer": self.mixed_layer,
        }
        try:
            return mapping[key]
        except KeyError as exc:
            raise KeyError(f"unknown parcel kind {kind!r}") from exc


@dataclass(frozen=True)
class ParcelTrace:
    """Pressure and temperature coordinates for one parcel path."""

    pressure: tuple[float, ...]
    temperature: tuple[float, ...]


@dataclass(frozen=True)
class ParcelAscent:
    """Parcel diagnostics plus its virtual-temperature plotting trace."""

    diagnostics: ParcelDiagnostics
    trace: ParcelTrace


@dataclass(frozen=True)
class ConvectiveParcelWorkspace:
    """Standard full-profile parcels and effective-inflow-layer bounds."""

    surface: ParcelAscent
    forecast: ParcelAscent
    most_unstable: ParcelAscent
    mixed_layer: ParcelAscent
    effective: ParcelAscent
    effective_bottom_pressure: float
    effective_top_pressure: float

    def parcel(self, kind: str) -> ParcelAscent:
        """Return a parcel ascent by its short or descriptive name."""
        key = str(kind).strip().lower().replace("-", "_")
        mapping = {
            "sb": self.surface,
            "surface": self.surface,
            "fcst": self.forecast,
            "forecast": self.forecast,
            "mu": self.most_unstable,
            "most_unstable": self.most_unstable,
            "ml": self.mixed_layer,
            "mixed_layer": self.mixed_layer,
            "eff": self.effective,
            "effective": self.effective,
        }
        try:
            return mapping[key]
        except KeyError as exc:
            raise KeyError(f"unknown parcel kind {kind!r}") from exc


@dataclass(frozen=True)
class DowndraftDiagnostics:
    """DCAPE, source level, downrush temperature, and parcel trace."""

    cape: float
    source_pressure: float
    downrush_temperature: float
    trace: ParcelTrace


@dataclass(frozen=True)
class ProfileThermodynamics:
    """Parcel, convective, and downdraft results from one preparation."""

    parcels: ParcelWorkspace
    convective: ConvectiveParcelWorkspace
    downdraft: DowndraftDiagnostics


@dataclass(frozen=True)
class BatchProfileAnalysis:
    """Dense fixed-width diagnostics for an ordered profile batch."""

    parcels: object
    convective_parcels: object
    effective_bounds: object
    downdraft: object
    storm_motion: object
    kinematic_layers: object
    execution_mode: str
    worker_count: int


@runtime_checkable
class Backend(Protocol):
    """Operations implemented by both the Python and Rust backends."""

    name: str

    def wind_to_components(self, direction, speed, *, missing=None):
        """Convert meteorological direction/speed to unit-preserving ``u/v``."""

    def components_to_wind(self, u, v, *, missing=None):
        """Convert unit-preserving ``u/v`` to direction/speed."""

    def interpolate_1d(
        self, target, coordinate, values, *, missing=None, log=False,
    ):
        """Interpolate values at one or more targets without extrapolation."""

    def basic_sounding_qc(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        wdir,
        wspd,
        *,
        missing=-9999.0,
    ) -> QualityControlResult:
        """Run the pinned basic sounding-profile checks."""

    def pressure_sort_dedup_indices(self, pressure, *, missing=-9999.0):
        """Return stable descending indices with invalid/duplicate pressure removed."""

    def parse_sounding_rows(self, text: str, *, missing=-9999.0):
        """Parse the simple six-column sounding-row representation."""

    def profile_kinematics(
        self,
        pres,
        hght,
        u,
        v,
        layer_tops_agl,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ProfileKinematics:
        """Compute shared surface-layer wind diagnostics in one backend call."""

    def profile_parcels(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ParcelWorkspace:
        """Compute cached SB/MU/ML parcel diagnostics in one backend call."""

    def profile_convective_parcels(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ConvectiveParcelWorkspace:
        """Compute standard parcel summaries, traces, and effective bounds."""

    def lift_parcel(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        parcel_pressure,
        parcel_temperature,
        parcel_dewpoint,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ParcelAscent:
        """Lift one explicitly defined parcel and return its trace."""

    def profile_dcape(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> DowndraftDiagnostics:
        """Compute DCAPE and its descending parcel trace."""

    def profile_thermodynamics(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ProfileThermodynamics:
        """Compute all parcel and downdraft workspaces in one preparation."""

    def profile_batch_analysis(
        self,
        profiles,
        layer_tops_agl,
        *,
        missing=-9999.0,
        max_threads=DEFAULT_BATCH_THREADS,
        parallel_threshold=DEFAULT_BATCH_PARALLEL_THRESHOLD,
    ) -> BatchProfileAnalysis:
        """Compute ordered fixed-width diagnostics for complete profiles."""

    def decode_grib_point(
        self, path, lat, lon, *, missing=-9999.0,
    ) -> "DecodedPoint":
        """Decode one pressure-level GRIB column at the nearest grid point."""

    def decode_grib_points(
        self, path, points, *, missing=-9999.0,
    ) -> tuple["DecodedPoint", ...]:
        """Decode several nearest-grid-point columns in request order."""
