use super::*;
use pyo3::{
    ffi::c_str,
    types::{PyList, PyTuple},
};
use std::{
    fs,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT: AtomicU64 = AtomicU64::new(0);

fn fixture(py: Python<'_>) -> PyResult<Bound<'_, PyModule>> {
    PyModule::from_code(
        py,
        c_str!(
            r#"
from datetime import datetime, timezone
from types import SimpleNamespace
import numpy as np
from sharpmod.providers import observations as obs

when = datetime(2026, 11, 17, tzinfo=timezone.utc)
class Provider:
    def __init__(self, key, result=None, error=None):
        self.info = obs.ObservedProviderInfo(key, key, "https://example.invalid")
        self.result, self.error, self.calls = result, error, 0
    def fetch(self, station, time):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

def sounding(pressure=(1000., 900., 800.)):
    profile = SimpleNamespace(
        pres=np.array(pressure), hght=np.array([100., 1000., 2000.]),
        tmpc=np.array([15., 5., -5.]), dwpc=np.ma.array([10., 0., -10.], mask=[0,0,1]),
        wdir=np.array([180., 200., 220.]), wspd=np.array([10., 20., 30.]),
        omeg=None, meta={})
    return obs.ObservedSounding(profile, "iem", "IEM", "KOUN", "72357", when,
        "https://example.invalid", {"lat":35.2, "lon":-97.4, "valid":when,
        "station_name":"Norman", "nested":{"count":np.int64(3)}})
"#
        ),
        c_str!("observed_native_test.py"),
        c_str!("observed_native_test"),
    )
}

#[test]
fn observed_selection_retries_bad_downloaded_profiles_and_preserves_one_provider() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = fixture(py)?;
        let invalid = fixture.call_method1("sounding", ((1000., 1000., 800.),))?;
        let good = fixture.call_method0("sounding")?;
        let bad_provider = fixture.call_method1("Provider", ("uwyo", &invalid))?;
        let good_provider = fixture.call_method1("Provider", ("iem", &good))?;
        let providers = PyTuple::new(py, [&bad_provider, &good_provider])?;
        let result = fetch::fetch_observed(
            py,
            "72357".into_pyobject(py)?.as_any(),
            &fixture.getattr("when")?,
            providers.as_any(),
        )?;
        assert_eq!(result.getattr("provider")?.extract::<String>()?, "iem");
        assert!(result.getattr("profile")?.is(&good.getattr("profile")?));
        let attempts = result.getattr("metadata")?.get_item("fallback_attempts")?;
        assert_eq!(attempts.len()?, 1);
        assert_eq!(
            attempts
                .get_item(0)?
                .get_item("error_type")?
                .extract::<String>()?,
            "ObservedParseError"
        );
        assert!(attempts
            .get_item(0)?
            .get_item("error")?
            .extract::<String>()?
            .contains("pressure_not_strictly_decreasing"));
        for provider in [&bad_provider, &good_provider] {
            assert_eq!(provider.getattr("calls")?.extract::<u32>()?, 1);
        }
        assert!(!good.getattr("metadata")?.contains("fallback_attempts")?);
        Ok(())
    })
    .unwrap();
}

#[test]
fn malformed_observed_arrays_fall_back_and_all_failures_remain_structured() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = fixture(py)?;
        let invalid = fixture.call_method0("sounding")?;
        invalid.getattr("profile")?.setattr("pres", vec![1000.])?;
        let bad = fixture.call_method1("Provider", ("uwyo", &invalid))?;
        let station = "72357".into_pyobject(py)?;
        let when = fixture.getattr("when")?;
        let providers = PyList::new(py, [&bad])?;
        let error =
            fetch::fetch_observed(py, station.as_any(), &when, providers.as_any()).unwrap_err();
        assert!(error.is_instance(
            py,
            &PyModule::import(py, "sharpmod.providers.observations")?
                .getattr("ObservedFallbackError")?
        ));
        let attempts = error.value(py).getattr("attempts")?;
        assert_eq!(
            attempts
                .get_item(0)?
                .get_item("error_type")?
                .extract::<String>()?,
            "ObservedParseError"
        );
        Ok(())
    })
    .unwrap();
}

#[test]
fn invalid_provider_metadata_and_optional_arrays_fall_back_before_writing() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = fixture(py)?;
        let providers = PyList::empty(py);
        for (name, value) in [("lat", 91.), ("lon", f64::NAN)] {
            let invalid = fixture.call_method0("sounding")?;
            invalid.getattr("metadata")?.set_item(name, value)?;
            providers.append(fixture.call_method1("Provider", (name, invalid))?)?;
        }
        let invalid = fixture.call_method0("sounding")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("valid", "invalid provider timestamp")?;
        let invalid = PyModule::import(py, "dataclasses")?.call_method(
            "replace",
            (invalid,),
            Some(&kwargs),
        )?;
        providers.append(fixture.call_method1("Provider", ("timestamp", invalid))?)?;
        for omega in [vec![1., 2.], vec![f64::INFINITY]] {
            let invalid = fixture.call_method0("sounding")?;
            invalid.getattr("profile")?.setattr("omeg", omega)?;
            providers.append(fixture.call_method1("Provider", ("omega", invalid))?)?;
        }
        let good = fixture.call_method0("sounding")?;
        // Missing optional omega remains valid; no measurements are invented.
        good.getattr("profile")?.delattr("omeg")?;
        providers.append(fixture.call_method1("Provider", ("iem", &good))?)?;
        let result = fetch::fetch_observed(
            py,
            "72357".into_pyobject(py)?.as_any(),
            &fixture.getattr("when")?,
            providers.as_any(),
        )?;
        assert!(result.getattr("profile")?.is(&good.getattr("profile")?));
        let attempts = result.getattr("metadata")?.get_item("fallback_attempts")?;
        assert_eq!(attempts.len()?, 5);
        for attempt in attempts.try_iter()? {
            assert_eq!(
                attempt?.get_item("error_type")?.extract::<String>()?,
                "ObservedParseError"
            );
        }
        Ok(())
    })
    .unwrap();
}

#[test]
fn native_observed_output_preserves_masks_measurements_and_nested_provenance() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let root = std::env::temp_dir().join(format!(
        "observed-write-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let fixture = fixture(py)?;
        let result = fixture.call_method0("sounding")?;
        let path = root.join("observed.npz");
        write::write_observed_npz(
            py,
            &result,
            path.clone().into_pyobject(py)?.as_any(),
            &py.None().into_bound(py),
        )?;
        let archive = PyModule::import(py, "numpy")?.call_method1("load", (&path,))?;
        assert_eq!(
            archive
                .get_item("pres")?
                .call_method0("tolist")?
                .extract::<Vec<f64>>()?,
            [1000., 900., 800.]
        );
        assert_eq!(
            archive
                .get_item("dwpc")?
                .call_method0("tolist")?
                .extract::<Vec<f64>>()?,
            [10., 0., MISSING]
        );
        archive.call_method0("close")?;
        let sidecar: serde_json::Value =
            serde_json::from_slice(&fs::read(path.with_extension("json")).unwrap()).unwrap();
        assert_eq!(sidecar["nested"]["count"], 3);
        assert_eq!(sidecar["valid"], "2026-11-17T00:00:00Z");
        assert_eq!(sidecar["source_station"], "KOUN");
        assert_eq!(
            result
                .getattr("profile")?
                .getattr("dwpc")?
                .getattr("mask")?
                .call_method0("tolist")?
                .extract::<Vec<bool>>()?,
            [false, false, true]
        );
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn observed_open_rolls_back_reserved_files_after_writer_decoder_or_registration_failure() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let root = std::env::temp_dir().join(format!(
        "observed-open-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let fixture = fixture(py)?;
        let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
        cache.call_method1("reset_cache", (&root,))?;
        let result = fixture.call_method0("sounding")?;
        let mocking = PyModule::import(py, "unittest.mock")?;
        let returns = PyDict::new(py);
        returns.set_item("return_value", &result)?;
        let source = mocking.getattr("patch")?.call(
            ("sharpmod.providers.observations.fetch_observed",),
            Some(&returns),
        )?;
        source.call_method0("__enter__")?;
        let before = fs::read_dir(&root).unwrap().count();
        let params = PyDict::new(py);
        params.set_item("station_id", "72357")?;
        params.set_item("time", "2026-11-17T00:00:00Z")?;
        for target in [
            "sharpmod.providers.observations.write_observed_npz",
            "sharpmod.bridge.sounding_summary.decode",
            "sharpmod.bridge.sounding_summary.register",
        ] {
            let options = PyDict::new(py);
            options.set_item(
                "side_effect",
                py.get_type::<pyo3::exceptions::PyValueError>()
                    .call1(("injected failure",))?,
            )?;
            let patch = mocking.getattr("patch")?.call((target,), Some(&options))?;
            patch.call_method0("__enter__")?;
            let error = open::open_observed_sounding(py, &params).unwrap_err();
            if target.ends_with("write_observed_npz") {
                assert_eq!(
                    error.value(py).getattr("code")?.extract::<String>()?,
                    "SOUNDING_INVALID"
                );
            } else {
                assert!(
                    error.to_string().contains("injected failure"),
                    "{target}: {error}"
                );
            }
            patch.call_method1("__exit__", (py.None(), py.None(), py.None()))?;
            assert_eq!(
                fs::read_dir(&root).unwrap().count(),
                before,
                "{target} left files"
            );
        }
        source.call_method1("__exit__", (py.None(), py.None(), py.None()))?;
        cache.call_method1("reset_cache", (py.None(),))?;
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(root).unwrap();
}
