//! Opt-in measurements of the production extractor, never part of routine checks.
use super::*;

#[cfg(windows)]
fn peak_memory_bytes() -> usize {
    #[repr(C)]
    #[derive(Default)]
    struct Counters {
        size: u32,
        faults: u32,
        peak: usize,
        working: usize,
        quota_peak_paged: usize,
        quota_paged: usize,
        quota_peak_nonpaged: usize,
        quota_nonpaged: usize,
        pagefile: usize,
        peak_pagefile: usize,
        private: usize,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut c_void;
    }
    #[link(name = "psapi")]
    unsafe extern "system" {
        fn GetProcessMemoryInfo(process: *mut c_void, counters: *mut Counters, size: u32) -> i32;
    }
    let mut counters = Counters {
        size: std::mem::size_of::<Counters>() as u32,
        ..Default::default()
    };
    // SAFETY: the pseudo process handle is valid and Counters matches PROCESS_MEMORY_COUNTERS_EX.
    let success =
        unsafe { GetProcessMemoryInfo(GetCurrentProcess(), &mut counters, counters.size) };
    assert_ne!(success, 0);
    counters.peak
}

#[cfg(not(windows))]
fn peak_memory_bytes() -> usize {
    0
}

#[test]
#[ignore = "requires a verified surface GRIB fixture and SHARPMOD_BOX_BENCH_OUTPUT"]
fn measure_native_boxes_with_application_cold_and_warm_caches() {
    let path = PathBuf::from(std::env::var_os("SHARPMOD_GRIB_BENCH_FIXTURE").unwrap());
    let library = PathBuf::from(std::env::var_os("SHARPMOD_ECCODES_LIBRARY").unwrap());
    let output = PathBuf::from(std::env::var_os("SHARPMOD_BOX_BENCH_OUTPUT").unwrap());
    let (_file, mapping, _) = open_grib_mapping(&path).unwrap();
    let grid = {
        let _lock = ECCODES_CALL_LOCK.lock().unwrap();
        let api = EccodesApi::cached(&library).unwrap();
        let _multi = api.enable_multi_support();
        let mut decoder = messages::PointDecoder::new(&[], Some(-9999.0));
        let messages = scan_message_boundaries(&mapping)
            .unwrap()
            .into_iter()
            .filter_map(|boundary| {
                decoder
                    .message(
                        api,
                        &mapping[boundary.offset..boundary.offset + boundary.length],
                        boundary,
                    )
                    .unwrap()
            })
            .collect();
        grid::read(
            api,
            &mapping,
            &GribInventory {
                messages,
                content: None,
            },
        )
        .unwrap()
        .0
    };
    let spacing = match grid.projection {
        crate::box_soundings::grid::Projection::RegularLatLon {
            latitude_increment, ..
        } => latitude_increment,
        crate::box_soundings::grid::Projection::Lambert { dy, .. } => dy / 111_000.0,
    };
    let mut reports = Vec::new();
    for target in [4, 9, 25, 100, 400] {
        let mut found = None;
        'search: for ratio in 0..80 {
            let height = spacing * (f64::sqrt(target as f64) - 0.7) * (0.65 + ratio as f64 / 80.0);
            let (mut low, mut high) = (spacing / 100.0, spacing * f64::sqrt(target as f64) * 4.0);
            for _ in 0..28 {
                let width = (low + high) / 2.0;
                let bounds = Bounds::new(
                    -97.44 - width / 2.0,
                    35.18 - height / 2.0,
                    -97.44 + width / 2.0,
                    35.18 + height / 2.0,
                )
                .unwrap();
                let count = grid
                    .select(bounds, Limits::default(), &|| false)
                    .unwrap()
                    .len();
                if count == target {
                    found = Some(bounds);
                    break 'search;
                }
                if count > target {
                    high = width;
                } else {
                    low = width;
                }
            }
        }
        let bounds = found.unwrap_or_else(|| {
            panic!("could not select exactly {target} overlapping native cells")
        });
        clear_grib_inventory_cache(true);
        for mode in ["application_cache_cold", "exact_profile_cache_hit"] {
            let result = decode(&path, &library, bounds, Limits::default(), &|| false).unwrap();
            assert_eq!(result.averaged.selected_columns, target);
            assert_eq!(result.cache_hit, mode == "exact_profile_cache_hit");
            let timings = &result.timings;
            let report = serde_json::json!({ "columns": target, "mode": mode,
                "bounds": {"west":bounds.west,"east":bounds.east,"south":bounds.south,"north":bounds.north},
                "valid_columns":result.averaged.valid_columns, "levels":result.averaged.profile.level_count,
                "cache_hit":result.cache_hit,"source_ms":timings.source_ms,"geometry_ms":timings.geometry_ms,
                "decode_ms":timings.decode_ms,"unpack_read_ms":timings.unpack_read_ms,"column_assembly_ms":timings.column_assembly_ms,
                "interpolation_ms":timings.interpolation_ms,"spatial_ms":timings.spatial_ms,"aggregation_ms":timings.aggregation_ms,
                "total_ms":timings.total_ms,"process_peak_working_set_bytes":peak_memory_bytes() });
            eprintln!("{}", report);
            reports.push(report);
            // Retain every completed measurement even if a later size fails.
            std::fs::write(&output, serde_json::to_vec_pretty(&reports).unwrap()).unwrap();
        }
    }
}
