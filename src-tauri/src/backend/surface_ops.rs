//! Surface station-plot operations, served natively by `sharpmod_weather::surface`.
//!
//! | Operation | Backend | Deadline |
//! |---|---|---|
//! | `get_surface_plots` | `sharpmod_weather::surface` | 15 s / station |
//! | `get_latest_surface_plots` | `sharpmod_weather::surface` | 12 s bulk request |
//!
//! The plots cross as JSON point records with the display slots and the
//! wind-staff mark spec, not a frame: the frontend draws the station models.

use time::{format_description::well_known::Rfc3339, OffsetDateTime};

use crate::backend::map_ops::{context, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::surface::SurfacePlots;
use crate::error::{BackendError, ErrorCode, ErrorContext};

pub const OP_SURFACE_PLOTS: &str = "get_surface_plots";
pub const OP_LATEST_SURFACE_PLOTS: &str = "get_latest_surface_plots";

fn fetch_failed(context: &ErrorContext, error: String) -> BackendError {
    BackendError {
        code: ErrorCode::OverlayFetchFailed,
        message: format!("The surface observations could not be loaded: {error}."),
        retryable: true,
        context: Some(Box::new(context.clone())),
        details: None,
    }
}

impl Router {
    /// The station models near one point at one time.
    pub async fn surface_plots(
        &self,
        latitude: f64,
        longitude: f64,
        valid_time: &str,
    ) -> Result<SurfacePlots, BackendError> {
        let context = context(
            OP_SURFACE_PLOTS,
            &format!("surface plots near {latitude:.4}, {longitude:.4}"),
        );
        if !(-90.0..=90.0).contains(&latitude) {
            return Err(invalid(&context, "latitude must be within [-90, 90]"));
        }
        if !(-180.0..=180.0).contains(&longitude) {
            return Err(invalid(&context, "longitude must be within [-180, 180]"));
        }
        if !time_ok(valid_time) {
            return Err(invalid(&context, "validTime must be an RFC 3339 time"));
        }
        let when = OffsetDateTime::parse(valid_time, &Rfc3339)
            .map_err(|_| invalid(&context, "validTime must be an RFC 3339 time"))?;
        let snapshot = sharpmod_weather::surface::fetch_plots(latitude, longitude, when)
            .await
            .map_err(|error| fetch_failed(&context, error))?;
        let plots = SurfacePlots::from(snapshot);
        plots
            .validate()
            .map_err(|reason| rejected(&context, "surface.plots", reason))?;
        Ok(plots)
    }

    pub async fn latest_surface_plots(&self) -> Result<SurfacePlots, BackendError> {
        let context = context(
            OP_LATEST_SURFACE_PLOTS,
            "latest worldwide METAR observations",
        );
        let latest = sharpmod_weather::surface::fetch_latest_plots()
            .await
            .map_err(|error| fetch_failed(&context, error))?;
        let plots = SurfacePlots::from(latest.snapshot.clone());
        plots
            .validate_latest()
            .map_err(|reason| rejected(&context, "surface.latest_plots", reason))?;
        Ok(plots)
    }
}
