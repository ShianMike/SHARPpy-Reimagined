use criterion::{criterion_group, criterion_main, BatchSize, Criterion};
use sharpmod_rs::grib::{
    clear_grib_inventory_cache, decode_grib_point, decode_grib_points,
    set_grib_inventory_cache_enabled,
};
use std::hint::black_box;
use std::path::PathBuf;
use std::time::Duration;

fn configured_paths() -> Option<(PathBuf, PathBuf)> {
    let fixture = std::env::var_os("SHARPMOD_GRIB_BENCH_FIXTURE").map(PathBuf::from)?;
    let library = std::env::var_os("SHARPMOD_ECCODES_LIBRARY").map(PathBuf::from)?;
    if fixture.is_file() && library.is_file() {
        Some((fixture, library))
    } else {
        None
    }
}

fn bench_grib(c: &mut Criterion) {
    let Some((fixture, library)) = configured_paths() else {
        eprintln!(
            "skipping GRIB benchmarks: set SHARPMOD_GRIB_BENCH_FIXTURE and \
             SHARPMOD_ECCODES_LIBRARY to existing files"
        );
        return;
    };
    set_grib_inventory_cache_enabled(true);
    let point = (35.18, -97.44);
    let second_point = (35.23, -97.39);
    let points = [point, point, second_point, (35.28, -97.34)];
    let mut group = c.benchmark_group("grib");
    // GRIB decoding is intentionally an integration-scale benchmark. Keep the
    // fixed sample count small enough for release machines and record the OS
    // file-cache state separately in the surrounding benchmark report.
    group.sample_size(10);
    group.measurement_time(Duration::from_secs(20));

    group.bench_function("point_application_cache_cold", |bencher| {
        bencher.iter_batched(
            || clear_grib_inventory_cache(false),
            |_| {
                decode_grib_point(
                    black_box(&fixture),
                    black_box(&library),
                    black_box(point.0),
                    black_box(point.1),
                    Some(-9999.0),
                )
                .expect("configured GRIB fixture should decode")
            },
            BatchSize::PerIteration,
        )
    });

    clear_grib_inventory_cache(false);
    decode_grib_point(&fixture, &library, point.0, point.1, Some(-9999.0))
        .expect("configured GRIB fixture should warm its inventory");
    group.bench_function("point_inventory_reuse", |bencher| {
        bencher.iter(|| {
            decode_grib_point(
                black_box(&fixture),
                black_box(&library),
                black_box(second_point.0),
                black_box(second_point.1),
                Some(-9999.0),
            )
            .expect("configured GRIB fixture should decode")
        })
    });
    group.bench_function("four_points_inventory_reuse_with_duplicate", |bencher| {
        bencher.iter(|| {
            decode_grib_points(
                black_box(&fixture),
                black_box(&library),
                black_box(&points),
                Some(-9999.0),
            )
            .expect("configured GRIB fixture should decode")
        })
    });
    group.finish();
}

criterion_group!(benches, bench_grib);
criterion_main!(benches);
