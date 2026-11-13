//! Surface station-plot operation, served natively by `sharpmod_weather::surface`.
//!
//! | Operation | Backend | Deadline |
//! |---|---|---|
//! | `get_surface_plots` | `sharpmod_weather::surface` | 15 s / station |
//!
//! The plots cross as JSON point records with the display slots and the
//! wind-staff mark spec, not a frame: the frontend draws the station models.

use time::{format_description::well_known::Rfc3339, OffsetDateTime};

use crate::backend::map_ops::{context, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::surface::{StaffSpec, SurfacePlot, SurfacePlotCounts, SurfacePlots};
use crate::error::{BackendError, ErrorCode};

pub const OP_SURFACE_PLOTS: &str = "get_surface_plots";

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
            .map_err(|error| BackendError {
                code: ErrorCode::OverlayFetchFailed,
                message: format!("The surface observations could not be loaded: {error}."),
                retryable: true,
                context: Some(Box::new(context.clone())),
                details: None,
            })?;
        let plots = SurfacePlots {
            valid_time: snapshot.valid_time,
            counts: SurfacePlotCounts {
                matched: snapshot.counts.matched,
                stale: snapshot.counts.stale,
                no_observation: snapshot.counts.no_observation,
                failed: snapshot.counts.failed,
                nearby: snapshot.counts.nearby,
                queried: snapshot.counts.queried,
                unqueried: snapshot.counts.unqueried,
            },
            plots: snapshot
                .plots
                .into_iter()
                .map(|plot| SurfacePlot {
                    id: plot.id,
                    longitude: plot.longitude,
                    latitude: plot.latitude,
                    valid: plot.valid,
                    offset_min: plot.offset_min,
                    stale: plot.stale,
                    availability: plot.availability,
                    wind_state: plot.wind_state,
                    staff: plot.staff.map(|s| StaffSpec {
                        angle_deg: s.angle_deg,
                        pennants: s.pennants,
                        barbs: s.barbs,
                        halves: s.halves,
                    }),
                    temperature_c: plot.temperature_c,
                    dewpoint_c: plot.dewpoint_c,
                    upper_text: plot.upper_text,
                    lower_text: plot.lower_text,
                    label: plot.label,
                    description: plot.description,
                })
                .collect(),
        };
        plots
            .validate()
            .map_err(|reason| rejected(&context, "surface.plots", reason))?;
        Ok(plots)
    }
}
