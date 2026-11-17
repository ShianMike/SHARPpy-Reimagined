//! Point-subset routing uses observed work and the existing NOMADS queue.
use pyo3::{
    prelude::*,
    types::{PyDict, PyTuple},
};
use std::{
    cell::Cell,
    collections::HashMap,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant},
};

#[derive(Default)]
struct Slots(Mutex<Option<Instant>>);

impl Slots {
    fn remaining(&self, interval: Duration) -> Duration {
        self.0.lock().unwrap().map_or(Duration::ZERO, |last| {
            interval.saturating_sub(last.elapsed())
        })
    }

    fn acquire(
        &self,
        interval: Duration,
        cancelled: impl Fn() -> Result<bool, String>,
    ) -> Result<Instant, sharpmod_weather::model_http::Error> {
        loop {
            if cancelled().map_err(sharpmod_weather::model_http::Error::Invalid)? {
                return Err(sharpmod_weather::model_http::Error::Cancelled);
            }
            let remaining = {
                let mut last = self.0.lock().unwrap();
                let now = Instant::now();
                let remaining = last.map_or(Duration::ZERO, |last| {
                    interval.saturating_sub(now.duration_since(last))
                });
                if remaining.is_zero() {
                    *last = Some(now);
                    return Ok(now);
                }
                remaining
            };
            std::thread::sleep(remaining.min(Duration::from_millis(50)));
        }
    }
}

fn slots() -> &'static Slots {
    static SLOTS: OnceLock<Slots> = OnceLock::new();
    SLOTS.get_or_init(Slots::default)
}

thread_local! { static REQUEST_STARTED: Cell<Option<Instant>> = const { Cell::new(None) }; }

#[pyfunction(signature=(cancelled=None))]
fn wait_model_point_slot(py: Python<'_>, cancelled: Option<Py<PyAny>>) -> PyResult<()> {
    let acquired = py
        .detach(|| {
            slots().acquire(Duration::from_secs(10), || {
                crate::model_http_bindings::cancelled(cancelled.as_ref())
            })
        })
        .map_err(|error| crate::model_http_bindings::field_error(py, error))?;
    REQUEST_STARTED.with(|started| started.set(Some(acquired)));
    Ok(())
}

#[derive(Default)]
struct Subsets {
    times: Mutex<HashMap<String, (usize, f64)>>,
}
impl Subsets {
    fn observe(&self, model: String, seconds: f64) {
        let mut times = self.times.lock().unwrap();
        if !times.contains_key(&model) && times.len() >= 64 {
            times.clear();
        }
        let value = times.entry(model).or_default();
        value.1 = if value.0 == 0 {
            seconds
        } else {
            0.75 * value.1 + 0.25 * seconds
        };
        value.0 = value.0.saturating_add(1);
    }
    fn seconds(&self, model: &str) -> Option<f64> {
        self.times
            .lock()
            .unwrap()
            .get(model)
            .filter(|value| value.0 >= 2)
            .map(|value| value.1)
    }
}
fn subsets() -> &'static Subsets {
    static SUBSETS: OnceLock<Subsets> = OnceLock::new();
    SUBSETS.get_or_init(Subsets::default)
}

fn prefer(bytes: u64, queue: f64, subset: Option<f64>, direct: Option<f64>) -> bool {
    if bytes == 0 {
        return true;
    }
    match (subset, direct) {
        (Some(subset), Some(direct)) => queue + subset <= direct * 1.1,
        (None, Some(direct)) if queue > direct * 1.25 => false,
        _ => bytes > 32 * 1024 * 1024,
    }
}

#[pyfunction(signature=(expected_bytes,model="",source_url=None))]
fn prefer_model_point_subset(
    expected_bytes: u64,
    model: &str,
    source_url: Option<String>,
) -> PyResult<bool> {
    let queue = slots().remaining(Duration::from_secs(10)).as_secs_f64();
    let policy = sharpmod_weather::transfer_policy::shared();
    let direct = source_url
        .and_then(|url| policy.seconds(&url, expected_bytes))
        .map(|network| network + policy.decode_seconds(expected_bytes).unwrap_or(0.));
    Ok(prefer(
        expected_bytes,
        queue,
        subsets().seconds(model),
        direct,
    ))
}

#[pyclass]
struct ModelPointSubset {
    fetch: Py<PyAny>,
}
#[pymethods]
impl ModelPointSubset {
    #[new]
    fn new(fetch: Py<PyAny>) -> Self {
        Self { fetch }
    }
    #[pyo3(signature=(*args,**kwargs))]
    fn __call__<'py>(
        &self,
        py: Python<'py>,
        args: &Bound<'py, PyTuple>,
        kwargs: Option<&Bound<'py, PyDict>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        REQUEST_STARTED.with(|started| started.set(None));
        let result = self.fetch.bind(py).call(args, kwargs)?;
        if let Some(started) = REQUEST_STARTED.with(Cell::take) {
            let model: String = args.get_item(1)?.getattr("key")?.extract()?;
            subsets().observe(model, started.elapsed().as_secs_f64());
        }
        Ok(result)
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(wait_model_point_slot, module)?)?;
    module.add_function(wrap_pyfunction!(prefer_model_point_subset, module)?)?;
    module.add_class::<ModelPointSubset>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn queue_and_real_completion_cost_override_the_initial_size_heuristic() {
        let large = 128 * 1024 * 1024;
        assert!(prefer(large, 0., None, None));
        assert!(!prefer(large, 9., Some(1.), Some(3.)));
        assert!(prefer(large, 0., Some(1.), Some(3.)));
        assert!(!prefer(large, 9., None, Some(3.)));
        assert!(prefer(0, 9., Some(1.), Some(3.)));
        assert!(!prefer(1024, 0., None, None));
    }
    #[test]
    fn warm_cached_subsets_and_single_samples_do_not_claim_network_cost() {
        let history = Subsets::default();
        history.observe("gfs".into(), 2.);
        assert!(history.seconds("gfs").is_none());
        history.observe("gfs".into(), 4.);
        assert_eq!(history.seconds("gfs"), Some(2.5));
        assert!(history.seconds("hrrr").is_none());
    }

    #[test]
    fn concurrent_point_requests_keep_spacing_and_can_cancel_without_taking_a_slot() {
        let slots = Slots::default();
        let interval = Duration::from_millis(40);
        let first = slots.acquire(interval, || Ok(false)).unwrap();
        assert!(!slots.remaining(interval).is_zero());
        assert_eq!(
            slots.acquire(interval, || Ok(true)),
            Err(sharpmod_weather::model_http::Error::Cancelled)
        );
        std::thread::scope(|scope| {
            let a = scope.spawn(|| slots.acquire(interval, || Ok(false)).unwrap());
            let b = scope.spawn(|| slots.acquire(interval, || Ok(false)).unwrap());
            let mut times = [first, a.join().unwrap(), b.join().unwrap()];
            times.sort();
            assert!(times
                .windows(2)
                .all(|pair| pair[1].duration_since(pair[0]) >= interval));
        });
    }
}
