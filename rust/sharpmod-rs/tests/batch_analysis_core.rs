use sharpmod_rs::batch_analysis::{
    analyze_profile, default_executor, BatchAnalysisExecutor, BatchExecutionMode,
    BatchExecutionOptions, BatchProfileDiagnostics, BatchProfileInput, BOX_LAYER_TOPS_AGL,
    MAX_BATCH_THREADS,
};

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

fn assert_same_float(left: f64, right: f64) {
    assert!(
        (left.is_nan() && right.is_nan()) || left.to_bits() == right.to_bits(),
        "float mismatch: {left:?} != {right:?}"
    );
}

fn assert_same_output(left: &BatchProfileDiagnostics, right: &BatchProfileDiagnostics) {
    for (left_row, right_row) in left.parcels.iter().zip(&right.parcels) {
        for (&left_value, &right_value) in left_row.iter().zip(right_row) {
            assert_same_float(left_value, right_value);
        }
    }
    for (left_row, right_row) in left
        .convective_parcels
        .iter()
        .zip(&right.convective_parcels)
    {
        for (&left_value, &right_value) in left_row.iter().zip(right_row) {
            assert_same_float(left_value, right_value);
        }
    }
    assert_same_float(
        left.effective_bottom_pressure,
        right.effective_bottom_pressure,
    );
    assert_same_float(left.effective_top_pressure, right.effective_top_pressure);
    for (&left_value, &right_value) in left.downdraft.iter().zip(&right.downdraft) {
        assert_same_float(left_value, right_value);
    }
    for (&left_value, &right_value) in left.storm_motion.iter().zip(&right.storm_motion) {
        assert_same_float(left_value, right_value);
    }
    assert_eq!(left.kinematic_layers.len(), right.kinematic_layers.len());
    for (left_row, right_row) in left.kinematic_layers.iter().zip(&right.kinematic_layers) {
        for (&left_value, &right_value) in left_row.iter().zip(right_row) {
            assert_same_float(left_value, right_value);
        }
    }
}

#[test]
fn bounded_parallel_matches_serial_bit_for_bit_and_keeps_order() {
    let inputs: Vec<_> = (0..12)
        .map(|index| sounding(64, index as f64 * 0.05))
        .collect();
    let serial = BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 1,
        parallel_threshold: 2,
    })
    .unwrap()
    .analyze(&inputs, &BOX_LAYER_TOPS_AGL)
    .unwrap();
    let parallel = BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 2,
        parallel_threshold: 2,
    })
    .unwrap()
    .analyze(&inputs, &BOX_LAYER_TOPS_AGL)
    .unwrap();

    assert_eq!(serial.mode, BatchExecutionMode::SerialSingleThread);
    assert_eq!(parallel.mode, BatchExecutionMode::BoundedParallel);
    assert_eq!(parallel.worker_count, 2);
    assert_eq!(serial.diagnostics.len(), inputs.len());
    assert_eq!(parallel.diagnostics.len(), inputs.len());
    for ((input, serial_row), parallel_row) in inputs
        .iter()
        .zip(&serial.diagnostics)
        .zip(&parallel.diagnostics)
    {
        assert_same_float(parallel_row.parcels[0][2], input.temperature[0]);
        assert_same_output(serial_row, parallel_row);
    }
}

#[test]
fn small_batches_take_the_serial_cutoff_path() {
    let inputs: Vec<_> = (0..4).map(|index| sounding(32, index as f64)).collect();
    let execution = BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 4,
        parallel_threshold: 8,
    })
    .unwrap()
    .analyze(&inputs, &BOX_LAYER_TOPS_AGL)
    .unwrap();

    assert_eq!(execution.mode, BatchExecutionMode::SerialBelowThreshold);
    assert_eq!(execution.worker_count, 1);
}

#[test]
fn nested_rayon_call_stays_serial() {
    let inputs: Vec<_> = (0..8).map(|index| sounding(32, index as f64)).collect();
    let executor = BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 4,
        parallel_threshold: 2,
    })
    .unwrap();
    let outer_pool = rayon::ThreadPoolBuilder::new()
        .num_threads(2)
        .build()
        .unwrap();
    let execution = outer_pool
        .install(|| executor.analyze(&inputs, &BOX_LAYER_TOPS_AGL))
        .unwrap();

    assert_eq!(execution.mode, BatchExecutionMode::SerialNestedRayon);
    assert_eq!(execution.worker_count, 1);
}

#[test]
fn first_error_is_selected_by_input_index_in_parallel_mode() {
    let mut inputs: Vec<_> = (0..10).map(|index| sounding(32, index as f64)).collect();
    inputs[2].height.pop();
    inputs[7].wind_speed.pop();
    let error = BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 4,
        parallel_threshold: 2,
    })
    .unwrap()
    .analyze(&inputs, &BOX_LAYER_TOPS_AGL)
    .unwrap_err();

    assert!(error.starts_with("profile 2: profile column lengths differ"));
}

#[test]
fn worker_configuration_is_bounded_and_rejects_ambiguous_values() {
    let bounded = BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: usize::MAX,
        parallel_threshold: 2,
    })
    .unwrap();
    assert_eq!(bounded.max_threads(), MAX_BATCH_THREADS);

    assert!(BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 0,
        parallel_threshold: 2,
    })
    .is_err());
    assert!(BatchAnalysisExecutor::new(BatchExecutionOptions {
        max_threads: 2,
        parallel_threshold: 1,
    })
    .is_err());
}

#[test]
fn default_executor_is_process_wide_and_supports_concurrent_callers() {
    let first = default_executor().unwrap();
    let second = default_executor().unwrap();
    assert!(std::ptr::eq(first, second));
    assert!(first.max_threads() <= MAX_BATCH_THREADS);

    let callers: Vec<_> = (0..2)
        .map(|caller| {
            std::thread::spawn(move || {
                let inputs: Vec<_> = (0..8)
                    .map(|index| sounding(32, (caller * 8 + index) as f64 * 0.01))
                    .collect();
                default_executor()
                    .unwrap()
                    .analyze(&inputs, &BOX_LAYER_TOPS_AGL)
                    .unwrap()
            })
        })
        .collect();
    for caller in callers {
        let execution = caller.join().unwrap();
        assert_eq!(execution.mode, BatchExecutionMode::BoundedParallel);
        assert!(execution.worker_count <= MAX_BATCH_THREADS);
        assert_eq!(execution.diagnostics.len(), 8);
    }
}

#[test]
fn one_profile_matches_direct_scalar_analysis() {
    let input = sounding(128, 0.0);
    let direct = analyze_profile(&input, &BOX_LAYER_TOPS_AGL).unwrap();
    let execution = BatchAnalysisExecutor::new(BatchExecutionOptions::default())
        .unwrap()
        .analyze(std::slice::from_ref(&input), &BOX_LAYER_TOPS_AGL)
        .unwrap();

    assert_eq!(execution.mode, BatchExecutionMode::SerialBelowThreshold);
    assert_same_output(&direct, &execution.diagnostics[0]);
}
