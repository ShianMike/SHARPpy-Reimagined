//! Bounded, deterministic batch execution for profile diagnostics.
//!
//! Box and ensemble analysis need the same native parcel, downdraft, and
//! kinematic products for many independent profiles.  This module keeps that
//! scheduling policy next to the Rust kernels: small batches stay serial,
//! larger batches use a bounded Rayon pool, and calls made from an existing
//! Rayon worker stay serial to avoid nested pools competing for the same CPUs.
//! The ordinary Python binding shares one process-wide default executor, so
//! simultaneous GUI callers still have one four-worker ceiling rather than one
//! pool each. `IndexedParallelIterator::collect` preserves input order; errors
//! are collected per input and resolved serially so the lowest failing index is
//! deterministic too.

use crate::{kinematics, parcels, wind};
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::sync::OnceLock;

/// Hard ceiling for native profile-analysis workers.
///
/// The GUI already performs extraction and orchestration on worker threads.
/// Four analysis workers leave headroom for those tasks and prevent a large
/// box from claiming every logical CPU on a workstation.
pub const MAX_BATCH_THREADS: usize = 4;

/// Batches smaller than this run serially by default.
pub const DEFAULT_PARALLEL_THRESHOLD: usize = 8;

/// Shared fixed-layer diagnostics requested by box analysis.
pub const BOX_LAYER_TOPS_AGL: [f64; 4] = [1_000.0, 3_000.0, 6_000.0, 8_000.0];

/// One owned profile snapshot, safe to evaluate after Python releases the GIL.
#[derive(Clone, Debug)]
pub struct BatchProfileInput {
    pub pressure: Vec<f64>,
    pub height: Vec<f64>,
    pub temperature: Vec<f64>,
    pub dewpoint: Vec<f64>,
    pub wind_direction: Vec<f64>,
    pub wind_speed: Vec<f64>,
    pub surface_index: usize,
    pub missing: Option<f64>,
}

impl BatchProfileInput {
    fn validate(&self) -> Result<(), String> {
        let expected = self.pressure.len();
        for (name, actual) in [
            ("height", self.height.len()),
            ("temperature", self.temperature.len()),
            ("dewpoint", self.dewpoint.len()),
            ("wind_direction", self.wind_direction.len()),
            ("wind_speed", self.wind_speed.len()),
        ] {
            if actual != expected {
                return Err(format!(
                    "profile column lengths differ: pressure={expected}, {name}={actual}"
                ));
            }
        }
        if (expected == 0 && self.surface_index != 0)
            || (expected > 0 && self.surface_index >= expected)
        {
            return Err(format!(
                "surface_index {} is outside a profile with {expected} levels",
                self.surface_index
            ));
        }
        Ok(())
    }
}

/// Dense native products needed by the box/ensemble fast tier.
///
/// Plotting traces are intentionally omitted.  A later PyO3 adapter can stack
/// these fixed-width rows into NumPy matrices without constructing Python
/// objects for each diagnostic.
#[derive(Clone, Debug)]
pub struct BatchProfileDiagnostics {
    pub parcels: [[f64; parcels::PARCEL_WIDTH]; parcels::PARCEL_COUNT],
    pub convective_parcels: [[f64; parcels::PARCEL_WIDTH]; parcels::CONVECTIVE_PARCEL_COUNT],
    pub effective_bottom_pressure: f64,
    pub effective_top_pressure: f64,
    /// `[dcape, source_pressure, downrush_temperature]`.
    pub downdraft: [f64; 3],
    pub storm_motion: [f64; 4],
    pub kinematic_layers: Vec<[f64; kinematics::LAYER_WIDTH]>,
}

/// Why a batch used its serial or parallel path.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BatchExecutionMode {
    /// Fewer inputs than the configured parallel cutoff.
    SerialBelowThreshold,
    /// The effective worker bound is one.
    SerialSingleThread,
    /// The caller is already executing on a Rayon worker.
    SerialNestedRayon,
    /// The dedicated bounded pool evaluated inputs in parallel.
    BoundedParallel,
}

/// Scheduler configuration supplied by a binding or native caller.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BatchExecutionOptions {
    pub max_threads: usize,
    pub parallel_threshold: usize,
}

impl Default for BatchExecutionOptions {
    fn default() -> Self {
        let available = std::thread::available_parallelism().map_or(1, |count| count.get());
        Self {
            max_threads: available.clamp(1, MAX_BATCH_THREADS),
            parallel_threshold: DEFAULT_PARALLEL_THRESHOLD,
        }
    }
}

/// Ordered output plus observable scheduling metadata.
#[derive(Debug)]
pub struct BatchExecution {
    pub diagnostics: Vec<BatchProfileDiagnostics>,
    pub mode: BatchExecutionMode,
    /// Threads configured for the path actually used (`1` for serial modes).
    pub worker_count: usize,
}

/// Reusable executor so repeated ensemble/box calls do not rebuild a pool.
pub struct BatchAnalysisExecutor {
    options: BatchExecutionOptions,
    worker_count: usize,
    pool: Option<ThreadPool>,
}

static DEFAULT_EXECUTOR: OnceLock<Result<BatchAnalysisExecutor, String>> = OnceLock::new();

/// Return the process-wide executor used by the default Python batch path.
///
/// Sharing this executor bounds simultaneous box and ensemble calls to one
/// native worker pool. Explicit non-default configurations remain isolated so
/// benchmarks and callers can evaluate smaller worker counts and cutoffs.
pub fn default_executor() -> Result<&'static BatchAnalysisExecutor, String> {
    match DEFAULT_EXECUTOR
        .get_or_init(|| BatchAnalysisExecutor::new(BatchExecutionOptions::default()))
    {
        Ok(executor) => Ok(executor),
        Err(error) => Err(error.clone()),
    }
}

impl BatchAnalysisExecutor {
    /// Create an executor with a dedicated pool bounded by
    /// [`MAX_BATCH_THREADS`].
    pub fn new(options: BatchExecutionOptions) -> Result<Self, String> {
        if options.max_threads == 0 {
            return Err("max_threads must be at least 1".to_string());
        }
        if options.parallel_threshold < 2 {
            return Err("parallel_threshold must be at least 2".to_string());
        }
        let worker_count = options.max_threads.min(MAX_BATCH_THREADS);
        let pool = if worker_count > 1 {
            Some(
                ThreadPoolBuilder::new()
                    .num_threads(worker_count)
                    .thread_name(|index| format!("sharpmod-batch-{index}"))
                    .build()
                    .map_err(|error| format!("could not create batch thread pool: {error}"))?,
            )
        } else {
            None
        };
        Ok(Self {
            options,
            worker_count,
            pool,
        })
    }

    /// Effective bounded worker count retained by this executor.
    pub fn max_threads(&self) -> usize {
        self.worker_count
    }

    /// Analyze profiles with stable ordering and deterministic error selection.
    pub fn analyze(
        &self,
        inputs: &[BatchProfileInput],
        layer_tops_agl: &[f64],
    ) -> Result<BatchExecution, String> {
        let mode = if inputs.len() < self.options.parallel_threshold {
            BatchExecutionMode::SerialBelowThreshold
        } else if self.pool.is_none() {
            BatchExecutionMode::SerialSingleThread
        } else if rayon::current_thread_index().is_some() {
            BatchExecutionMode::SerialNestedRayon
        } else {
            BatchExecutionMode::BoundedParallel
        };

        let staged: Vec<Result<BatchProfileDiagnostics, String>> = match mode {
            BatchExecutionMode::BoundedParallel => self.pool.as_ref().unwrap().install(|| {
                inputs
                    .par_iter()
                    .map(|input| analyze_profile(input, layer_tops_agl))
                    .collect()
            }),
            _ => inputs
                .iter()
                .map(|input| analyze_profile(input, layer_tops_agl))
                .collect(),
        };

        let mut diagnostics = Vec::with_capacity(staged.len());
        for (index, result) in staged.into_iter().enumerate() {
            diagnostics.push(result.map_err(|error| format!("profile {index}: {error}"))?);
        }
        Ok(BatchExecution {
            diagnostics,
            mode,
            worker_count: if mode == BatchExecutionMode::BoundedParallel {
                self.worker_count
            } else {
                1
            },
        })
    }
}

/// Evaluate one profile using the same kernels as the scalar backend API.
pub fn analyze_profile(
    input: &BatchProfileInput,
    layer_tops_agl: &[f64],
) -> Result<BatchProfileDiagnostics, String> {
    input.validate()?;
    let thermodynamics = parcels::profile_thermodynamic_diagnostics(
        &input.pressure,
        &input.height,
        &input.temperature,
        &input.dewpoint,
        input.surface_index,
        input.missing,
    )?;
    let (u, v) = wind::wind_to_components(&input.wind_direction, &input.wind_speed, input.missing)?;
    let kinematics = kinematics::profile_kinematics(
        &input.pressure,
        &input.height,
        &u,
        &v,
        layer_tops_agl,
        input.surface_index,
        input.missing,
    )?;
    let parcels::ProfileThermodynamicDiagnostics {
        parcels,
        convective_parcels,
        effective_bottom_pressure,
        effective_top_pressure,
        downdraft,
    } = thermodynamics;

    Ok(BatchProfileDiagnostics {
        parcels,
        convective_parcels,
        effective_bottom_pressure,
        effective_top_pressure,
        downdraft,
        storm_motion: kinematics.storm_motion,
        kinematic_layers: kinematics.layers,
    })
}
