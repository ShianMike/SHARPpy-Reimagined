use criterion::{criterion_group, criterion_main, Criterion};
use sharpmod_rs::{interpolation, kinematics, parcels, records, wind};
use std::hint::black_box;

type Sounding = (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>);

fn sounding(levels: usize) -> Sounding {
    let denominator = (levels - 1) as f64;
    let pressure: Vec<f64> = (0..levels)
        .map(|index| 1_020.0 - 920.0 * index as f64 / denominator)
        .collect();
    let height: Vec<f64> = (0..levels)
        .map(|index| 120.0 + 15_880.0 * index as f64 / denominator)
        .collect();
    let temperature: Vec<f64> = height
        .iter()
        .map(|value| 29.0 - 6.4 * (value - height[0]) / 1_000.0)
        .collect();
    let dewpoint: Vec<f64> = temperature
        .iter()
        .enumerate()
        .map(|(index, value)| *value - (3.0 + 21.0 * index as f64 / denominator))
        .collect();
    let wind_direction: Vec<f64> = (0..levels)
        .map(|index| 155.0 + 130.0 * index as f64 / denominator)
        .collect();
    let wind_speed: Vec<f64> = (0..levels)
        .map(|index| 8.0 + 64.0 * index as f64 / denominator)
        .collect();
    let (u, v) = wind::wind_to_components(&wind_direction, &wind_speed, None).unwrap();
    (pressure, height, temperature, dewpoint, u, v)
}

fn sounding_benchmarks(criterion: &mut Criterion) {
    let layer_tops = [500.0, 1_000.0, 3_000.0, 4_000.0, 6_000.0];
    for levels in [32_usize, 128] {
        let (pressure, height, temperature, dewpoint, u, v) = sounding(levels);
        let log_pressure: Vec<f64> = pressure.iter().map(|value| value.log10()).collect();
        let target = [700.0_f64.log10()];

        criterion.bench_function(&format!("interpolate_scalar_{levels}"), |bencher| {
            bencher.iter(|| {
                interpolation::interpolate_1d(
                    black_box(&target),
                    black_box(&log_pressure),
                    black_box(&temperature),
                    None,
                    false,
                )
                .unwrap()
            })
        });
        criterion.bench_function(&format!("profile_kinematics_{levels}"), |bencher| {
            bencher.iter(|| {
                kinematics::profile_kinematics(
                    black_box(&pressure),
                    black_box(&height),
                    black_box(&u),
                    black_box(&v),
                    black_box(&layer_tops),
                    0,
                    None,
                )
                .unwrap()
            })
        });
        criterion.bench_function(&format!("parcel_diagnostics_{levels}"), |bencher| {
            bencher.iter(|| {
                parcels::profile_parcels(
                    black_box(&pressure),
                    black_box(&height),
                    black_box(&temperature),
                    black_box(&dewpoint),
                    0,
                    None,
                )
                .unwrap()
            })
        });
        // The public convective workspace is the pre-optimization proxy for
        // the private effective-layer search. It intentionally captures the
        // full traced path whose internal search is optimized by this work.
        criterion.bench_function(
            &format!("effective_layer_search_traced_{levels}"),
            |bencher| {
                bencher.iter(|| {
                    parcels::profile_convective_parcels(
                        black_box(&pressure),
                        black_box(&height),
                        black_box(&temperature),
                        black_box(&dewpoint),
                        0,
                        None,
                    )
                    .unwrap()
                })
            },
        );
        criterion.bench_function(&format!("dcape_{levels}"), |bencher| {
            bencher.iter(|| {
                parcels::profile_dcape(
                    black_box(&pressure),
                    black_box(&height),
                    black_box(&temperature),
                    black_box(&dewpoint),
                    0,
                    None,
                )
                .unwrap()
            })
        });
    }

    let (pressure, height, temperature, dewpoint, u, v) = sounding(128);
    let log_pressure: Vec<f64> = pressure.iter().map(|value| value.log10()).collect();
    let omega: Vec<f64> = (0..pressure.len())
        .map(|index| {
            -0.12 * (std::f64::consts::PI * index as f64 / (pressure.len() - 1) as f64).sin()
        })
        .collect();
    let target = [700.0_f64.log10()];
    let fields = [&height, &temperature, &dewpoint, &u, &v, &omega];
    criterion.bench_function("interpolate_scalar_six_fields_128", |bencher| {
        bencher.iter(|| {
            fields
                .iter()
                .map(|field| {
                    interpolation::interpolate_1d(
                        black_box(&target),
                        black_box(&log_pressure),
                        black_box(field.as_slice()),
                        None,
                        false,
                    )
                    .unwrap()[0]
                })
                .collect::<Vec<_>>()
        })
    });
}

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

criterion_group!(benches, kernel_benchmarks, sounding_benchmarks);
criterion_main!(benches);
