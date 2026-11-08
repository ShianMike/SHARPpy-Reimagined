//! Box extraction reads selected native indexes once per message, with no point queries.

#[cfg(test)]
mod benchmark;
mod cache;
mod columns;
mod grid;
#[cfg(test)]
mod surface_tests;
#[cfg(test)]
mod tests;
mod traces;

use super::*;
use crate::box_soundings::{
    geometry::Bounds,
    profile::{self, Averaged, Column},
    Error, Limits,
};
use std::sync::TryLockError;
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Default)]
pub struct Timings {
    pub source_ms: f64,
    pub geometry_ms: f64,
    pub decode_ms: f64,
    pub unpack_read_ms: f64,
    pub column_assembly_ms: f64,
    pub aggregation_ms: f64,
    pub interpolation_ms: f64,
    pub spatial_ms: f64,
    pub total_ms: f64,
}

#[derive(Clone, Debug)]
pub struct Output {
    pub averaged: Averaged,
    pub display_profiles: Arc<[DecodedPoint]>,
    pub bounds: Bounds,
    pub requested_area_m2: f64,
    pub grid_resolution_km: f64,
    pub grid_type: String,
    pub cache_hit: bool,
    pub timings: Timings,
}

impl From<GribError> for Error {
    fn from(error: GribError) -> Self {
        Self::Data(error.to_string())
    }
}

pub fn decode(
    path: &Path,
    library: &Path,
    bounds: Bounds,
    limits: Limits,
    cancelled: &dyn Fn() -> bool,
) -> Result<Output, Error> {
    let total = Instant::now();
    let limits = limits.validate()?;
    if cancelled() {
        return Err(Error::Cancelled);
    }
    let (_file, mapping, identity) = open_grib_mapping(path)?;
    let content = point_cache::fingerprint(&mapping);
    let generation = {
        let cache = inventory_cache();
        cache.enabled.then_some(cache.generation)
    };
    let key = cache::Key::new(content, bounds, limits, generation);
    if generation.is_some() {
        if let Some(mut result) = cache::get(&key) {
            ECCODES_API
                .get()
                .expect("cached box has an initialized runtime")
                .for_path(library)?;
            if cancelled() {
                return Err(Error::Cancelled);
            }
            result.cache_hit = true;
            result.timings = Timings {
                source_ms: total.elapsed().as_secs_f64() * 1000.0,
                total_ms: total.elapsed().as_secs_f64() * 1000.0,
                ..Timings::default()
            };
            return Ok(result);
        }
    }
    let source_ms = total.elapsed().as_secs_f64() * 1000.0;
    let guard = loop {
        if cancelled() {
            return Err(Error::Cancelled);
        }
        match ECCODES_CALL_LOCK.try_lock() {
            Ok(guard) => break guard,
            Err(TryLockError::WouldBlock) => std::thread::sleep(Duration::from_millis(10)),
            Err(TryLockError::Poisoned(_)) => {
                return Err(Error::Data(
                    "The native GRIB runtime lock was poisoned".into(),
                ))
            }
        }
    };
    let api = EccodesApi::cached(library)?;
    // Another request may have completed this exact box while we waited.
    if generation.is_some() {
        if let Some(mut result) = cache::get(&key) {
            if cancelled() {
                return Err(Error::Cancelled);
            }
            result.cache_hit = true;
            result.timings = Timings {
                source_ms: total.elapsed().as_secs_f64() * 1000.0,
                total_ms: total.elapsed().as_secs_f64() * 1000.0,
                ..Timings::default()
            };
            return Ok(result);
        }
    }
    let _multi = api.enable_multi_support();
    let cached = identity.as_ref().and_then(|identity| {
        let mut cache = inventory_cache();
        let inventory = cache.lookup(identity)?;
        if inventory.content != Some(content) || !inventory.matches_mapping(&mapping) {
            cache.invalidate(identity);
            None
        } else {
            Some(inventory)
        }
    });
    let geometry_started = Instant::now();
    let inventory = if let Some(inventory) = cached {
        inventory
    } else {
        let mut decoder = messages::PointDecoder::new(&[], Some(-9999.0));
        let mut inventory_messages = Vec::new();
        for boundary in scan_message_boundaries(&mapping)? {
            if cancelled() {
                return Err(Error::Cancelled);
            }
            let bytes = &mapping[boundary.offset..boundary.offset + boundary.length];
            if let Some(message) = decoder.message(api, bytes, boundary)? {
                inventory_messages.push(message);
            }
        }
        let inventory = Arc::new(GribInventory {
            messages: inventory_messages,
            content: Some(content),
        });
        if let Some(identity) = identity.clone() {
            inventory_cache().insert(identity, inventory.clone());
        }
        inventory
    };
    let (grid, grid_key, grid_type) = grid::read(api, &mapping, &inventory)?;
    let cells = grid.select(bounds, limits, cancelled)?;
    let requested_area_m2 = bounds.area(grid.radius);
    let grid_resolution_km = match grid.projection {
        crate::box_soundings::grid::Projection::RegularLatLon {
            latitude_increment, ..
        } => grid.radius * latitude_increment.to_radians() / 1000.0,
        crate::box_soundings::grid::Projection::Lambert { dx, .. } => dx / 1000.0,
    };
    let geometry_ms = geometry_started.elapsed().as_secs_f64() * 1000.0;
    let decoding_started = Instant::now();
    let (points, vorticity, unpack_read_ms, column_assembly_ms) =
        columns::decode(api, &mapping, &inventory, &grid_key, &cells, cancelled)?;
    let decode_ms = decoding_started.elapsed().as_secs_f64() * 1000.0;
    drop(_multi);
    let aggregation_started = Instant::now();
    let columns: Vec<_> = points
        .iter()
        .zip(&cells)
        .zip(&vorticity)
        .map(|((profile, cell), vorticity)| Column {
            profile,
            overlap_area: cell.overlap_area,
            vorticity,
        })
        .collect();
    let averaged = profile::aggregate(&columns, bounds, requested_area_m2, limits, cancelled)?;
    let display_profiles = traces::select(&columns);
    let interpolation_ms = averaged.interpolation_ms;
    let spatial_ms = averaged.spatial_ms;
    let result = Output {
        averaged,
        display_profiles,
        bounds,
        requested_area_m2,
        grid_resolution_km,
        grid_type,
        cache_hit: false,
        timings: Timings {
            source_ms,
            geometry_ms,
            decode_ms,
            unpack_read_ms,
            column_assembly_ms,
            interpolation_ms,
            spatial_ms,
            aggregation_ms: aggregation_started.elapsed().as_secs_f64() * 1000.0,
            total_ms: total.elapsed().as_secs_f64() * 1000.0,
        },
    };
    if cancelled() {
        return Err(Error::Cancelled);
    }
    if generation.is_some_and(|value| {
        let cache = inventory_cache();
        cache.enabled && cache.generation == value
    }) {
        cache::insert(key, result.clone());
    }
    drop(guard);
    Ok(result)
}
