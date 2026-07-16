use criterion::{criterion_group, criterion_main, Criterion};
use sharpmod_rs::{interpolation, records, wind};
use std::hint::black_box;

fn kernel_benchmarks(criterion: &mut Criterion) {
    let direction: Vec<f64> = (0..100_000)
        .map(|index| (index as f64 * 7.25) % 360.0)
        .collect();
    let speed: Vec<f64> = (0..100_000)
        .map(|index| 5.0 + (index % 80) as f64)
        .collect();
    let coordinate: Vec<f64> = (0..100_000).map(|index| index as f64).collect();
    let values: Vec<f64> = coordinate.iter().map(|value| value.sin()).collect();
    let targets: Vec<f64> = (0..99_999).map(|index| index as f64 + 0.5).collect();
    let pressure: Vec<f64> = (0..100_000)
        .map(|index| 1100.0 - (index as f64 * 0.01))
        .collect();

    criterion.bench_function("wind_to_components_100k", |bencher| {
        bencher.iter(|| {
            wind::wind_to_components(black_box(&direction), black_box(&speed), None).unwrap()
        })
    });
    criterion.bench_function("interpolate_1d_100k", |bencher| {
        bencher.iter(|| {
            interpolation::interpolate_1d(
                black_box(&targets),
                black_box(&coordinate),
                black_box(&values),
                None,
                false,
            )
            .unwrap()
        })
    });
    criterion.bench_function("pressure_sort_dedup_100k", |bencher| {
        bencher.iter(|| records::pressure_sort_dedup_indices(black_box(&pressure), Some(-9999.0)))
    });
}

criterion_group!(benches, kernel_benchmarks);
criterion_main!(benches);
