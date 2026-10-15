//! Bridge payloads to public DTOs: the one place where snake_case becomes
//! camelCase. Station records are re-validated here, because Rust is the trust
//! boundary in front of the webview.

use std::collections::HashSet;

use crate::dto::health::{
    AppInfo, BackendHealth, BridgeStatus, PythonInfo, ScientificBackendStatus,
};
use crate::dto::stations::{
    check_record, AvailabilityBasis, CatalogOrigin, Station, StationAvailability, StationCatalog,
};
use crate::python::protocol::{BridgeOrigin, BridgeScientificBackend, CatalogResult, HelloResult};

/// Converts a `stations.catalog` result. Records that fail the record rule, or
/// repeat an earlier id, are dropped, logged, and added to `droppedCount`.
pub fn catalog(result: CatalogResult) -> StationCatalog {
    let mut seen = HashSet::with_capacity(result.stations.len());
    let mut dropped = result.dropped_count;
    let mut stations = Vec::with_capacity(result.stations.len());
    for record in result.stations {
        let verdict = check_record(
            &record.id,
            &record.name,
            record.lat,
            record.lon,
            &record.src,
        )
        .and_then(|()| {
            if seen.contains(&record.id) {
                Err("duplicate id")
            } else {
                Ok(())
            }
        });
        if let Err(reason) = verdict {
            log::warn!(
                "station catalogue: dropped record {:?}: {reason}",
                record.id
            );
            dropped = dropped.saturating_add(1);
            continue;
        }
        seen.insert(record.id.clone());
        stations.push(Station {
            id: record.id,
            name: record.name,
            latitude: record.lat,
            longitude: record.lon,
            source: record.src,
            availability: StationAvailability::Unknown,
        });
    }
    StationCatalog {
        stations,
        origin: match result.origin {
            BridgeOrigin::Bundled => CatalogOrigin::Bundled,
            BridgeOrigin::SeedFallback => CatalogOrigin::SeedFallback,
        },
        availability_basis: AvailabilityBasis::CatalogOnly,
        dropped_count: dropped,
    }
}

/// Converts the bridge `health` payload's scientific backend.
pub fn scientific_backend(backend: BridgeScientificBackend) -> ScientificBackendStatus {
    match backend {
        BridgeScientificBackend::Ok {
            requested_backend,
            active_backend,
            rust_installed,
            rust_version,
            fallback_reason,
        } => ScientificBackendStatus::Ok {
            requested_backend,
            active_backend,
            rust_installed,
            rust_version,
            fallback_reason,
        },
        BridgeScientificBackend::Unavailable { code, message } => {
            ScientificBackendStatus::Unavailable { code, message }
        }
    }
}

/// Python facts from the cached `hello`; the executable is reported only in
/// debug builds (`include_executable`).
pub fn python_info(hello: &HelloResult, include_executable: bool) -> PythonInfo {
    PythonInfo {
        version: hello.python_version.clone(),
        executable: include_executable.then(|| hello.python_executable.clone()),
        sharpmod_version: hello.sharpmod_version.clone(),
    }
}

/// Assembles the health DTO. `python` and `scientificBackend` are reported
/// only while the bridge is `up`.
pub fn health(
    app: AppInfo,
    bridge: BridgeStatus,
    hello: Option<&HelloResult>,
    backend: Option<ScientificBackendStatus>,
    checked_at: String,
    include_executable: bool,
) -> BackendHealth {
    let up = matches!(bridge, BridgeStatus::Up { .. });
    BackendHealth {
        app,
        python: hello
            .filter(|_| up)
            .map(|h| python_info(h, include_executable)),
        scientific_backend: backend.filter(|_| up),
        bridge,
        checked_at,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::python::protocol::BridgeStation;

    fn station(id: &str, lat: f64) -> BridgeStation {
        BridgeStation {
            id: id.into(),
            name: "N".into(),
            lat,
            lon: 10.0,
            src: "FM35".into(),
        }
    }

    #[test]
    fn re_validates_records() {
        let result = CatalogResult {
            stations: vec![
                station("72357", 35.0),
                station("72357", 36.0),
                station("", 1.0),
                station(" 1 ", 1.0),
                station("x", f64::NAN),
                station("y", 90.5),
                station("72358", -90.0),
            ],
            origin: BridgeOrigin::SeedFallback,
            dropped_count: 2,
        };
        let catalog = catalog(result);
        let ids: Vec<_> = catalog.stations.iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["72357", "72358"]);
        assert_eq!(catalog.dropped_count, 7);
        assert_eq!(catalog.origin, CatalogOrigin::SeedFallback);
        assert!(catalog
            .stations
            .iter()
            .all(|s| s.availability == StationAvailability::Unknown));
        assert!(catalog.validate().is_ok());
    }

    #[test]
    fn long_text_is_counted_in_characters() {
        let mut long = station("ß".repeat(16).as_str(), 1.0);
        long.name = "é".repeat(200);
        let result = CatalogResult {
            stations: vec![long],
            origin: BridgeOrigin::Bundled,
            dropped_count: 0,
        };
        assert_eq!(catalog(result).stations.len(), 1);
    }
}
