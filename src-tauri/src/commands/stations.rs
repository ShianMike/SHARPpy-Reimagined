//! Station commands.

use crate::dto::stations::StationCatalog;
use crate::error::BackendError;
use crate::state::AppState;

/// The bundled station catalogue, with availability `unknown` on every station.
#[tauri::command]
pub async fn get_station_catalog(
    state: tauri::State<'_, AppState>,
) -> Result<StationCatalog, BackendError> {
    let catalog = state.router.station_catalog().await?;
    Ok(StationCatalog::clone(&catalog))
}
