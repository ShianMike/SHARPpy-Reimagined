use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};
use sharpmod_rs::batch_analysis::{
    BatchAnalysisExecutor, BatchExecutionOptions, BatchProfileInput, BOX_LAYER_TOPS_AGL,
    MAX_BATCH_THREADS,
};
use std::hint::black_box;
use std::time::Duration;

fn sounding(levels: usize, marker: f64) -> BatchProfileInput {
    let denominator = (levels - 1) as f64;
    let pressure: Vec<f64> = (0..levels)
        .map(|index| 1_020.0 - 920.0 * index as f64 / denominator)
        .collect();
    let height: Vec<f64> = (0..levels)
        .map(|index| 120.0 + 15_880.0 * index as f64 / denominator)
        .collect();
    let temperature: Vec<f64> = height
        .iter()
        .map(|value| 29.0 + marker - 6.4 * (value - height[0]) / 1_000.0)
        .collect();
    let dewpoint: Vec<f64> = temperature
        .iter()
        .enumerate()
        .map(|(index, value)| *value - (3.0 + 21.0 * index as f64 / denominator))
        .collect();
    let wind_direction: Vec<f64> = (0..levels)
        .map(|index| 155.0 + marker + 130.0 * index as f64 / denominator)
        .collect();
    let wind_speed: Vec<f64> = (0..levels)
        .map(|index| 8.0 + 64.0 * index as f64 / denominator)
        .collect();
    BatchProfileInput {
        pressure,
        height,
        temperature,
        dewpoint,
        wind_direction,
        wind_speed,
        surface_index: 0,
        missing: Some(-9_999.0),
    }
}

fn inputs(count: usize, levels: usize) -> Vec<BatchProfileInput> {
    (0..count)
        .map(|index| sounding(levels, index as f64 * 0.025))
        .collect()
}

fn bench_batch_analysis(c: &mut Criterion) {
    let mut group = c.benchmark_group("batch_analysis");
    group.sample_size(10);
    group.warm_up_time(Duration::from_secs(3));
    group.measurement_time(Duration::from_secs(10));

    for (profile_count, levels) in [(4, 32), (8, 32), (32, 32), (8, 128), (32, 128)] {
        let profiles = inputs(profile_count, levels);
        for worker_count in [1, 2, MAX_BATCH_THREADS] {
            let executor = BatchAnalysisExecutor::new(BatchExecutionOptions {
                max_threads: worker_count,
                parallel_threshold: 2,
            })
            .expect("benchmark executor should be valid");
            group.bench_with_input(
                BenchmarkId::new(
                    format!("workers_{worker_count}"),
                    format!("{profile_count}x{levels}"),
                ),
                &profiles,
                |bencher, profiles| {
                    bencher.iter(|| {
                        executor
                            .analyze(black_box(profiles), black_box(&BOX_LAYER_TOPS_AGL))
                            .expect("synthetic profiles should analyze")
                    })
                },
            );
        }
    }
    group.finish();
}

criterion_group!(benches, bench_batch_analysis);
criterion_main!(benches);
