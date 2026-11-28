//! Canadian map transfers through the shared native HTTP client and GRIB runtime.

use crate::{
    gdps_fields::{
        self, Frame, Model, Record, HEIGHT, MAX_FIELD_BYTES, MAX_INVENTORY_BYTES, SPACING, WIDTH,
    },
    grib::gdps_grid,
    model_http_bindings::{cancelled, field_error},
};
use numpy::{ndarray::Array2, IntoPyArray};
use pyo3::{prelude::*, types::PyDict};
use sharpmod_weather::{
    map_fields::{fetch_model_fields, FieldJob},
    model_http::{Error, ModelClient},
};
use std::path::Path;
use std::time::Duration;
use time::OffsetDateTime;

fn records(product: &Bound<'_, PyAny>, model: Model) -> PyResult<Vec<(String, Record)>> {
    product
        .getattr("all_fields")?
        .try_iter()?
        .map(|field| {
            let name: String = field?.getattr("name")?.extract()?;
            let record = match model {
                Model::Gdps => gdps_fields::record(&name),
                Model::Rdps => gdps_fields::rdps_record(&name),
                Model::Hrdps => gdps_fields::hrdps_record(&name),
            }
            .map_err(|error| field_error(product.py(), error))?;
            Ok((name, record))
        })
        .collect()
}

struct RequiredRecords {
    current: Vec<Record>,
    earlier: Vec<Record>,
}

fn available_records_for_frame(
    entries: Vec<(String, Record)>,
    frame: Frame,
) -> Option<RequiredRecords> {
    let intermediate =
        matches!(frame.model, Model::Gdps | Model::Rdps) && !frame.hour.is_multiple_of(6);
    let mut required = RequiredRecords {
        current: Vec::with_capacity(entries.len()),
        earlier: Vec::new(),
    };
    for (name, record) in entries {
        if intermediate {
            if let Some(total) = gdps_fields::run_total_counterpart(&name) {
                if frame.hour < 6 {
                    return None;
                }
                required.current.push(total);
                required.earlier.push(total);
                continue;
            }
        }
        required.current.push(record);
    }
    Some(required)
}

async fn available_from_listings(
    requested: &[Option<RequiredRecords>],
    frame: Frame,
    timeout: f64,
    fetch: impl AsyncFn(Frame) -> Result<Vec<u8>, Error>,
) -> Result<Vec<bool>, Error> {
    let needs_earlier = requested
        .iter()
        .flatten()
        .any(|records| !records.earlier.is_empty());
    // Fetch both listings concurrently. Give the optional listing one request
    // budget even if the HTTP client retries, and hide only its dependents.
    let (listing, earlier_inventory) = tokio::try_join!(fetch(frame), async {
        if !needs_earlier {
            return Ok(None);
        }
        let Ok(budget) = Duration::try_from_secs_f64(timeout) else {
            // The required listing reports an invalid timeout.
            return Ok(None);
        };
        let earlier = Frame {
            hour: frame.hour - 6,
            ..frame
        };
        match tokio::time::timeout(budget, fetch(earlier)).await {
            Ok(Ok(bytes)) => Ok(std::str::from_utf8(&bytes)
                .ok()
                .map(|text| earlier.inventory(text))),
            Ok(Err(Error::Cancelled)) => Err(Error::Cancelled),
            _ => Ok(None),
        }
    })?;
    let text = std::str::from_utf8(&listing)
        .map_err(|_| Error::Invalid("Invalid Canadian model file listing".into()))?;
    let inventory = frame.inventory(text);
    // Earlier records use the same model's stems. The listing parser has
    // already matched them to the earlier run and hour.
    Ok(requested
        .iter()
        .map(|records| {
            records.as_ref().is_some_and(|records| {
                frame.has_records(&inventory, &records.current)
                    && (records.earlier.is_empty()
                        || earlier_inventory.as_ref().is_some_and(|inventory| {
                            frame.has_records(inventory, &records.earlier)
                        }))
            })
        })
        .collect())
}

#[pyfunction]
fn gdps_map_grid(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("shape", (HEIGHT, WIDTH))?;
    kwargs.set_item("spacing", SPACING)?;
    Ok(PyModule::import(py, "types")?
        .getattr("SimpleNamespace")?
        .call((), Some(&kwargs))?
        .unbind())
}

#[pyfunction]
fn gdps_map_directory(py: Python<'_>, run: OffsetDateTime, hour: u16) -> PyResult<String> {
    Ok(Frame::new(run, hour)
        .map_err(|error| field_error(py, error))?
        .directory())
}

#[pyfunction]
fn rdps_map_directory(py: Python<'_>, run: OffsetDateTime, hour: u16) -> PyResult<String> {
    Ok(Frame::for_model(Model::Rdps, run, hour)
        .map_err(|error| field_error(py, error))?
        .directory())
}

#[pyfunction]
fn hrdps_map_directory(py: Python<'_>, run: OffsetDateTime, hour: u16) -> PyResult<String> {
    Ok(Frame::for_model(Model::Hrdps, run, hour)
        .map_err(|error| field_error(py, error))?
        .directory())
}

#[pyfunction]
fn available_gdps_map_products(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    available(
        py,
        products,
        Frame::new(run, hour).map_err(|error| field_error(py, error))?,
        timeout,
    )
}

#[pyfunction]
fn available_rdps_map_products(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    available(
        py,
        products,
        Frame::for_model(Model::Rdps, run, hour).map_err(|error| field_error(py, error))?,
        timeout,
    )
}

#[pyfunction]
fn available_hrdps_map_products(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    available(
        py,
        products,
        Frame::for_model(Model::Hrdps, run, hour).map_err(|error| field_error(py, error))?,
        timeout,
    )
}

fn available(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    frame: Frame,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    let requested = products
        .iter()
        .map(|product| {
            let entries = records(product.bind(py), frame.model)?;
            Ok(available_records_for_frame(entries, frame))
        })
        .collect::<PyResult<Vec<_>>>()?;
    let cancellation = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    let event = cancellation.call_method0("event")?;
    let callback = if event.is_none() {
        None
    } else {
        Some(event.getattr("is_set")?.unbind())
    };
    let runtime = crate::weather::runtime()?;
    let available = py.detach(|| {
        runtime.block_on(available_from_listings(
            &requested,
            frame,
            timeout,
            async |frame: Frame| {
                ModelClient::shared()
                    .fetch(
                        &frame.directory(),
                        timeout,
                        MAX_INVENTORY_BYTES,
                        None,
                        || cancelled(callback.as_ref()),
                    )
                    .await
            },
        ))
    });
    match available {
        Ok(available) => Ok(products
            .into_iter()
            .zip(available)
            .filter_map(|(product, present)| present.then_some(product))
            .collect()),
        Err(Error::Cancelled) => Err(PyErr::from_value(
            cancellation.getattr("Cancelled")?.call0()?,
        )),
        Err(error) => Err(field_error(py, error)),
    }
}

#[pyfunction(signature = (product, run, hour, timeout, should_cancel=None, workers=4))]
fn download_gdps_map_fields<'py>(
    py: Python<'py>,
    product: &Bound<'py, PyAny>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let frame = Frame::new(run, hour).map_err(|error| field_error(py, error))?;
    let decoded = download(py, product, frame, timeout, should_cancel, workers)?;
    if product.getattr("key")?.extract::<String>()? == "hgt-anom-500" {
        decoded.set_item(
            "__valid_time",
            (run + time::Duration::hours(i64::from(hour))).unix_timestamp(),
        )?;
    }
    Ok(decoded)
}

#[pyfunction(signature = (product, run, hour, timeout, should_cancel=None, workers=4))]
fn download_rdps_map_fields<'py>(
    py: Python<'py>,
    product: &Bound<'py, PyAny>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let frame = Frame::for_model(Model::Rdps, run, hour).map_err(|error| field_error(py, error))?;
    download(py, product, frame, timeout, should_cancel, workers)
}

#[pyfunction(signature = (product, run, hour, timeout, should_cancel=None, workers=4))]
fn download_hrdps_map_fields<'py>(
    py: Python<'py>,
    product: &Bound<'py, PyAny>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let frame =
        Frame::for_model(Model::Hrdps, run, hour).map_err(|error| field_error(py, error))?;
    download(py, product, frame, timeout, should_cancel, workers)
}

struct FetchTask {
    name: String,
    frame: Frame,
    record: Record,
    diff_prev: bool,
}

fn download<'py>(
    py: Python<'py>,
    product: &Bound<'py, PyAny>,
    frame: Frame,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let entries = records(product, frame.model)?;
    let intermediate =
        matches!(frame.model, Model::Gdps | Model::Rdps) && !frame.hour.is_multiple_of(6);
    let mut tasks = Vec::with_capacity(entries.len());
    for (name, record) in entries {
        if intermediate {
            if let Some(total) = gdps_fields::run_total_counterpart(&name) {
                if frame.hour < 6 {
                    return Err(field_error(py, Error::Unavailable(404)));
                }
                let prev = Frame {
                    hour: frame.hour - 6,
                    ..frame
                };
                tasks.push(FetchTask {
                    name: name.clone(),
                    frame,
                    record: total,
                    diff_prev: false,
                });
                tasks.push(FetchTask {
                    name,
                    frame: prev,
                    record: total,
                    diff_prev: true,
                });
                continue;
            }
        }
        tasks.push(FetchTask {
            name,
            frame,
            record,
            diff_prev: false,
        });
    }
    let library = PyModule::import(py, "sharpmod.backends.rust_backend")?
        .getattr("_eccodes_library_path")?
        .call0()
        .and_then(|path| path.extract::<String>())
        .map_err(|error| {
            field_error(
                py,
                Error::Invalid(format!("Canadian maps need the GRIB runtime: {error}")),
            )
        })?;
    let jobs = tasks
        .iter()
        .map(|task| FieldJob {
            url: task.frame.file_url(task.record),
            limit: MAX_FIELD_BYTES,
            range: None,
        })
        .collect();
    let runtime = crate::weather::runtime()?;
    let stop = || cancelled(should_cancel.as_ref());
    let payloads = py
        .detach(|| {
            runtime.block_on(fetch_model_fields(
                ModelClient::shared(),
                jobs,
                timeout,
                workers,
                stop,
            ))
        })
        .map_err(|error| field_error(py, error))?;
    // Other map providers use ecCodes from Python. Share their lock only
    // while decoding, so waiting on Datamart never blocks another decoder.
    let lock = PyModule::import(py, "sharpmod.backends.grib")?.getattr("_ECCODES_LOCK")?;
    lock.call_method0("acquire")?;
    let decoded_tasks = py.detach(|| {
        tasks
            .into_iter()
            .zip(payloads)
            .map(|(task, payload)| {
                if stop().map_err(Error::Invalid)? {
                    return Err(Error::Cancelled);
                }
                let decoded =
                    gdps_grid::decode(Path::new(&library), &payload, &task.frame, task.record)
                        .map_err(|error| Error::Invalid(error.to_string()))?;
                Ok((task.name, task.diff_prev, decoded))
            })
            .collect::<Result<Vec<_>, _>>()
    });
    lock.call_method0("release")?;
    let decoded_tasks = decoded_tasks.map_err(|error| field_error(py, error))?;
    let mut values: Vec<(String, Vec<f32>)> = Vec::with_capacity(decoded_tasks.len());
    for (name, diff_prev, grid) in decoded_tasks {
        if !diff_prev {
            values.push((name, grid));
            continue;
        }
        let (_, current) = values
            .iter_mut()
            .rev()
            .find(|(entry_name, _)| entry_name == &name)
            .expect("current accumulation precedes its 6-h difference endpoint");
        for (cur, prev) in current.iter_mut().zip(grid) {
            *cur = if cur.is_finite() && prev.is_finite() {
                (*cur - prev).max(0.0)
            } else {
                f32::NAN
            };
        }
    }
    if matches!(frame.model, Model::Rdps | Model::Hrdps) {
        // Each product requests complete pairs. Rotate before deriving speed or
        // packing arrows, so all downstream consumers see true east/north winds.
        let pairs = values
            .iter()
            .enumerate()
            .filter_map(|(i, (name, _))| {
                let other = name
                    .strip_prefix("ugrd")
                    .map(|level| format!("vgrd{level}"))
                    .or_else(|| (name == "u10").then(|| "v10".to_owned()))?;
                values
                    .iter()
                    .position(|(name, _)| name == &other)
                    .map(|j| (i, j))
            })
            .collect::<Vec<_>>();
        for (i, j) in pairs {
            let (u, v) = if i < j {
                let (a, b) = values.split_at_mut(j);
                (&mut a[i].1, &mut b[0].1)
            } else {
                let (a, b) = values.split_at_mut(i);
                (&mut b[0].1, &mut a[j].1)
            };
            if frame.model == Model::Hrdps {
                crate::regional_grids::rotate_hrdps(u, v);
            } else {
                crate::regional_grids::rotate_rdps(u, v);
            }
        }
    }
    let shape = match frame.model {
        Model::Gdps => (HEIGHT, WIDTH),
        Model::Rdps => (
            crate::regional_grids::RDPS_HEIGHT,
            crate::regional_grids::RDPS_WIDTH,
        ),
        Model::Hrdps => (
            crate::regional_grids::HRDPS_HEIGHT,
            crate::regional_grids::HRDPS_WIDTH,
        ),
    };
    let decoded = PyDict::new(py);
    for (name, values) in values {
        let grid =
            Array2::from_shape_vec(shape, values).expect("validated Canadian grid dimensions");
        decoded.set_item(name, grid.into_pyarray(py))?;
    }
    Ok(decoded)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(gdps_map_grid, module)?)?;
    module.add_function(wrap_pyfunction!(gdps_map_directory, module)?)?;
    module.add_function(wrap_pyfunction!(available_gdps_map_products, module)?)?;
    module.add_function(wrap_pyfunction!(download_gdps_map_fields, module)?)?;
    module.add_function(wrap_pyfunction!(rdps_map_directory, module)?)?;
    module.add_function(wrap_pyfunction!(available_rdps_map_products, module)?)?;
    module.add_function(wrap_pyfunction!(download_rdps_map_fields, module)?)?;
    module.add_function(wrap_pyfunction!(hrdps_map_directory, module)?)?;
    module.add_function(wrap_pyfunction!(available_hrdps_map_products, module)?)?;
    module.add_function(wrap_pyfunction!(download_hrdps_map_fields, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn listing(frame: Frame, records: &[Record]) -> Vec<u8> {
        records
            .iter()
            .map(|&record| {
                let url = frame.file_url(record);
                let name = url.rsplit('/').next().unwrap();
                format!("<a href=\"{name}\">{name}</a>\n")
            })
            .collect::<String>()
            .into_bytes()
    }

    #[test]
    fn intermediate_ptype_requires_all_four_earlier_totals() {
        let runtime = crate::weather::runtime().unwrap();
        let run = OffsetDateTime::UNIX_EPOCH;
        let temperature = gdps_fields::record("tmp2m").unwrap();
        let types = ["rain6h", "snow6h", "frzr6h", "sleet6h"];
        let totals = types.map(|name| gdps_fields::run_total_counterpart(name).unwrap());
        let overlays = ["mslp", "hgt500", "hgt1000"];
        let overlay_records = overlays.map(|name| gdps_fields::record(name).unwrap());
        for (model, hour) in [(Model::Gdps, 9), (Model::Rdps, 7)] {
            let frame = Frame::for_model(model, run, hour).unwrap();
            let earlier = Frame::for_model(model, run, hour - 6).unwrap();
            let entries = types
                .into_iter()
                .chain(overlays)
                .map(|name| (name.to_owned(), gdps_fields::record(name).unwrap()))
                .collect();
            let requested = [
                available_records_for_frame(entries, frame),
                available_records_for_frame(vec![("tmp2m".into(), temperature)], frame),
            ];
            let current_records: Vec<_> = totals
                .into_iter()
                .chain(overlay_records)
                .chain([temperature])
                .collect();
            let current_listing = listing(frame, &current_records);
            for (earlier_listing, expected) in [
                (Ok(listing(earlier, &totals)), [true, true]),
                (Ok(listing(earlier, &totals[..3])), [false, true]),
                (Ok(listing(frame, &totals)), [false, true]),
                (Err(Error::Unavailable(404)), [false, true]),
            ] {
                let hours = RefCell::new(Vec::new());
                let available = runtime
                    .block_on(available_from_listings(
                        &requested,
                        frame,
                        1.0,
                        async |request: Frame| {
                            hours.borrow_mut().push(request.hour);
                            if request.hour == frame.hour {
                                Ok(current_listing.clone())
                            } else {
                                assert_eq!(request.hour, earlier.hour);
                                earlier_listing.clone()
                            }
                        },
                    ))
                    .unwrap();
                assert_eq!(available, expected);
                let mut hours = hours.into_inner();
                hours.sort_unstable();
                assert_eq!(hours, [hour - 6, hour]);
            }
        }
    }

    #[test]
    fn products_without_earlier_totals_fetch_only_the_selected_hour() {
        let runtime = crate::weather::runtime().unwrap();
        let run = OffsetDateTime::UNIX_EPOCH;
        for (model, hour, name) in [
            (Model::Gdps, 6, "rain6h"),
            (Model::Rdps, 6, "rain6h"),
            (Model::Hrdps, 7, "qpf6h"),
            (Model::Gdps, 9, "tmp2m"),
            (Model::Rdps, 7, "tmp2m"),
        ] {
            let frame = Frame::for_model(model, run, hour).unwrap();
            let record = if model == Model::Hrdps {
                gdps_fields::hrdps_record(name).unwrap()
            } else {
                gdps_fields::record(name).unwrap()
            };
            let requested = [available_records_for_frame(
                vec![(name.into(), record)],
                frame,
            )];
            let available = runtime
                .block_on(available_from_listings(
                    &requested,
                    frame,
                    1.0,
                    async |request: Frame| {
                        assert_eq!(request.hour, hour);
                        Ok(listing(request, &[record]))
                    },
                ))
                .unwrap();
            assert_eq!(available, [true]);
        }
    }
}
