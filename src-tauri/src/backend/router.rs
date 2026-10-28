//! The operation router: each semantic operation has one provider, and the
//! frontend never learns which one answered (DESIGN-bridge B5).
//!
//! | Operation | Phase 1 provider |
//! |---|---|
//! | App information | Rust-native, fixed at startup |
//! | Bridge status | The supervisor's state; never waits on Python |
//! | Python facts | The supervisor's cached `hello` |
//! | Scientific backend | Python bridge `health`, only while the bridge is `up` |
//! | Station catalogue | In-memory cache, else Python bridge `stations.catalog` |

use std::sync::{Arc, Mutex, PoisonError};
use std::time::Instant;

use serde_json::json;

use crate::backend::convert;
use crate::dto::health::{AppInfo, BackendHealth, BridgeStatus, ScientificBackendStatus};
use crate::dto::map::{MapCatalog, PlaceLabels, RadarSiteCatalogPublic};
use crate::dto::models::ModelCatalog;
use crate::dto::reports::ReportOptions;
use crate::dto::rfc3339_utc_now;
use crate::dto::stations::StationCatalog;
use crate::error::{BackendError, ErrorContext, PYTHON_PROVIDER};
use crate::python::protocol::{CatalogResult, HealthResult};
use crate::python::supervisor::Supervisor;
use sharpmod_weather::radar::VelocityInventory;

pub const OP_HEALTH: &str = "get_backend_health";
pub const OP_CATALOG: &str = "get_station_catalog";
pub const OP_RESTART: &str = "restart_backend";
const CATALOG_RESOURCE: &str = "bundled UWyo station catalogue";
const HEALTH_RESOURCE: &str = "scientific backend status";

pub struct Router {
    supervisor: Supervisor,
    app: AppInfo,
    include_executable: bool,
    catalog: Mutex<Option<Arc<StationCatalog>>>,
    /// The forecast model catalogue and when it was fetched (backend/sounding_ops.rs).
    pub(crate) models: Mutex<Option<(Instant, Arc<ModelCatalog>)>>,
    /// The map catalogue and the place names, static per bridge (backend/map_ops.rs).
    pub(crate) map_catalog: Mutex<Option<Arc<MapCatalog>>>,
    pub(crate) places: Mutex<Option<Arc<PlaceLabels>>>,
    /// The radar site catalogue, static per bridge (backend/map_ops.rs).
    pub(crate) radar_sites: Mutex<Option<Arc<RadarSiteCatalogPublic>>>,
    pub(crate) velocity_inventory: VelocityInventory,
    /// The storm report options, static per bridge (backend/reports_ops.rs).
    pub(crate) report_options: Mutex<Option<Arc<ReportOptions>>>,
}

impl Router {
    /// `include_executable` reports the interpreter path in health (debug builds).
    pub fn new(supervisor: Supervisor, app: AppInfo, include_executable: bool) -> Self {
        Self {
            supervisor,
            app,
            include_executable,
            catalog: Mutex::new(None),
            models: Mutex::new(None),
            map_catalog: Mutex::new(None),
            places: Mutex::new(None),
            radar_sites: Mutex::new(None),
            velocity_inventory: VelocityInventory::new(),
            report_options: Mutex::new(None),
        }
    }

    pub fn supervisor(&self) -> &Supervisor {
        &self.supervisor
    }

    /// The health report. It succeeds in every bridge state: bridge problems
    /// are reported inside the DTO.
    pub async fn health(&self) -> Result<BackendHealth, BackendError> {
        let backend = if matches!(self.supervisor.status(), BridgeStatus::Up { .. }) {
            Some(self.scientific_backend().await)
        } else {
            None
        };
        let hello = self.supervisor.hello();
        Ok(convert::health(
            self.app.clone(),
            self.supervisor.status(),
            hello.as_ref(),
            backend,
            rfc3339_utc_now(),
            self.include_executable,
        ))
    }

    async fn scientific_backend(&self) -> ScientificBackendStatus {
        let context = ErrorContext::new(OP_HEALTH, Some(PYTHON_PROVIDER), Some(HEALTH_RESOURCE));
        let limit = self.supervisor.config().health_timeout;
        match self
            .supervisor
            .request_typed::<HealthResult>("health", json!({}), limit)
            .await
        {
            Ok(result) => convert::scientific_backend(result.scientific_backend),
            Err(failure) => {
                let error = failure.into_backend_error(&context);
                log::warn!(
                    "health: scientific backend unknown: {} ({})",
                    error.message,
                    error.code.as_str()
                );
                ScientificBackendStatus::Unknown { error }
            }
        }
    }

    /// The station catalogue, from the cache after the first success.
    pub async fn station_catalog(&self) -> Result<Arc<StationCatalog>, BackendError> {
        if let Some(cached) = self.cached() {
            return Ok(cached);
        }
        let context = ErrorContext::new(OP_CATALOG, Some(PYTHON_PROVIDER), Some(CATALOG_RESOURCE));
        let limit = self.supervisor.config().catalog_timeout;
        let result = self
            .supervisor
            .request_typed::<CatalogResult>("stations.catalog", json!({}), limit)
            .await
            .map_err(|failure| {
                let error = failure.into_backend_error(&context);
                log::warn!(
                    "{OP_CATALOG} failed: {} ({})",
                    error.message,
                    error.code.as_str()
                );
                error
            })?;
        let catalog = Arc::new(convert::catalog(result));
        let bytes = serde_json::to_vec(&*catalog).map(|v| v.len()).unwrap_or(0);
        log::info!(
            "station catalogue loaded: {} stations, origin {:?}, {} dropped, {bytes} bytes",
            catalog.stations.len(),
            catalog.origin,
            catalog.dropped_count
        );
        *self.catalog.lock().unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&catalog));
        Ok(catalog)
    }

    fn cached(&self) -> Option<Arc<StationCatalog>> {
        self.catalog
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
    }

    /// Clears the catalogue cache and restarts the bridge (an explicit user action).
    pub async fn restart_backend(&self) -> Result<BridgeStatus, BackendError> {
        *self.catalog.lock().unwrap_or_else(PoisonError::into_inner) = None;
        *self.models.lock().unwrap_or_else(PoisonError::into_inner) = None;
        *self
            .map_catalog
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = None;
        *self.places.lock().unwrap_or_else(PoisonError::into_inner) = None;
        *self
            .radar_sites
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = None;
        *self
            .report_options
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = None;
        self.velocity_inventory.clear();
        log::info!("{OP_RESTART}: restarting the Python backend");
        Ok(self.supervisor.restart().await)
    }
}
