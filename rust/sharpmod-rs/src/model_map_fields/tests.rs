use super::fixtures::{Provider, Server};
use super::*;
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};

#[pyfunction(signature=(payload,*,model="hrrr",field_index=0))]
fn fixture_decode<'py>(
    py: Python<'py>,
    payload: &Bound<'py, PyBytes>,
    model: &str,
    field_index: usize,
) -> Bound<'py, PyArray2<f32>> {
    let _ = (model, field_index);
    let value = f32::from(payload.as_bytes()[4]);
    Array2::from_shape_vec(
        (2, 2),
        vec![
            value,
            if value == 254. { f32::NAN } else { value },
            value,
            value,
        ],
    )
    .unwrap()
    .into_pyarray(py)
}

fn message(value: u8, size: usize) -> Vec<u8> {
    let mut bytes = vec![0; size];
    bytes[..4].copy_from_slice(b"GRIB");
    bytes[4] = value;
    bytes[size - 4..].copy_from_slice(b"7777");
    bytes
}

#[pyfunction(signature=(_payload,*,model="hrrr",field_index=0))]
fn failed_decode(_payload: &Bound<'_, PyBytes>, model: &str, field_index: usize) -> PyResult<()> {
    let _ = (model, field_index);
    Err(pyo3::exceptions::PyValueError::new_err(
        "fixture decode failure",
    ))
}

#[test]
fn pipeline_preserves_the_decode_error_when_it_closes_a_backpressured_channel() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    clear_model_field_cache();
    let mut body = Vec::new();
    let mut index = String::new();
    for (variable, level) in [
        ("TMP", "2 m above ground"),
        ("DPT", "2 m above ground"),
        ("PRES", "surface"),
        ("UGRD", "10 m above ground"),
        ("VGRD", "10 m above ground"),
    ] {
        index.push_str(&format!(
            "1:{}:d:{variable}:{level}:1 hour fcst:\n",
            body.len()
        ));
        body.extend_from_slice(&message(1, 24));
        index.push_str(&format!("2:{}:d:unused:surface:1 hour fcst:\n", body.len()));
        body.extend_from_slice(&vec![0; 131072]);
    }
    let server = Server::start(body, index.into_bytes());
    Python::attach(|py| -> PyResult<()> {
        let _provider = Provider::new(
            py,
            &server,
            Some(wrap_pyfunction!(failed_decode, py)?.into_any()),
        )?;
        let error = download_map_fields(
            py,
            &product(py, "thetae-2m")?,
            &run(py)?,
            1,
            5.,
            None,
            "hrrr".into(),
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("fixture decode failure"),
            "{error}"
        );
        Ok(())
    })
    .unwrap();
    clear_model_field_cache();
}

fn run(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    PyModule::import(py, "datetime")?
        .getattr("datetime")?
        .call_method1("fromisoformat", ("2026-11-17T00:00:00+00:00",))
}

#[test]
fn surface_and_pressure_aliases_share_one_revision_and_inventory_per_request() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    clear_model_field_cache();
    let server = Server::start(
        [message(1, 24), message(2, 24)].concat(),
        b"1:0:d:TMP:2 m above ground:1 hour fcst:\n2:24:d:TMP:500 mb:1 hour fcst:\n".to_vec(),
    );
    Python::attach(|py| -> PyResult<()> {
        let _provider = Provider::new(py, &server, None)?;
        let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
        let pressure = catalogue
            .getattr("FieldSpec")?
            .call1(("tmp500", "prs", "TMP", "500 mb"))?;
        let fields = pyo3::types::PyTuple::new(py, [catalogue.getattr("F_TMP2M")?, pressure])?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("fields", fields)?;
        let product = PyModule::import(py, "dataclasses")?
            .getattr("replace")?
            .call((product(py, "tmp-2m")?,), Some(&kwargs))?;
        let plan = plan::fields(py, &product, &run(py)?, 1, 5., "hrrr", None)?;
        assert_eq!(plan.fields.len(), 2);
        assert_eq!(plan.identities.len(), 1);
        assert_eq!(server.requests.load(Ordering::Relaxed), 2);
        Ok(())
    })
    .unwrap();
    clear_model_field_cache();
}

fn product<'py>(py: Python<'py>, key: &str) -> PyResult<Bound<'py, PyAny>> {
    PyModule::import(py, "sharpmod.providers.hrrr_products")?.call_method1("get_product", (key,))
}

fn value(py: Python<'_>, grids: &Py<PyDict>, name: &str) -> PyResult<f32> {
    Ok(grids
        .bind(py)
        .get_item(name)?
        .unwrap()
        .cast::<PyArray2<f32>>()?
        .readonly()
        .as_array()[[0, 0]])
}

#[test]
fn real_native_path_reuses_ingredients_without_aliasing_or_reusing_a_stale_inventory() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    clear_model_field_cache();
    let body = (1..=5).flat_map(|value| message(value, 24)).collect();
    let index=b"1:0:d:PRES:surface:1 hour fcst:\n2:24:d:TMP:2 m above ground:1 hour fcst:\n3:48:d:DPT:2 m above ground:1 hour fcst:\n4:72:d:UGRD:10 m above ground:1 hour fcst:\n5:96:d:VGRD:10 m above ground:1 hour fcst:\n".to_vec();
    let server = Server::start(body, index);
    Python::attach(|py| -> PyResult<()> {
        let _provider = Provider::new(
            py,
            &server,
            Some(wrap_pyfunction!(fixture_decode, py)?.into_any()),
        )?;
        let before = DECODED.load(Ordering::Relaxed);
        let first = download_map_fields(
            py,
            &product(py, "thetae-2m")?,
            &run(py)?,
            1,
            5.,
            None,
            "hrrr".into(),
        )?;
        assert_eq!(DECODED.load(Ordering::Relaxed) - before, 5);
        let array = first.bind(py).get_item("tmp2m")?.unwrap();
        array.cast::<PyArray2<f32>>()?.readwrite().as_array_mut()[[0, 0]] = 99.;
        let second = download_map_fields(
            py,
            &product(py, "tmp-2m")?,
            &run(py)?,
            1,
            5.,
            None,
            "hrrr".into(),
        )?;
        assert_eq!(value(py, &second, "tmp2m")?, 2.);
        assert_eq!(DECODED.load(Ordering::Relaxed) - before, 5);
        // A new representation moves TMP to another offset. Both the metadata
        // and decoded grid must change, even though the URL stays the same.
        server.replace(
            [message(8, 32), message(9, 24)].concat(),
            b"1:0:d:PRES:surface:1 hour fcst:\n2:32:d:TMP:2 m above ground:1 hour fcst:\n".to_vec(),
            "\"updated\"",
        );
        let changed = download_map_fields(
            py,
            &product(py, "tmp-2m")?,
            &run(py)?,
            1,
            5.,
            None,
            "hrrr".into(),
        )?;
        assert_eq!(value(py, &changed, "tmp2m")?, 9.);
        server.replace(
            message(7, 24),
            b"1:0:d:TMP:2 m above ground:1 hour fcst:\n".to_vec(),
            "W/\"weak\"",
        );
        let before = DECODED.load(Ordering::Relaxed);
        for _ in 0..2 {
            let output = download_map_fields(
                py,
                &product(py, "tmp-2m")?,
                &run(py)?,
                1,
                5.,
                None,
                "hrrr".into(),
            )?;
            assert_eq!(value(py, &output, "tmp2m")?, 7.);
        }
        assert_eq!(DECODED.load(Ordering::Relaxed) - before, 2);
        Ok(())
    })
    .unwrap();
    clear_model_field_cache();
}

static CANCEL: AtomicBool = AtomicBool::new(false);

#[test]
fn live_consumer_retries_a_shared_producer_that_disappears_before_completing() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    clear_model_field_cache();
    let server = Server::start(
        message(7, 24),
        b"1:0:d:TMP:2 m above ground:1 hour fcst:\n".to_vec(),
    );
    let provider = Python::attach(|py| {
        Provider::new(
            py,
            &server,
            Some(wrap_pyfunction!(fixture_decode, py).unwrap().into_any()),
        )
        .unwrap()
    });
    let owner = cache().reserve(Some(Key {
        url: format!("{}/fixture.grib2", server.url),
        identity: ObjectIdentity {
            etag: "\"fixed-hrrr-fixture\"".into(),
            length: 24,
        },
        offset: 0,
        length: 24,
        subfield: 0,
        model: "hrrr".into(),
    }));
    let before = SHARED.load(Ordering::Relaxed);
    std::thread::scope(|scope| {
        let follower = scope.spawn(|| {
            Python::attach(|py| {
                download_map_fields(
                    py,
                    &product(py, "tmp-2m")?,
                    &run(py)?,
                    1,
                    5.,
                    None,
                    "hrrr".into(),
                )
            })
        });
        let deadline = Instant::now() + Duration::from_secs(5);
        while SHARED.load(Ordering::Relaxed) == before && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(1));
        }
        assert!(SHARED.load(Ordering::Relaxed) > before);
        drop(owner);
        let output = follower.join().unwrap().unwrap();
        Python::attach(|py| assert_eq!(value(py, &output, "tmp2m").unwrap(), 7.));
    });
    drop(provider);
    clear_model_field_cache();
}
#[pyfunction]
fn cancelled_consumer() -> bool {
    CANCEL.load(Ordering::Acquire)
}

#[test]
fn cancelling_a_leader_does_not_abort_another_panel_waiting_on_the_same_field() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    clear_model_field_cache();
    CANCEL.store(false, Ordering::Release);
    let server = Server::start(
        message(7, 8 * 1024 * 1024),
        b"1:0:d:TMP:2 m above ground:1 hour fcst:\n".to_vec(),
    );
    let provider = Python::attach(|py| {
        Provider::new(
            py,
            &server,
            Some(wrap_pyfunction!(fixture_decode, py).unwrap().into_any()),
        )
        .unwrap()
    });
    let before = SHARED.load(Ordering::Relaxed);
    std::thread::scope(|scope| {
        let leader = scope.spawn(|| {
            Python::attach(|py| {
                download_map_fields(
                    py,
                    &product(py, "tmp-2m")?,
                    &run(py)?,
                    1,
                    5.,
                    Some(
                        wrap_pyfunction!(cancelled_consumer, py)?
                            .into_any()
                            .unbind(),
                    ),
                    "hrrr".into(),
                )
            })
        });
        let deadline = Instant::now() + Duration::from_secs(5);
        while server.active.load(Ordering::Relaxed) == 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(1));
        }
        let follower = scope.spawn(|| {
            Python::attach(|py| {
                download_map_fields(
                    py,
                    &product(py, "tmp-2m")?,
                    &run(py)?,
                    1,
                    5.,
                    None,
                    "hrrr".into(),
                )
            })
        });
        while SHARED.load(Ordering::Relaxed) == before && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(1));
        }
        assert!(SHARED.load(Ordering::Relaxed) > before);
        CANCEL.store(true, Ordering::Release);
        let first = leader.join().unwrap().unwrap_err();
        assert!(first.to_string().contains("cancelled"));
        let second = follower.join().unwrap().unwrap();
        Python::attach(|py| assert_eq!(value(py, &second, "tmp2m").unwrap(), 7.));
    });
    drop(provider);
    clear_model_field_cache();
}

#[test]
fn accumulation_and_nan_maxima_remain_correct_across_bounded_batches() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    clear_model_field_cache();
    let body = (1..=9).flat_map(|value| message(value, 24)).collect();
    let index = (1..=9)
        .map(|step| {
            format!(
                "{step}:{}:d:APCP:surface:{}-{} hour acc fcst:\n",
                (step - 1) * 24,
                (step - 1) * 3,
                step * 3
            )
        })
        .collect::<String>()
        .into_bytes();
    let server = Server::start(body, index);
    Python::attach(|py| -> PyResult<()> {
        let _provider = Provider::new(
            py,
            &server,
            Some(wrap_pyfunction!(fixture_decode, py)?.into_any()),
        )?;
        let sum = download_map_fields(
            py,
            &product(py, "apcp")?,
            &run(py)?,
            27,
            5.,
            None,
            "nam".into(),
        )?;
        assert_eq!(value(py, &sum, "apcp")?, 45.);
        let body = (1..=9)
            .flat_map(|value| message(if value == 4 { 254 } else { value }, 24))
            .collect();
        let index = (1..=9)
            .map(|step| {
                format!(
                    "{step}:{}:d:MXUPHL:3000-0 m above ground:{}-{step} hour max fcst:\n",
                    (step - 1) * 24,
                    step - 1
                )
            })
            .collect::<String>()
            .into_bytes();
        server.replace(body, index, "\"hourly-max\"");
        let kwargs = PyDict::new(py);
        kwargs.set_item("max_hours", 9)?;
        let maximum = PyModule::import(py, "dataclasses")?
            .getattr("replace")?
            .call((product(py, "uh-0-3km")?,), Some(&kwargs))?;
        let output = download_map_fields(py, &maximum, &run(py)?, 9, 5., None, "hrrr".into())?;
        assert_eq!(value(py, &output, "uh03")?, 254.);
        assert!(output
            .bind(py)
            .get_item("uh03")?
            .unwrap()
            .cast::<PyArray2<f32>>()?
            .readonly()
            .as_array()[[0, 1]]
        .is_nan());
        Ok(())
    })
    .unwrap();
    clear_model_field_cache();
}

#[test]
fn source_revision_subfield_and_model_are_part_of_shared_field_identity() {
    let key = Key {
        url: "https://aws/model".into(),
        identity: ObjectIdentity {
            etag: "\"a\"".into(),
            length: 123,
        },
        offset: 1,
        length: 12,
        subfield: 0,
        model: "hrrr".into(),
    };
    let mut different = key.clone();
    different.identity.etag = "\"b\"".into();
    assert!(key != different);
    different = key.clone();
    different.subfield = 1;
    assert!(key != different);
    different = key.clone();
    different.model = "rap".into();
    assert!(key != different);
    let cache = Cache::<Key, Grid>::new(32, 8);
    let owner = cache.reserve(Some(key.clone()));
    owner.complete(
        Ok(Grid {
            shape: (1, 2),
            values: vec![1., 2.],
        }),
        8,
    );
    let follower = cache.reserve(Some(key));
    let value = follower.wait(|| Ok(false)).unwrap();
    let mut output = value.values.clone();
    output[0] = 99.;
    assert_eq!(value.values[0], 1.);
}

#[test]
fn direct_native_calls_reject_incomplete_hour_windows_without_unsigned_underflow() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let products = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
        let product = products.call_method1("get_product", ("uh-0-3km",))?;
        let error = match plan::fields(py, &product, py.None().bind(py), 0, 30., "hrrr", None) {
            Err(error) => error,
            Ok(_) => panic!("an incomplete maximum window was accepted"),
        };
        assert!(error.to_string().contains("complete forecast hours"));
        let precipitation = products.call_method1("get_product", ("apcp",))?;
        assert!(plan::fields(
            py,
            &precipitation,
            py.None().bind(py),
            u32::MAX,
            30.,
            "nam",
            None
        )
        .is_err());
        assert!(plan::fields(py, &precipitation, py.None().bind(py), 0, 30., "nam", None).is_err());
        Ok(())
    })
    .unwrap();
}
