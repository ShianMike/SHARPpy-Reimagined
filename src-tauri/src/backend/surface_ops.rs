//! Surface station-plot operation, served by the Python bridge
//! (`sharpmod/bridge/surface_plots.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_surface_plots` | `surface.plots` | 90 s |
//!
//! The plots cross as JSON point records with the display slots and the
//! wind-staff mark spec, not a frame: the frontend draws the station models.

use std::time::Duration;

use serde_json::json;

use crate::backend::map_ops::{context, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::surface::SurfacePlots;
use crate::error::BackendError;

pub const OP_SURFACE_PLOTS: &str = "get_surface_plots";

/// The fetch walks the NWS station catalogue and then each nearby station in
/// turn, so it gets the widest map deadline rather than a frame's.
const SURFACE_TIMEOUT: Duration = Duration::from_secs(90);

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
        let params = json!({
            "latitude": latitude,
            "longitude": longitude,
            "valid_time": valid_time,
        });
        let plots: SurfacePlots = self
            .call(&context, "surface.plots", params, SURFACE_TIMEOUT)
            .await?;
        plots
            .validate()
            .map_err(|reason| rejected(&context, "surface.plots", reason))?;
        Ok(plots)
    }
}
