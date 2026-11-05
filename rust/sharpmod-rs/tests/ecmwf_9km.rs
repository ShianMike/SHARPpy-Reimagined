//! Real IFS 0.1-degree pressure/surface retrieval regression.
//!
//! Use the combined 2026-11-05 00Z F012 subset acquired by the model extractor,
//! including the same run's F000 terrain. A surface-only download reproduces
//! the original failure instead of satisfying this sounding contract.

use sharpmod_rs::grib::decode_grib_point;
use std::path::PathBuf;

#[test]
#[ignore = "requires SHARPMOD_ECMWF_9KM_FIXTURE and SHARPMOD_ECCODES_LIBRARY"]
fn real_ifs_9km_f012_has_pressure_levels_and_verified_ground_at_reported_point() {
    let fixture = PathBuf::from(
        std::env::var_os("SHARPMOD_ECMWF_9KM_FIXTURE").expect("set the real ECMWF fixture"),
    );
    let library = PathBuf::from(
        std::env::var_os("SHARPMOD_ECCODES_LIBRARY").expect("set the ecCodes library path"),
    );
    let point = decode_grib_point(&fixture, &library, -27.1173, -52.4449, Some(-9999.0))
        .expect("IFS sounding must include pressure data, not only the surface companion");

    assert!((point.selected_latitude + 27.1).abs() < 1.0e-8);
    assert!((point.selected_longitude + 52.4).abs() < 1.0e-8);
    assert!(point.surface_merged);
    assert_eq!(point.below_ground_levels_removed, 1);
    assert_eq!(point.level_count, 14);
    let columns = point
        .matrix
        .chunks_exact(point.level_count)
        .collect::<Vec<_>>();
    assert_eq!(columns.len(), 9);
    assert!((columns[0][0] - 942.9233203125).abs() < 0.01);
    assert_eq!(
        &columns[0][1..],
        &[925.0, 850.0, 700.0, 600.0, 500.0, 400.0, 300.0, 250.0, 200.0, 150.0, 100.0, 50.0, 10.0],
    );
    assert!(columns[1].windows(2).all(|pair| pair[1] > pair[0]));
    for column in &columns[..6] {
        assert!(column
            .iter()
            .all(|value| value.is_finite() && *value != -9999.0));
    }
    assert!(columns[2]
        .iter()
        .zip(columns[3])
        .all(|(t, td)| td <= &(t + 0.01)));
    assert!(columns[4]
        .iter()
        .all(|direction| (0.0..=360.0).contains(direction)));
    assert!(columns[5].iter().all(|speed| *speed >= 0.0));
    assert!(point.surface_relative_vorticity.is_some_and(f64::is_finite));

    // Exercise retained native inventories/grid indexes as well as a cold decode.
    let cached = decode_grib_point(&fixture, &library, -27.1173, -52.4449, Some(-9999.0))
        .expect("cached IFS decode");
    assert_eq!(point, cached);
    eprintln!(
        "real ECMWF F012: {} levels at {}, {}; surface={} hPa; removed={}",
        point.level_count,
        point.selected_latitude,
        point.selected_longitude,
        columns[0][0],
        point.below_ground_levels_removed,
    );
}
