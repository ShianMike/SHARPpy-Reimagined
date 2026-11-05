//! Opt-in timings of the production decoder; absent from release builds.

use super::*;
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

thread_local! {
    static TIMINGS: RefCell<Option<BTreeMap<&'static str, (usize, Duration)>>> = const { RefCell::new(None) };
}

pub(super) struct Span {
    name: &'static str,
    started: Option<Instant>,
}

pub(super) fn span(name: &'static str) -> Span {
    Span {
        name,
        started: TIMINGS.with(|timings| timings.borrow().as_ref().map(|_| Instant::now())),
    }
}

impl Drop for Span {
    fn drop(&mut self) {
        if let Some(started) = self.started {
            let elapsed = started.elapsed();
            TIMINGS.with(|timings| {
                if let Some(timings) = timings.borrow_mut().as_mut() {
                    let entry = timings.entry(self.name).or_default();
                    entry.0 += 1;
                    entry.1 += elapsed;
                }
            });
        }
    }
}

#[test]
#[ignore = "requires SHARPMOD_GRIB_BENCH_FIXTURE and SHARPMOD_ECCODES_LIBRARY"]
fn profile_configured_point_extraction() {
    let fixture = PathBuf::from(std::env::var_os("SHARPMOD_GRIB_BENCH_FIXTURE").unwrap());
    let library = PathBuf::from(std::env::var_os("SHARPMOD_ECCODES_LIBRARY").unwrap());
    let reference_path = std::env::var_os("SHARPMOD_GRIB_PROFILE_REFERENCE").map(PathBuf::from);
    let output_path = std::env::var_os("SHARPMOD_GRIB_PROFILE_OUTPUT").map(PathBuf::from);
    let mut reports = Vec::new();
    let mut reference = None;
    clear_grib_inventory_cache(true);
    for label in [
        "cold",
        "inventory_reuse_1",
        "inventory_reuse_2",
        "geometry_evicted",
        "geometry_reuse_3",
    ] {
        if label == "geometry_evicted" {
            inventory_cache().grid_points.clear();
        }
        TIMINGS.with(|timings| *timings.borrow_mut() = Some(BTreeMap::new()));
        let started = Instant::now();
        let result = decode_grib_point(&fixture, &library, 35.18, -97.44, Some(-9999.0)).unwrap();
        let elapsed = started.elapsed();
        let timings = TIMINGS.with(|timings| timings.borrow_mut().take().unwrap());
        let output = serde_json::json!({
            "matrix_bits": result.matrix.iter().map(|value| value.to_bits()).collect::<Vec<_>>(),
            "levels": result.level_count,
            "latitude_bits": result.selected_latitude.to_bits(),
            "longitude_bits": result.selected_longitude.to_bits(),
            "vorticity_bits": result.surface_relative_vorticity.map(f64::to_bits),
            "surface_merged": result.surface_merged,
            "removed_levels": result.below_ground_levels_removed,
        });
        if let Some(reference) = &reference {
            assert_eq!(&output, reference, "{label}: decoded output changed");
        } else {
            reference = Some(output.clone());
        }
        let stages: BTreeMap<_, _> = timings.into_iter().map(|(name, (calls, elapsed))| {
            (name, serde_json::json!({"calls": calls, "milliseconds": elapsed.as_secs_f64() * 1000.0}))
        }).collect();
        let report = serde_json::json!({
            "label": label,
            "milliseconds": elapsed.as_secs_f64() * 1000.0,
            "stages": stages,
        });
        eprintln!("{}", serde_json::to_string(&report).unwrap());
        reports.push(report);
    }
    if let Some(path) = reference_path {
        if path.exists() {
            let saved: serde_json::Value =
                serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
            assert_eq!(
                reference.as_ref().unwrap(),
                &saved,
                "baseline decoded bits changed"
            );
        } else {
            std::fs::write(
                path,
                serde_json::to_vec_pretty(reference.as_ref().unwrap()).unwrap(),
            )
            .unwrap();
        }
    }
    if let Some(path) = output_path {
        std::fs::write(path, serde_json::to_vec_pretty(&reports).unwrap()).unwrap();
    }
}
