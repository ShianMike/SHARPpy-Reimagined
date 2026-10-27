//! Map data commands: the map catalogue, field and radar frames, single-site
//! radar, SPC outlooks, place labels, and station availability.

use crate::dto::contours::ContourLines;
use crate::dto::map::{
    AvailabilityReport, MapCatalog, PlaceLabels, RadarSiteCatalogPublic, SpcOutlook,
};
use crate::dto::radar::RadarTilts;
use crate::dto::reports::{ReportOptions, StormReports};
use crate::dto::surface::SurfacePlots;
use crate::error::BackendError;
use crate::state::AppState;

#[tauri::command]
pub async fn get_cached_forecast_hours(
    state: tauri::State<'_, AppState>,
    model: String,
    products: Vec<String>,
    run_time: String,
    hours: Vec<u32>,
) -> Result<Vec<u32>, BackendError> {
    let context = crate::backend::map_ops::context("get_cached_forecast_hours", "forecast cache");
    if !crate::backend::map_ops::key_ok(&model)
        || !crate::backend::map_ops::time_ok(&run_time)
        || products.is_empty()
        || products.len() > 4
        || products
            .iter()
            .any(|key| !crate::backend::map_ops::key_ok(key))
        || hours.is_empty()
        || hours.len() > 385
        || hours.iter().any(|hour| *hour > 384)
    {
        return Err(crate::backend::sounding_ops::invalid(
            &context,
            "invalid forecast-cache selection",
        ));
    }
    let result: Vec<u32> = state.router.call(&context, "fields.cached", serde_json::json!({"model": model, "products": products, "run_time": run_time, "hours": hours}), std::time::Duration::from_secs(15)).await?;
    if result.iter().any(|hour| !hours.contains(hour))
        || result.windows(2).any(|pair| pair[0] >= pair[1])
    {
        return Err(crate::backend::sounding_ops::rejected(
            &context,
            "fields.cached",
            "invalid cached hours".to_owned(),
        ));
    }
    Ok(result)
}

#[tauri::command]
pub async fn get_weather_alerts(
    state: tauri::State<'_, AppState>,
    valid_time: String,
) -> Result<crate::dto::weather_alerts::WeatherAlerts, BackendError> {
    state.router.weather_alerts(&valid_time).await
}

/// What the map can draw: the selected model's fields, radar, outlooks.
#[tauri::command]
pub async fn get_map_catalog(
    state: tauri::State<'_, AppState>,
    model: Option<String>,
    run_time: Option<String>,
    forecast_hour: Option<u32>,
) -> Result<MapCatalog, BackendError> {
    Ok(MapCatalog::clone(
        &*state
            .router
            .map_catalog(
                model.as_deref().unwrap_or("hrrr"),
                run_time.as_deref(),
                forecast_hour,
            )
            .await?,
    ))
}

/// One model product/run/hour, as an SHRF frame (an ArrayBuffer in JS).
#[tauri::command]
pub async fn get_weather_field(
    state: tauri::State<'_, AppState>,
    product: String,
    run_time: String,
    forecast_hour: u32,
    model: Option<String>,
    request_id: Option<String>,
) -> Result<tauri::ipc::Response, BackendError> {
    let frame = state
        .router
        .weather_field(
            &product,
            &run_time,
            forecast_hour,
            model.as_deref().unwrap_or("hrrr"),
            request_id.as_deref(),
        )
        .await?;
    Ok(tauri::ipc::Response::new(frame))
}

/// The SPC outlook of one product covering `valid_time`.
#[tauri::command]
pub async fn get_spc_outlook(
    state: tauri::State<'_, AppState>,
    product: String,
    valid_time: String,
) -> Result<SpcOutlook, BackendError> {
    state.router.spc_outlook(&product, &valid_time).await
}

/// The latest or requested recent radar scan, as an SHRF frame holding a PNG.
#[tauri::command]
pub async fn get_radar_frame(
    state: tauri::State<'_, AppState>,
    product: Option<String>,
    valid_time: Option<String>,
) -> Result<tauri::ipc::Response, BackendError> {
    let frame = state
        .router
        .radar_frame(product.as_deref(), valid_time.as_deref())
        .await?;
    Ok(tauri::ipc::Response::new(frame))
}

/// One NEXRAD antenna's own frame, as an SHRF frame holding a PNG.
/// `site` pins an antenna; otherwise `latitude`/`longitude` serve the nearest.
/// `product` null takes the backend's default single-site product.
#[tauri::command]
pub async fn get_radar_site_frame(
    state: tauri::State<'_, AppState>,
    product: Option<String>,
    site: Option<String>,
    latitude: Option<f64>,
    longitude: Option<f64>,
    valid_time: Option<String>,
    tilt: Option<String>,
) -> Result<tauri::ipc::Response, BackendError> {
    let frame = state
        .router
        .radar_site_frame(
            product.as_deref(),
            site.as_deref(),
            latitude,
            longitude,
            valid_time.as_deref(),
            tilt.as_deref(),
        )
        .await?;
    Ok(tauri::ipc::Response::new(frame))
}

/// Published elevations for this antenna, product and viewing time.
#[tauri::command]
pub async fn get_radar_tilts(
    state: tauri::State<'_, AppState>,
    site: String,
    product: String,
    valid_time: Option<String>,
) -> Result<RadarTilts, BackendError> {
    state
        .router
        .radar_tilts(&site, &product, valid_time.as_deref())
        .await
}

/// The bundled WSR-88D catalogue backing the single-site picker.
#[tauri::command]
pub async fn get_radar_sites(
    state: tauri::State<'_, AppState>,
) -> Result<RadarSiteCatalogPublic, BackendError> {
    Ok(RadarSiteCatalogPublic::clone(
        &*state.router.radar_sites().await?,
    ))
}

/// The GOES frame nearest `valid_time`, as an SHRF frame holding a rendered
/// PNG. `channel` is "visible" or "infrared" (infrared).
#[tauri::command]
pub async fn get_satellite_frame(
    state: tauri::State<'_, AppState>,
    valid_time: String,
    channel: Option<String>,
) -> Result<tauri::ipc::Response, BackendError> {
    let frame = state
        .router
        .satellite_frame(&valid_time, channel.as_deref())
        .await?;
    Ok(tauri::ipc::Response::new(frame))
}

/// The storm reports within a window of `valid_time`, after a hazard filter.
/// `windowS` is the total span in seconds (21600); `hazardFilter` is "all",
/// "tornado", "hail", "wind", or "significant" ("all").
#[tauri::command]
pub async fn get_storm_reports(
    state: tauri::State<'_, AppState>,
    valid_time: String,
    window_s: Option<u32>,
    hazard_filter: Option<String>,
) -> Result<StormReports, BackendError> {
    state
        .router
        .storm_reports(&valid_time, window_s, hazard_filter.as_deref())
        .await
}

/// The provider's own window and filter labels (cached for the session).
#[tauri::command]
pub async fn get_report_options(
    state: tauri::State<'_, AppState>,
) -> Result<ReportOptions, BackendError> {
    Ok(ReportOptions::clone(&*state.router.report_options().await?))
}

/// The isolines of one product's contour field, as lon/lat segments.
#[tauri::command]
pub async fn get_field_contours(
    state: tauri::State<'_, AppState>,
    product: String,
    run_time: String,
    forecast_hour: u32,
    model: Option<String>,
    request_id: Option<String>,
) -> Result<ContourLines, BackendError> {
    state
        .router
        .field_contours(
            &product,
            &run_time,
            forecast_hour,
            model.as_deref().unwrap_or("hrrr"),
            request_id.as_deref(),
        )
        .await
}

/// The station models near one point at one time.
#[tauri::command]
pub async fn get_surface_plots(
    state: tauri::State<'_, AppState>,
    latitude: f64,
    longitude: f64,
    valid_time: String,
) -> Result<SurfacePlots, BackendError> {
    state
        .router
        .surface_plots(latitude, longitude, &valid_time)
        .await
}

/// Map place names: major cities and towns.
#[tauri::command]
pub async fn get_place_labels(
    state: tauri::State<'_, AppState>,
) -> Result<PlaceLabels, BackendError> {
    Ok(PlaceLabels::clone(&*state.router.place_labels().await?))
}

/// The stations UWyo lists for one observation time.
#[tauri::command]
pub async fn get_station_availability(
    state: tauri::State<'_, AppState>,
    observation_time: String,
) -> Result<AvailabilityReport, BackendError> {
    state.router.station_availability(&observation_time).await
}
