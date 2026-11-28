//! NOAA SPC REFS Zarr retrieval and Blosc decoding for ensemble statistics.

use numpy::{ndarray::Array2, IntoPyArray, PyArrayLike1, PyUntypedArrayMethods};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyBytes, PyDict},
};
use sharpmod_weather::model_http::{Error, ModelClient};
use time::{OffsetDateTime, UtcOffset};

use crate::model_http_bindings::{cancelled, field_error};

const ROOT: &str = "https://www.spc.noaa.gov/exper/refs/data";
const MAX_CHUNK_BYTES: usize = 4 * 1024 * 1024;
const NY: usize = 1059;
const NX: usize = 1799;
const KT_TO_MS: f32 = 1.0 / 1.943_844_5;

fn zarr_root_url(run: OffsetDateTime) -> String {
    let run = run.to_offset(UtcOffset::UTC);
    let (year, month, day, cycle) = (run.year(), u8::from(run.month()), run.day(), run.hour());
    format!("{ROOT}/{year:04}/{month:02}/{day:02}/refs-spc_{year:04}{month:02}{day:02}_{cycle:02}00.zarr")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ZarrWindow {
    Hourly,
    Step3h,
    Step6h,
    Max4h,
    Max24hOverlap12h,
    Acc48hOverlap12h,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ZarrAux {
    None,
    Shear06,
    Srh(&'static str, &'static str),
    Srh03Shear06,
    PtypeConsensus,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ZarrSpec {
    array: &'static str,
    field: &'static str,
    dtype: &'static str,
    aux: ZarrAux,
    window: ZarrWindow,
}

pub(super) fn window_step_hours(cycle: u8, step: u16) -> Vec<u16> {
    if !matches!(cycle, 0 | 6 | 12 | 18) || step == 0 {
        return Vec::new();
    }
    (step..=60).step_by(usize::from(step)).collect()
}

pub(super) fn window_24h_hours(cycle: u8) -> Vec<u16> {
    if !matches!(cycle, 0 | 6 | 12 | 18) {
        return Vec::new();
    }
    let start = 24 + u16::from(cycle % 12);
    (start..=60).step_by(12).collect()
}

pub(super) fn window_48h_hours(cycle: u8) -> Vec<u16> {
    if !matches!(cycle, 0 | 6 | 12 | 18) {
        return Vec::new();
    }
    let start = 48 + u16::from(cycle % 12);
    (start..=60).step_by(12).collect()
}

pub(super) fn is_zarr_product(key: &str) -> bool {
    zarr_spec(key).is_some()
}

fn resolve_chunk(window: ZarrWindow, cycle: u8, hour: u16) -> Option<(u16, &'static str)> {
    if !matches!(cycle, 0 | 6 | 12 | 18) {
        return None;
    }
    match window {
        ZarrWindow::Hourly => (1..=60).contains(&hour).then(|| (hour - 1, "step")),
        ZarrWindow::Step3h => ((3..=60).contains(&hour) && hour.is_multiple_of(3))
            .then(|| ((hour / 3) - 1, "valid_time_3h")),
        ZarrWindow::Step6h => ((6..=60).contains(&hour) && hour.is_multiple_of(6))
            .then(|| ((hour / 6) - 1, "valid_time_6h")),
        ZarrWindow::Max4h => (4..=60)
            .contains(&hour)
            .then(|| (hour - 4, "valid_time_4h_overlap")),
        ZarrWindow::Max24hOverlap12h => {
            let start = 24 + u16::from(cycle % 12);
            ((start..=60).contains(&hour) && (hour - start).is_multiple_of(12))
                .then(|| ((hour - start) / 12, "valid_time_24h_overlap_12h"))
        }
        ZarrWindow::Acc48hOverlap12h => {
            let start = 48 + u16::from(cycle % 12);
            ((start..=60).contains(&hour) && (hour - start).is_multiple_of(12))
                .then(|| ((hour - start) / 12, "valid_time_48h_overlap_12h"))
        }
    }
}

fn zarr_spec(key: &str) -> Option<ZarrSpec> {
    match key {
        "qpf-1h-ptype" => Some(ZarrSpec {
            array: "qpf_01h_acc_mean",
            field: "qpf1h_ptype",
            dtype: "<f2",
            aux: ZarrAux::PtypeConsensus,
            window: ZarrWindow::Hourly,
        }),
        "qpf-3h" => Some(ZarrSpec {
            array: "qpf_03h_acc_mean",
            field: "qpf3h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Step3h,
        }),
        "qpf-6h" => Some(ZarrSpec {
            array: "qpf_06h_acc_mean",
            field: "qpf6h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Step6h,
        }),
        "qpf-24h" => Some(ZarrSpec {
            array: "qpf_24h_acc_overlap12h_mean",
            field: "qpf24h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Max24hOverlap12h,
        }),
        "qpf-48h" => Some(ZarrSpec {
            array: "qpf_48h_acc_overlap12h_mean",
            field: "qpf48h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Acc48hOverlap12h,
        }),
        "snow-1h" => Some(ZarrSpec {
            array: "snow_01h_acc_mean",
            field: "snow1h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Hourly,
        }),
        "sbcape-min" => Some(ZarrSpec {
            array: "sbcape_min",
            field: "sbcape",
            dtype: "<f4",
            aux: ZarrAux::Shear06,
            window: ZarrWindow::Hourly,
        }),
        "sbcape" => Some(ZarrSpec {
            array: "sbcape_mean",
            field: "sbcape",
            dtype: "<f4",
            aux: ZarrAux::Shear06,
            window: ZarrWindow::Hourly,
        }),
        "sbcape-max" => Some(ZarrSpec {
            array: "sbcape_max",
            field: "sbcape",
            dtype: "<f4",
            aux: ZarrAux::Shear06,
            window: ZarrWindow::Hourly,
        }),
        "mucape" => Some(ZarrSpec {
            array: "mucape_mean",
            field: "mucape",
            dtype: "<f4",
            aux: ZarrAux::Shear06,
            window: ZarrWindow::Hourly,
        }),
        "srh-0-1km" => Some(ZarrSpec {
            array: "srh01_mean",
            field: "srh01",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Hourly,
        }),
        "ehi-0-1km" => Some(ZarrSpec {
            array: "sbcape_mean",
            field: "sbcape",
            dtype: "<f4",
            aux: ZarrAux::Srh("srh01_mean", "srh01"),
            window: ZarrWindow::Hourly,
        }),
        "ehi-0-3km" => Some(ZarrSpec {
            array: "sbcape_mean",
            field: "sbcape",
            dtype: "<f4",
            aux: ZarrAux::Srh("srh03_mean", "srh03"),
            window: ZarrWindow::Hourly,
        }),
        "scp" => Some(ZarrSpec {
            array: "mucape_mean",
            field: "mucape",
            dtype: "<f4",
            aux: ZarrAux::Srh03Shear06,
            window: ZarrWindow::Hourly,
        }),
        "stp" => Some(ZarrSpec {
            array: "stp_fixed_mean",
            field: "stp",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Hourly,
        }),
        "refc-4h" => Some(ZarrSpec {
            array: "maxref1km_04h_max_overlap_max",
            field: "refc4h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Max4h,
        }),
        "refc-24h" => Some(ZarrSpec {
            array: "maxref1km_24h_max_overlap12h_max",
            field: "refc24h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Max24hOverlap12h,
        }),
        "wind-10m-4h" => Some(ZarrSpec {
            array: "s10m_04h_max_overlap_max",
            field: "wspd10m4h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Max4h,
        }),
        "wind-10m-24h" => Some(ZarrSpec {
            array: "s10m_24h_max_overlap12h_max",
            field: "wspd10m24h",
            dtype: "<f2",
            aux: ZarrAux::None,
            window: ZarrWindow::Max24hOverlap12h,
        }),
        _ => None,
    }
}

fn encode_qpf_ptype(qpf: &Array2<f32>, ptype: &Array2<f32>) -> Array2<f32> {
    let mut out = Array2::<f32>::from_elem((NY, NX), f32::NAN);
    for ((out_cell, &q), &pt) in out.iter_mut().zip(qpf.iter()).zip(ptype.iter()) {
        let q_in = (q * 1000.0).round() / 1000.0;
        let code = pt.round() as i32;
        if q_in.is_finite() && q_in >= 0.01 && (1..=4).contains(&code) {
            *out_cell = ((code - 1) as f32) * 10.0 + q_in.min(9.99) + 0.0004;
        }
    }
    out
}

/// Ensemble-mean sea-level pressure (hPa) and 1000-500 mb thickness (dam).
///
/// SPC's REFS mean omits the 1000 mb height, so the base of the thickness is a
/// hydrostatic 1000 mb height derived from the mean sea-level pressure and the
/// 2-m mean temperature, the two mean fields the Zarr archive publishes.
fn refs_mean_thickness(
    pmsl_pa: &Array2<f32>,
    z500_m: &Array2<f32>,
    t2m_f: &Array2<f32>,
) -> (Array2<f32>, Array2<f32>) {
    let mut mslp = Vec::with_capacity(NY * NX);
    let mut thickness = Vec::with_capacity(NY * NX);
    for ((&pressure, &z500), &t2m) in pmsl_pa.iter().zip(z500_m.iter()).zip(t2m_f.iter()) {
        let pressure_hpa = f64::from(pressure) / 100.0;
        mslp.push(pressure_hpa as f32);
        let temperature_k = (f64::from(t2m) - 32.0) * 5.0 / 9.0 + 273.15;
        if pressure_hpa > 0.0 && temperature_k.is_finite() && z500.is_finite() {
            let hgt1000_m = (287.05 * temperature_k / 9.80665) * (pressure_hpa / 1000.0).ln();
            thickness
                .push(crate::map_products::ptype::thickness_dam(f64::from(z500), hgt1000_m) as f32);
        } else {
            thickness.push(f32::NAN);
        }
    }
    (
        Array2::from_shape_vec((NY, NX), mslp).expect("MSLP keeps the decoded chunk shape"),
        Array2::from_shape_vec((NY, NX), thickness)
            .expect("thickness keeps the decoded chunk shape"),
    )
}

fn decode_blosc_f32(
    py: Python<'_>,
    blosc: &Bound<'_, PyAny>,
    np: &Bound<'_, PyModule>,
    payload: &[u8],
    dtype: &str,
    scale: f32,
) -> PyResult<Array2<f32>> {
    let raw = blosc.call_method1("decode", (PyBytes::new(py, payload),))?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("dtype", dtype)?;
    let array = np
        .call_method("frombuffer", (raw,), Some(&kwargs))?
        .call_method1("astype", ("<f4",))?;
    let flat: PyArrayLike1<'_, f32> = array.extract()?;
    if flat.len() != NY * NX {
        return Err(PyValueError::new_err(format!(
            "SPC REFS Zarr chunk has {} cells, expected {}",
            flat.len(),
            NY * NX
        )));
    }
    let values = flat
        .as_array()
        .iter()
        .map(|&value| value * scale)
        .collect::<Vec<_>>();
    Array2::from_shape_vec((NY, NX), values)
        .map_err(|error| PyValueError::new_err(error.to_string()))
}

fn decode_step_hour(py: Python<'_>, blosc: &Bound<'_, PyAny>, payload: &[u8]) -> PyResult<f64> {
    let raw = blosc.call_method1("decode", (PyBytes::new(py, payload),))?;
    let bytes: &[u8] = raw.cast::<PyBytes>()?.as_bytes();
    let octets: [u8; 8] = bytes
        .try_into()
        .map_err(|_| PyValueError::new_err("SPC REFS Zarr step chunk has unexpected length"))?;
    Ok(f64::from_le_bytes(octets))
}

#[pyfunction(signature = (key, run, hour, timeout, should_cancel=None))]
fn refs_zarr_map_fields<'py>(
    py: Python<'py>,
    key: &str,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
) -> PyResult<(Bound<'py, PyDict>, String)> {
    let spec = zarr_spec(key).ok_or_else(|| PyValueError::new_err("unknown REFS Zarr product"))?;
    let run = run.to_offset(UtcOffset::UTC);
    let (chunk, coord_array) = resolve_chunk(spec.window, run.hour(), hour)
        .ok_or_else(|| field_error(py, Error::Unavailable(404)))?;
    let root = zarr_root_url(run);
    let step_url = format!("{root}/{coord_array}/{chunk}");
    let scalar_url = format!("{root}/{}/{chunk}.0.0", spec.array);

    let runtime = crate::weather::runtime()?;
    let stop = || cancelled(should_cancel.as_ref());
    let client = ModelClient::shared();
    enum FetchedAux {
        None,
        Shear(Vec<u8>, Vec<u8>),
        Srh(&'static str, Vec<u8>),
        Srh03Shear(Vec<u8>, Vec<u8>, Vec<u8>),
        Ptype(Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>),
    }
    let (step_bytes, scalar_bytes, aux_bytes) = match spec.aux {
        ZarrAux::Shear06 => {
            let u_url = format!("{root}/ushr06_mean/{chunk}.0.0");
            let v_url = format!("{root}/vshr06_mean/{chunk}.0.0");
            let (step, scalar, u, v) = py
                .detach(|| {
                    runtime.block_on(async {
                        tokio::try_join!(
                            client.fetch(&step_url, timeout, 4096, None, &stop),
                            client.fetch(&scalar_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&u_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&v_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                        )
                    })
                })
                .map_err(|error| field_error(py, error))?;
            (step, scalar, FetchedAux::Shear(u, v))
        }
        ZarrAux::Srh(srh_array, srh_field) => {
            let srh_url = format!("{root}/{srh_array}/{chunk}.0.0");
            let (step, scalar, srh) = py
                .detach(|| {
                    runtime.block_on(async {
                        tokio::try_join!(
                            client.fetch(&step_url, timeout, 4096, None, &stop),
                            client.fetch(&scalar_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&srh_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                        )
                    })
                })
                .map_err(|error| field_error(py, error))?;
            (step, scalar, FetchedAux::Srh(srh_field, srh))
        }
        ZarrAux::Srh03Shear06 => {
            let srh03_url = format!("{root}/srh03_mean/{chunk}.0.0");
            let u_url = format!("{root}/ushr06_mean/{chunk}.0.0");
            let v_url = format!("{root}/vshr06_mean/{chunk}.0.0");
            let (step, scalar, srh03, u, v) = py
                .detach(|| {
                    runtime.block_on(async {
                        tokio::try_join!(
                            client.fetch(&step_url, timeout, 4096, None, &stop),
                            client.fetch(&scalar_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&srh03_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&u_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&v_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                        )
                    })
                })
                .map_err(|error| field_error(py, error))?;
            (step, scalar, FetchedAux::Srh03Shear(srh03, u, v))
        }
        ZarrAux::PtypeConsensus => {
            let ptype_url = format!("{root}/ptype_consensus/{chunk}.0.0");
            // The REFS mean omits HGT:1000 mb, so the thickness pairs z500_mean
            // with a hydrostatic 1000 mb height from pmsl_mean and t2m_mean.
            let pmsl_url = format!("{root}/pmsl_mean/{chunk}.0.0");
            let z500_url = format!("{root}/z500_mean/{chunk}.0.0");
            let t2m_url = format!("{root}/t2m_mean/{chunk}.0.0");
            let (step, scalar, ptype, pmsl, z500, t2m) = py
                .detach(|| {
                    runtime.block_on(async {
                        tokio::try_join!(
                            client.fetch(&step_url, timeout, 4096, None, &stop),
                            client.fetch(&scalar_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&ptype_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&pmsl_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&z500_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                            client.fetch(&t2m_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                        )
                    })
                })
                .map_err(|error| field_error(py, error))?;
            (step, scalar, FetchedAux::Ptype(ptype, pmsl, z500, t2m))
        }
        ZarrAux::None => {
            let (step, scalar) = py
                .detach(|| {
                    runtime.block_on(async {
                        tokio::try_join!(
                            client.fetch(&step_url, timeout, 4096, None, &stop),
                            client.fetch(&scalar_url, timeout, MAX_CHUNK_BYTES, None, &stop),
                        )
                    })
                })
                .map_err(|error| field_error(py, error))?;
            (step, scalar, FetchedAux::None)
        }
    };

    let blosc = PyModule::import(py, "numcodecs")?.call_method0("Blosc")?;
    let np = PyModule::import(py, "numpy")?;
    let raw_step = decode_step_hour(py, &blosc, &step_bytes)
        .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
    let step_hour = if matches!(spec.window, ZarrWindow::Hourly) {
        raw_step
    } else {
        (raw_step - run.unix_timestamp() as f64) / 3600.0
    };
    if (step_hour - f64::from(hour)).abs() > 1e-6 {
        return Err(field_error(py, Error::Unavailable(404)));
    }

    let scalar = decode_blosc_f32(py, &blosc, &np, &scalar_bytes, spec.dtype, 1.0)
        .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
    let decoded = PyDict::new(py);
    match aux_bytes {
        FetchedAux::Ptype(ptype_bytes, pmsl_bytes, z500_bytes, t2m_bytes) => {
            let ptype = decode_blosc_f32(py, &blosc, &np, &ptype_bytes, "|u1", 1.0)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let encoded = encode_qpf_ptype(&scalar, &ptype);
            decoded.set_item(spec.field, encoded.into_pyarray(py))?;
            // pmsl_mean publishes pascals; the isobars are drawn in hectopascals.
            let pmsl = decode_blosc_f32(py, &blosc, &np, &pmsl_bytes, "<f4", 1.0)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let z500 = decode_blosc_f32(py, &blosc, &np, &z500_bytes, "<f4", 1.0)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let t2m = decode_blosc_f32(py, &blosc, &np, &t2m_bytes, "<f2", 1.0)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let (mslp, thickness) = py.detach(|| refs_mean_thickness(&pmsl, &z500, &t2m));
            decoded.set_item("mslp", mslp.into_pyarray(py))?;
            decoded.set_item("thickness", thickness.into_pyarray(py))?;
        }
        FetchedAux::Shear(u_bytes, v_bytes) => {
            decoded.set_item(spec.field, scalar.into_pyarray(py))?;
            let u = decode_blosc_f32(py, &blosc, &np, &u_bytes, "<f2", KT_TO_MS)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let v = decode_blosc_f32(py, &blosc, &np, &v_bytes, "<f2", KT_TO_MS)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            decoded.set_item("ushr06", u.into_pyarray(py))?;
            decoded.set_item("vshr06", v.into_pyarray(py))?;
        }
        FetchedAux::Srh(srh_field, srh_bytes) => {
            decoded.set_item(spec.field, scalar.into_pyarray(py))?;
            let srh = decode_blosc_f32(py, &blosc, &np, &srh_bytes, "<f2", 1.0)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            decoded.set_item(srh_field, srh.into_pyarray(py))?;
        }
        FetchedAux::Srh03Shear(srh03_bytes, u_bytes, v_bytes) => {
            decoded.set_item(spec.field, scalar.into_pyarray(py))?;
            let srh03 = decode_blosc_f32(py, &blosc, &np, &srh03_bytes, "<f2", 1.0)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let u = decode_blosc_f32(py, &blosc, &np, &u_bytes, "<f2", KT_TO_MS)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let v = decode_blosc_f32(py, &blosc, &np, &v_bytes, "<f2", KT_TO_MS)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            decoded.set_item("srh03", srh03.into_pyarray(py))?;
            decoded.set_item("ushr06", u.into_pyarray(py))?;
            decoded.set_item("vshr06", v.into_pyarray(py))?;
        }
        FetchedAux::None => {
            decoded.set_item(spec.field, scalar.into_pyarray(py))?;
        }
    }
    Ok((decoded, root))
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(refs_zarr_map_fields, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::{Date, Month};

    #[test]
    fn formats_spc_refs_zarr_root_url_for_utc_cycle() {
        let run = Date::from_calendar_date(2026, Month::November, 22)
            .unwrap()
            .with_hms(6, 0, 0)
            .unwrap()
            .assume_utc();
        assert_eq!(
            zarr_root_url(run),
            "https://www.spc.noaa.gov/exper/refs/data/2026/11/22/refs-spc_20261122_0600.zarr"
        );
    }

    #[test]
    fn maps_refs_keys_to_spc_zarr_specs() {
        assert_eq!(
            zarr_spec("qpf-1h-ptype"),
            Some(ZarrSpec {
                array: "qpf_01h_acc_mean",
                field: "qpf1h_ptype",
                dtype: "<f2",
                aux: ZarrAux::PtypeConsensus,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("sbcape-min"),
            Some(ZarrSpec {
                array: "sbcape_min",
                field: "sbcape",
                dtype: "<f4",
                aux: ZarrAux::Shear06,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("sbcape"),
            Some(ZarrSpec {
                array: "sbcape_mean",
                field: "sbcape",
                dtype: "<f4",
                aux: ZarrAux::Shear06,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("sbcape-max"),
            Some(ZarrSpec {
                array: "sbcape_max",
                field: "sbcape",
                dtype: "<f4",
                aux: ZarrAux::Shear06,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("mucape"),
            Some(ZarrSpec {
                array: "mucape_mean",
                field: "mucape",
                dtype: "<f4",
                aux: ZarrAux::Shear06,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("srh-0-1km"),
            Some(ZarrSpec {
                array: "srh01_mean",
                field: "srh01",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("ehi-0-1km"),
            Some(ZarrSpec {
                array: "sbcape_mean",
                field: "sbcape",
                dtype: "<f4",
                aux: ZarrAux::Srh("srh01_mean", "srh01"),
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("ehi-0-3km"),
            Some(ZarrSpec {
                array: "sbcape_mean",
                field: "sbcape",
                dtype: "<f4",
                aux: ZarrAux::Srh("srh03_mean", "srh03"),
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("scp"),
            Some(ZarrSpec {
                array: "mucape_mean",
                field: "mucape",
                dtype: "<f4",
                aux: ZarrAux::Srh03Shear06,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("stp"),
            Some(ZarrSpec {
                array: "stp_fixed_mean",
                field: "stp",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(
            zarr_spec("refc-4h"),
            Some(ZarrSpec {
                array: "maxref1km_04h_max_overlap_max",
                field: "refc4h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Max4h,
            })
        );
        assert_eq!(
            zarr_spec("refc-24h"),
            Some(ZarrSpec {
                array: "maxref1km_24h_max_overlap12h_max",
                field: "refc24h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Max24hOverlap12h,
            })
        );
        assert_eq!(
            zarr_spec("wind-10m-4h"),
            Some(ZarrSpec {
                array: "s10m_04h_max_overlap_max",
                field: "wspd10m4h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Max4h,
            })
        );
        assert_eq!(
            zarr_spec("wind-10m-24h"),
            Some(ZarrSpec {
                array: "s10m_24h_max_overlap12h_max",
                field: "wspd10m24h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Max24hOverlap12h,
            })
        );
        assert_eq!(
            zarr_spec("qpf-3h"),
            Some(ZarrSpec {
                array: "qpf_03h_acc_mean",
                field: "qpf3h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Step3h,
            })
        );
        assert_eq!(
            zarr_spec("qpf-6h"),
            Some(ZarrSpec {
                array: "qpf_06h_acc_mean",
                field: "qpf6h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Step6h,
            })
        );
        assert_eq!(
            zarr_spec("qpf-24h"),
            Some(ZarrSpec {
                array: "qpf_24h_acc_overlap12h_mean",
                field: "qpf24h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Max24hOverlap12h,
            })
        );
        assert_eq!(
            zarr_spec("qpf-48h"),
            Some(ZarrSpec {
                array: "qpf_48h_acc_overlap12h_mean",
                field: "qpf48h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Acc48hOverlap12h,
            })
        );
        assert_eq!(
            zarr_spec("snow-1h"),
            Some(ZarrSpec {
                array: "snow_01h_acc_mean",
                field: "snow1h",
                dtype: "<f2",
                aux: ZarrAux::None,
                window: ZarrWindow::Hourly,
            })
        );
        assert_eq!(zarr_spec("mlcape"), None);
        assert!(is_zarr_product("qpf-1h-ptype"));
        assert!(is_zarr_product("qpf-48h"));
        assert!(is_zarr_product("snow-1h"));
        assert!(is_zarr_product("ehi-0-1km"));
        assert!(is_zarr_product("ehi-0-3km"));
        assert!(is_zarr_product("scp"));
        assert!(is_zarr_product("refc-4h"));
        assert!(!is_zarr_product("srh-0-3km"));
    }

    #[test]
    fn encodes_qpf_and_ptype_consensus_at_type_offsets() {
        let mut qpf = Array2::<f32>::zeros((NY, NX));
        let mut ptype = Array2::<f32>::from_elem((NY, NX), 1.0);
        qpf[[0, 0]] = 0.0099945; // float16 rounding near 0.01 in (Rain)
        ptype[[0, 0]] = 1.0;
        qpf[[0, 1]] = 0.08;
        ptype[[0, 1]] = 2.0; // Snow
        qpf[[0, 2]] = 0.15;
        ptype[[0, 2]] = 3.0; // Frzr
        qpf[[0, 3]] = 0.25;
        ptype[[0, 3]] = 4.0; // Mix
        qpf[[0, 4]] = 0.009; // below 0.01 in floor
        ptype[[0, 4]] = 2.0;
        qpf[[0, 5]] = 0.10;
        ptype[[0, 5]] = 0.0; // unknown consensus type
        qpf[[0, 6]] = f32::NAN;
        ptype[[0, 6]] = 1.0;

        let encoded = encode_qpf_ptype(&qpf, &ptype);
        assert!((encoded[[0, 0]] - 0.0104).abs() < 1e-5);
        assert!((encoded[[0, 1]] - 10.0804).abs() < 1e-5);
        assert!((encoded[[0, 2]] - 20.1504).abs() < 1e-5);
        assert!((encoded[[0, 3]] - 30.2504).abs() < 1e-5);
        assert!(encoded[[0, 4]].is_nan());
        assert!(encoded[[0, 5]].is_nan());
        assert!(encoded[[0, 6]].is_nan());
    }

    #[test]
    fn refs_mean_thickness_pairs_pmsl_z500_and_hydrostatic_1000_mb() {
        let mut pmsl = Array2::<f32>::from_elem((NY, NX), 101_300.0);
        let z500 = Array2::<f32>::from_elem((NY, NX), 5500.0);
        let mut t2m = Array2::<f32>::from_elem((NY, NX), 32.0);
        pmsl[[0, 1]] = f32::NAN;
        t2m[[0, 2]] = f32::NAN;
        pmsl[[0, 3]] = f32::NEG_INFINITY;
        let (mslp, thickness) = refs_mean_thickness(&pmsl, &z500, &t2m);
        // 32 F is 273.15 K: a 103 m hydrostatic 1000 mb height at 1013 hPa.
        let hgt1000 = 287.05 * 273.15 / 9.80665 * (1013.0_f64 / 1000.0).ln();
        assert!((mslp[[0, 0]] - 1013.0).abs() < 1e-2);
        assert!(
            (f64::from(thickness[[0, 0]]) - (5500.0 - hgt1000) / 10.0).abs() < 5e-2,
            "dam: {}",
            thickness[[0, 0]]
        );
        assert!(mslp[[0, 1]].is_nan() && thickness[[0, 1]].is_nan());
        assert!(mslp[[0, 2]] == 1013.0 && thickness[[0, 2]].is_nan());
        assert!(thickness[[0, 3]].is_nan());
    }

    #[test]
    fn resolves_hourly_4h_and_24h_zarr_window_schedules_and_chunks() {
        assert_eq!(
            window_step_hours(6, 3),
            (3..=60).step_by(3).collect::<Vec<_>>()
        );
        assert_eq!(
            window_step_hours(6, 6),
            (6..=60).step_by(6).collect::<Vec<_>>()
        );
        assert!(window_step_hours(3, 3).is_empty());

        assert_eq!(window_24h_hours(0), [24, 36, 48, 60]);
        assert_eq!(window_24h_hours(6), [30, 42, 54]);
        assert_eq!(window_24h_hours(12), [24, 36, 48, 60]);
        assert_eq!(window_24h_hours(18), [30, 42, 54]);
        assert!(window_24h_hours(3).is_empty());

        assert_eq!(window_48h_hours(0), [48, 60]);
        assert_eq!(window_48h_hours(6), [54]);
        assert_eq!(window_48h_hours(12), [48, 60]);
        assert_eq!(window_48h_hours(18), [54]);
        assert!(window_48h_hours(3).is_empty());

        assert_eq!(resolve_chunk(ZarrWindow::Hourly, 6, 1), Some((0, "step")));
        assert_eq!(resolve_chunk(ZarrWindow::Hourly, 6, 60), Some((59, "step")));
        assert_eq!(resolve_chunk(ZarrWindow::Hourly, 6, 0), None);

        assert_eq!(resolve_chunk(ZarrWindow::Step3h, 6, 2), None);
        assert_eq!(
            resolve_chunk(ZarrWindow::Step3h, 6, 3),
            Some((0, "valid_time_3h"))
        );
        assert_eq!(
            resolve_chunk(ZarrWindow::Step3h, 6, 60),
            Some((19, "valid_time_3h"))
        );

        assert_eq!(resolve_chunk(ZarrWindow::Step6h, 6, 3), None);
        assert_eq!(
            resolve_chunk(ZarrWindow::Step6h, 6, 6),
            Some((0, "valid_time_6h"))
        );
        assert_eq!(
            resolve_chunk(ZarrWindow::Step6h, 6, 60),
            Some((9, "valid_time_6h"))
        );

        assert_eq!(resolve_chunk(ZarrWindow::Max4h, 6, 3), None);
        assert_eq!(
            resolve_chunk(ZarrWindow::Max4h, 6, 4),
            Some((0, "valid_time_4h_overlap"))
        );
        assert_eq!(
            resolve_chunk(ZarrWindow::Max4h, 6, 60),
            Some((56, "valid_time_4h_overlap"))
        );

        assert_eq!(
            resolve_chunk(ZarrWindow::Max24hOverlap12h, 0, 24),
            Some((0, "valid_time_24h_overlap_12h"))
        );
        assert_eq!(
            resolve_chunk(ZarrWindow::Max24hOverlap12h, 0, 60),
            Some((3, "valid_time_24h_overlap_12h"))
        );
        assert_eq!(resolve_chunk(ZarrWindow::Max24hOverlap12h, 6, 24), None);
        assert_eq!(
            resolve_chunk(ZarrWindow::Max24hOverlap12h, 6, 30),
            Some((0, "valid_time_24h_overlap_12h"))
        );
        assert_eq!(
            resolve_chunk(ZarrWindow::Max24hOverlap12h, 18, 54),
            Some((2, "valid_time_24h_overlap_12h"))
        );
        assert_eq!(resolve_chunk(ZarrWindow::Max24hOverlap12h, 3, 24), None);

        assert_eq!(
            resolve_chunk(ZarrWindow::Acc48hOverlap12h, 0, 48),
            Some((0, "valid_time_48h_overlap_12h"))
        );
        assert_eq!(
            resolve_chunk(ZarrWindow::Acc48hOverlap12h, 0, 60),
            Some((1, "valid_time_48h_overlap_12h"))
        );
        assert_eq!(resolve_chunk(ZarrWindow::Acc48hOverlap12h, 6, 48), None);
        assert_eq!(
            resolve_chunk(ZarrWindow::Acc48hOverlap12h, 6, 54),
            Some((0, "valid_time_48h_overlap_12h"))
        );
    }

    #[test]
    fn decodes_blosc_chunks_and_converts_knots_to_metres_per_second() {
        let _guard = crate::lock_python_backend_test();
        Python::initialize();
        Python::attach(|py| {
            let blosc = PyModule::import(py, "numcodecs")
                .unwrap()
                .call_method0("Blosc")
                .unwrap();
            let np = PyModule::import(py, "numpy").unwrap();

            let step_payload = blosc
                .call_method1("encode", (PyBytes::new(py, &1.0_f64.to_le_bytes()),))
                .unwrap();
            let step_bytes = step_payload.cast::<PyBytes>().unwrap().as_bytes();
            assert_eq!(decode_step_hour(py, &blosc, step_bytes).unwrap(), 1.0);

            let cape_raw = np
                .call_method1("full", ((NY * NX,), 1250.0, "<f4"))
                .unwrap()
                .call_method0("tobytes")
                .unwrap();
            let cape_encoded = blosc.call_method1("encode", (cape_raw,)).unwrap();
            let cape_bytes = cape_encoded.cast::<PyBytes>().unwrap().as_bytes();
            let cape = decode_blosc_f32(py, &blosc, &np, cape_bytes, "<f4", 1.0).unwrap();
            assert_eq!(cape.dim(), (NY, NX));
            assert_eq!(cape[[0, 0]], 1250.0);

            let wind_raw = np
                .call_method1("full", ((NY * NX,), 20.0, "<f2"))
                .unwrap()
                .call_method0("tobytes")
                .unwrap();
            let wind_encoded = blosc.call_method1("encode", (wind_raw,)).unwrap();
            let wind_bytes = wind_encoded.cast::<PyBytes>().unwrap().as_bytes();
            let wind = decode_blosc_f32(py, &blosc, &np, wind_bytes, "<f2", KT_TO_MS).unwrap();
            assert!((wind[[0, 0]] - 20.0 * KT_TO_MS).abs() < 1e-4);

            let srh_raw = np
                .call_method1("full", ((NY * NX,), 250.0, "<f2"))
                .unwrap()
                .call_method0("tobytes")
                .unwrap();
            let srh_encoded = blosc.call_method1("encode", (srh_raw,)).unwrap();
            let srh_bytes = srh_encoded.cast::<PyBytes>().unwrap().as_bytes();
            let srh = decode_blosc_f32(py, &blosc, &np, srh_bytes, "<f2", 1.0).unwrap();
            assert_eq!(srh[[0, 0]], 250.0);
        });
    }
}
