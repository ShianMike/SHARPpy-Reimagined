//! Native SharpTab lapse-rate and export-path rules (milestone M44).
//!
//! Rust ports of the pure logic half of two Python modules:
//!
//! * ``sharpmod.sharptab.params`` -- the bound-combination tail of
//!   ``lapse_rate`` ([`sharptab_lapse_rate_from_bounds`]), its height-AGL
//!   branch ([`sharptab_lapse_rate_agl`]), and its pressure branch
//!   ([`sharptab_lapse_rate_pres`]);
//! * ``sharpmod.export_paths`` -- the ``Path(os.fspath(default_name)).name``
//!   check of ``export_file_path`` ([`export_validate_filename`]) and the
//!   write-probe half of ``_assert_writable_directory``
//!   ([`export_probe_writable_dir`]).
//!
//! The SharpTab design principle *missing data propagates, never crashes* is
//! preserved: missing/masked data crosses the boundary as ``None`` (or NaN
//! inside a NumPy column, which the interpolation kernel filters), and every
//! lapse-rate helper returns ``None`` instead of raising.  The filename rule
//! raises ``ValueError`` with the Python message verbatim; the probe raises
//! ``sharpmod.export_paths.ExportDirectoryError`` with the Python wording,
//! falling back to its ``OSError`` base when the class cannot be imported.

use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use numpy::PyReadonlyArray1;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/// Copy one profile column out of Python.
///
/// Non-contiguous ``PyReadonlyArray1`` inputs have no shared slice, so the
/// elements are collected through the array view; this accepts every NumPy
/// layout the boundary can receive.
fn column_values(arr: &PyReadonlyArray1<'_, f64>) -> Vec<f64> {
    arr.as_array().iter().copied().collect()
}

// ---------------------------------------------------------------------------
// Layer-bound combination (sharpmod.sharptab.params.lapse_rate)
// ---------------------------------------------------------------------------

/// The pure half of ``lapse_rate``'s bound-combination tail: four optional
/// layer-bound values combine into ``(T_lower - T_upper) / depth_km``, with
/// any missing or non-finite input -- or a zero height depth -- propagating
/// as ``None`` (the SharpTab ``MISSING`` sentinel).
pub(crate) fn lapse_rate_from_bounds_core(
    t_lower: Option<f64>,
    t_upper: Option<f64>,
    z_lower: Option<f64>,
    z_upper: Option<f64>,
) -> Option<f64> {
    let t_lower = t_lower?;
    let t_upper = t_upper?;
    let z_lower = z_lower?;
    let z_upper = z_upper?;
    if !t_lower.is_finite() || !t_upper.is_finite() || !z_lower.is_finite() || !z_upper.is_finite()
    {
        return None;
    }
    if z_upper == z_lower {
        return None;
    }
    Some((t_lower - t_upper) / ((z_upper - z_lower) / 1000.0))
}

/// Combine four layer-bound values into one lapse rate (degrees C/km).
///
/// ``t_lower``/``t_upper`` are the layer-bound temperatures and
/// ``z_lower``/``z_upper`` the layer-bound heights (m MSL); ``None`` models
/// the ``MISSING`` sentinel, and a missing, non-finite, or degenerate
/// (``z_upper == z_lower``) input returns ``None``.  A present set returns
/// ``(t_lower - t_upper) / ((z_upper - z_lower) / 1000.0)``, so a normally
/// cooling atmosphere yields a positive value.
#[pyfunction]
fn sharptab_lapse_rate_from_bounds(
    t_lower: Option<f64>,
    t_upper: Option<f64>,
    z_lower: Option<f64>,
    z_upper: Option<f64>,
) -> Option<f64> {
    lapse_rate_from_bounds_core(t_lower, t_upper, z_lower, z_upper)
}

// ---------------------------------------------------------------------------
// Height-AGL lapse rate (sharpmod.sharptab.params.lapse_rate, agl=True)
// ---------------------------------------------------------------------------

/// The pure half of ``lapse_rate``'s AGL branch: convert the AGL bounds to
/// MSL with the surface height, interpolate both bound temperatures on the
/// reported height column, and normalize by the AGL depth.
///
/// Returns ``Err`` only for mismatched column lengths (NumPy's ``interp``
/// raises the same way); every atmospheric condition is ``Ok(None)``.
pub(crate) fn lapse_rate_agl_core(
    hght: &[f64],
    tmpc: &[f64],
    sfc: usize,
    lower_agl: f64,
    upper_agl: f64,
) -> Result<Option<f64>, String> {
    if lower_agl == upper_agl {
        return Ok(None);
    }
    if sfc >= hght.len() {
        return Ok(None);
    }
    let sfc_h = hght[sfc];
    if !sfc_h.is_finite() {
        return Ok(None);
    }
    let low_msl = lower_agl + sfc_h;
    let high_msl = upper_agl + sfc_h;
    let temps =
        crate::interpolation::interpolate_1d(&[low_msl, high_msl], hght, tmpc, None, false)?;
    let t_lower = temps[0];
    let t_upper = temps[1];
    if !t_lower.is_finite() || !t_upper.is_finite() {
        return Ok(None);
    }
    let depth_km = (upper_agl - lower_agl) / 1000.0;
    if depth_km == 0.0 {
        return Ok(None);
    }
    Ok(Some((t_lower - t_upper) / depth_km))
}

/// Lapse rate (degrees C/km) across a height layer above ground level.
///
/// ``hght`` and ``tmpc`` are the reported-level columns, ``sfc`` the surface
/// index into them, and ``lower_agl``/``upper_agl`` the layer bounds in
/// metres AGL.  The bounds are converted to MSL with the surface height,
/// both bound temperatures are interpolated between the bracketing reported
/// levels, and the result is ``(T_lower - T_upper) / depth_km`` where the
/// depth is the AGL difference -- the ``agl=True`` branch of
/// ``sharpmod.sharptab.params.lapse_rate``.
///
/// ``None`` (the ``MISSING`` sentinel) is returned when the bounds coincide,
/// the surface index is outside the profile, the surface height is not
/// finite, either bound temperature cannot be interpolated (the profile
/// ends below the bound, or the surface datum is missing/masked), or the
/// depth underflows to zero.  Mismatched column lengths raise
/// ``ValueError``, matching NumPy's ``interp``.
#[pyfunction]
fn sharptab_lapse_rate_agl(
    hght: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    sfc: usize,
    lower_agl: f64,
    upper_agl: f64,
) -> PyResult<Option<f64>> {
    let hght_vec = column_values(&hght);
    let tmpc_vec = column_values(&tmpc);
    lapse_rate_agl_core(&hght_vec, &tmpc_vec, sfc, lower_agl, upper_agl)
        .map_err(PyValueError::new_err)
}

// ---------------------------------------------------------------------------
// Pressure-coordinate lapse rate
// (sharpmod.sharptab.params.lapse_rate, agl=False)
// ---------------------------------------------------------------------------

/// The pure half of ``lapse_rate``'s pressure branch: interpolate the
/// temperatures and heights of both bound pressures in log10 space, then
/// combine them through [`lapse_rate_from_bounds_core`].
///
/// The pressure bounds must be distinct, finite, and positive (log10 is
/// undefined otherwise); every atmospheric miss propagates as ``Ok(None)``.
/// Returns ``Err`` only for mismatched column lengths.
pub(crate) fn lapse_rate_pres_core(
    logp: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    lower_p: f64,
    upper_p: f64,
) -> Result<Option<f64>, String> {
    if lower_p == upper_p {
        return Ok(None);
    }
    if !lower_p.is_finite() || !upper_p.is_finite() {
        return Ok(None);
    }
    if lower_p <= 0.0 || upper_p <= 0.0 {
        return Ok(None);
    }
    let targets = [lower_p.log10(), upper_p.log10()];
    let temps = crate::interpolation::interpolate_1d(&targets, logp, tmpc, None, false)?;
    let heights = crate::interpolation::interpolate_1d(&targets, logp, hght, None, false)?;
    Ok(lapse_rate_from_bounds_core(
        Some(temps[0]),
        Some(temps[1]),
        Some(heights[0]),
        Some(heights[1]),
    ))
}

/// Lapse rate (degrees C/km) across a pressure layer.
///
/// ``logp``, ``hght``, and ``tmpc`` are the reported-level columns with
/// ``logp`` holding ``log10(pres)``; ``lower_p``/``upper_p`` are the layer
/// bounds in hPa.  Both bounds' temperatures and heights are interpolated in
/// log-pressure space between the bracketing reported levels, and the result
/// is ``(T_lower - T_upper) / depth_km`` with the depth derived from the
/// interpolated heights -- the pressure branch of
/// ``sharpmod.sharptab.params.lapse_rate``.
///
/// ``None`` (the ``MISSING`` sentinel) is returned when the bounds coincide,
/// either is non-finite or non-positive, any bound value cannot be
/// interpolated (the bound lies outside the reported range, or its datum is
/// missing/masked), or the interpolated height depth collapses to zero.
/// Mismatched column lengths raise ``ValueError``, matching NumPy's
/// ``interp``.
#[pyfunction]
fn sharptab_lapse_rate_pres(
    logp: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    lower_p: f64,
    upper_p: f64,
) -> PyResult<Option<f64>> {
    let logp_vec = column_values(&logp);
    let hght_vec = column_values(&hght);
    let tmpc_vec = column_values(&tmpc);
    lapse_rate_pres_core(&logp_vec, &hght_vec, &tmpc_vec, lower_p, upper_p)
        .map_err(PyValueError::new_err)
}

// ---------------------------------------------------------------------------
// Export filename extraction
// (sharpmod.export_paths.export_file_path)
// ---------------------------------------------------------------------------

/// A Python ``PureWindowsPath`` drive prefix: one ASCII letter and a colon.
/// The drive is part of the anchor, never of a filename.
fn strip_drive_prefix(component: &str) -> &str {
    let bytes = component.as_bytes();
    if bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' {
        &component[2..]
    } else {
        component
    }
}

/// The pure half of ``export_file_path``'s name check: the final path
/// component of ``Path(os.fspath(default_name))``.
///
/// Components split on both ``/`` and ``\`` (the separators Python's
/// ``Path`` accepts on the Windows platform the application runs on);
/// empty components and ``.`` are dropped and ``..`` is kept as an ordinary
/// component, exactly like ``pathlib``.  A leading ``X:`` drive prefix is
/// anchor, not filename.  ``None`` models every rejected name: no remaining
/// component at all (``""``, ``"."``, ``".."``, ``"/"``, ``"C:"``) or a
/// final ``..`` component.
pub(crate) fn validate_filename_core(raw_path: &str) -> Option<String> {
    let mut components: Vec<&str> = raw_path
        .split(['/', '\\'])
        .filter(|component| !component.is_empty() && *component != ".")
        .collect();
    if let Some(first) = components.first_mut() {
        let stripped = strip_drive_prefix(first);
        if stripped.is_empty() {
            components.remove(0);
        } else {
            *first = stripped;
        }
    }
    let name = components.last().copied()?;
    if name.is_empty() || name == "." || name == ".." {
        return None;
    }
    Some(name.to_string())
}

/// Validate and extract the filename of one export suggestion.
///
/// The name is the final component of ``Path(os.fspath(raw_path))``:
/// separators may be ``/`` or ``\``, trailing separators and ``.``
/// components are trimmed away like ``pathlib.Path.name``, and a leading
/// drive prefix is not a filename.  A name that is empty, ``.`` or ``..``
/// -- including a path that ends in ``..`` or reduces to a root or drive --
/// raises ``ValueError`` with ``an export filename is required`` verbatim,
/// exactly like ``sharpmod.export_paths.export_file_path``.
#[pyfunction]
fn export_validate_filename(raw_path: &str) -> PyResult<String> {
    validate_filename_core(raw_path)
        .ok_or_else(|| PyValueError::new_err("an export filename is required"))
}

// ---------------------------------------------------------------------------
// Export directory write probe
// (sharpmod.export_paths._assert_writable_directory)
// ---------------------------------------------------------------------------

/// Raise ``sharpmod.export_paths.ExportDirectoryError`` with ``message``,
/// falling back to ``OSError`` when the class cannot be imported
/// (``ExportDirectoryError`` subclasses ``OSError``).
fn export_directory_error(py: Python<'_>, message: String) -> PyErr {
    match PyModule::import(py, "sharpmod.export_paths")
        .and_then(|module| module.getattr("ExportDirectoryError"))
        .and_then(|class| class.call1((message.clone(),)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => PyOSError::new_err(message),
    }
}

/// Nanoseconds since the Unix epoch; ``0`` serves when the clock reads
/// before the epoch, which only needs to stay unique per probe.
fn unix_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0)
}

/// The pure half of ``_assert_writable_directory``: prove a directory
/// accepts file creation, writing, and deletion, which ``os.access``
/// cannot establish under Windows ACLs.
///
/// A unique ``.sharpmod-export-write-test-<pid>-<nanos>`` file is created
/// with ``create_new`` (an existing file is never clobbered), ``ok`` is
/// written and flushed, and the probe file is closed and removed again --
/// including when the write itself fails.  The write error is preferred
/// over a later removal error, exactly like the Python ``with`` block.
pub(crate) fn probe_writable_dir_core(directory: &Path) -> std::io::Result<()> {
    let pid = std::process::id();
    for attempt in 0_u32..8 {
        let nanos = unix_nanos().wrapping_add(u128::from(attempt));
        let probe = directory.join(format!(".sharpmod-export-write-test-{pid}-{nanos}"));
        let mut file = match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)
        {
            Ok(file) => file,
            // Two probes raced to one clock tick; retry with a fresh name.
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(err),
        };
        let written = file.write_all(b"ok").and_then(|()| file.flush());
        drop(file);
        let removed = std::fs::remove_file(&probe);
        return written.and(removed);
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::AlreadyExists,
        "unique export write-probe name could not be created",
    ))
}

/// Prove one directory accepts export writes, or raise the export error.
///
/// ``directory`` is probed the way ``_assert_writable_directory`` probes
/// the export folder: a unique ``.sharpmod-export-write-test-<pid>-<nanos>``
/// file is created inside it, ``ok`` is written and flushed, and the probe
/// file is closed and removed again (including when the write fails, so no
/// probe litter survives a failure).  Any I/O failure -- the directory
/// missing, a file standing in its place, ACLs denying creation, a failed
/// write or delete -- raises ``sharpmod.export_paths.ExportDirectoryError``
/// with ``The export folder is not writable: <directory> (<reason>)``,
/// falling back to ``OSError`` when the class cannot be imported.
#[pyfunction]
fn export_probe_writable_dir(py: Python<'_>, directory: &str) -> PyResult<()> {
    probe_writable_dir_core(Path::new(directory)).map_err(|err| {
        export_directory_error(
            py,
            format!("The export folder is not writable: {directory} ({err})"),
        )
    })
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sharptab_lapse_rate_from_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(sharptab_lapse_rate_agl, module)?)?;
    module.add_function(wrap_pyfunction!(sharptab_lapse_rate_pres, module)?)?;
    module.add_function(wrap_pyfunction!(export_validate_filename, module)?)?;
    module.add_function(wrap_pyfunction!(export_probe_writable_dir, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_close(actual: Option<f64>, expected: f64) {
        match actual {
            Some(value) => assert!(
                (value - expected).abs() <= 1.0e-9,
                "expected {expected}, got {value}"
            ),
            None => panic!("expected {expected}, got None"),
        }
    }

    fn assert_missing(actual: Option<f64>) {
        assert!(actual.is_none(), "expected None, got {actual:?}");
    }

    // -----------------------------------------------------------------------
    // Bound combination
    // -----------------------------------------------------------------------

    #[test]
    fn from_bounds_combines_present_layer_bounds() {
        assert_eq!(
            lapse_rate_from_bounds_core(Some(20.0), Some(13.0), Some(0.0), Some(1000.0)),
            Some(7.0)
        );
        assert_eq!(
            lapse_rate_from_bounds_core(Some(20.0), Some(10.0), Some(500.0), Some(1500.0)),
            Some(10.0)
        );
        // An inverted height pair still computes, like Python's arithmetic.
        assert_eq!(
            lapse_rate_from_bounds_core(Some(13.0), Some(20.0), Some(1500.0), Some(500.0)),
            Some(7.0)
        );
        // Isothermal bounds are a zero lapse rate, not missing data.
        assert_eq!(
            lapse_rate_from_bounds_core(Some(15.0), Some(15.0), Some(0.0), Some(2000.0)),
            Some(0.0)
        );
    }

    #[test]
    fn from_bounds_propagates_missing_and_non_finite_inputs() {
        for (t_lower, t_upper, z_lower, z_upper) in [
            (None, Some(13.0), Some(0.0), Some(1000.0)),
            (Some(20.0), None, Some(0.0), Some(1000.0)),
            (Some(20.0), Some(13.0), None, Some(1000.0)),
            (Some(20.0), Some(13.0), Some(0.0), None),
            (Some(f64::NAN), Some(13.0), Some(0.0), Some(1000.0)),
            (Some(20.0), Some(f64::NAN), Some(0.0), Some(1000.0)),
            (Some(20.0), Some(13.0), Some(f64::NAN), Some(1000.0)),
            (Some(20.0), Some(13.0), Some(0.0), Some(f64::NAN)),
            (Some(f64::INFINITY), Some(13.0), Some(0.0), Some(1000.0)),
            (
                Some(20.0),
                Some(13.0),
                Some(f64::NEG_INFINITY),
                Some(1000.0),
            ),
        ] {
            assert_missing(lapse_rate_from_bounds_core(
                t_lower, t_upper, z_lower, z_upper,
            ));
        }
    }

    #[test]
    fn from_bounds_refuses_zero_depth() {
        assert_missing(lapse_rate_from_bounds_core(
            Some(20.0),
            Some(13.0),
            Some(1000.0),
            Some(1000.0),
        ));
    }

    // -----------------------------------------------------------------------
    // Height-AGL lapse rate
    // -----------------------------------------------------------------------

    #[test]
    fn agl_lapse_rate_matches_the_reported_level_case() {
        // 1 km AGL is a reported level: T_sfc=20, T_1km=13 => 7 C/km.
        let hght = [0.0, 500.0, 1000.0, 2000.0];
        let tmpc = [20.0, 16.5, 13.0, 6.0];
        assert_close(
            lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, 1000.0).unwrap(),
            7.0,
        );
        // A surface station at 100 m MSL shifts both targets equally.
        let elevated = [100.0, 600.0, 1100.0, 2100.0];
        assert_close(
            lapse_rate_agl_core(&elevated, &tmpc, 0, 0.0, 1000.0).unwrap(),
            7.0,
        );
    }

    #[test]
    fn agl_lapse_rate_interpolates_between_reported_levels() {
        // 1 km AGL falls between 500 m and 1500 m; linear T => T_1km = 10.
        let hght = [0.0, 500.0, 1500.0];
        let tmpc = [20.0, 15.0, 5.0];
        assert_close(
            lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, 1000.0).unwrap(),
            10.0,
        );
        // 500 m AGL falls between 250 m and 750 m => T_500m = 20.
        let hght = [0.0, 250.0, 750.0];
        let tmpc = [25.0, 22.5, 17.5];
        assert_close(
            lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, 500.0).unwrap(),
            10.0,
        );
    }

    #[test]
    fn agl_lapse_rate_uses_the_surface_height_for_msl_conversion() {
        // Surface elevated 1500 m; AGL conversion must still resolve 1 km.
        let hght = [1500.0, 2500.0, 3500.0];
        let tmpc = [10.0, 4.0, -2.0];
        assert_close(
            lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, 1000.0).unwrap(),
            6.0,
        );
        // A surface index in the middle of the column works the same way.
        let hght = [0.0, 500.0, 1000.0, 2000.0];
        let tmpc = [20.0, 16.5, 13.0, 6.0];
        assert_close(
            lapse_rate_agl_core(&hght, &tmpc, 1, 0.0, 500.0).unwrap(),
            7.0,
        );
    }

    #[test]
    fn agl_lapse_rate_returns_missing_for_undefined_layers() {
        let hght = [0.0, 500.0, 1000.0, 2000.0];
        let tmpc = [20.0, 16.5, 13.0, 6.0];
        // Coinciding bounds.
        assert_missing(lapse_rate_agl_core(&hght, &tmpc, 0, 500.0, 500.0).unwrap());
        // Surface index outside the profile, and an empty column.
        assert_missing(lapse_rate_agl_core(&hght, &tmpc, 4, 0.0, 1000.0).unwrap());
        assert_missing(lapse_rate_agl_core(&[], &[], 0, 0.0, 1000.0).unwrap());
        // A non-finite surface height (masked datum crossed as NaN).
        let masked_hght = [f64::NAN, 500.0, 1000.0, 2000.0];
        assert_missing(lapse_rate_agl_core(&masked_hght, &tmpc, 0, 0.0, 1000.0).unwrap());
        // A masked surface temperature drops the level, so the surface
        // target falls below the remaining bracketing levels.
        let masked_tmpc = [f64::NAN, 16.5, 13.0, 6.0];
        assert_missing(lapse_rate_agl_core(&hght, &masked_tmpc, 0, 0.0, 1000.0).unwrap());
        // Non-finite bounds cannot be interpolated.
        assert_missing(lapse_rate_agl_core(&hght, &tmpc, 0, f64::NAN, 1000.0).unwrap());
        assert_missing(lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, f64::INFINITY).unwrap());
    }

    #[test]
    fn agl_lapse_rate_is_missing_when_the_profile_is_too_shallow() {
        // The highest valid level (800 m AGL) is below 1 km AGL.
        let hght = [0.0, 400.0, 800.0];
        let tmpc = [20.0, 17.0, 14.0];
        assert_missing(lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, 1000.0).unwrap());
    }

    #[test]
    fn agl_lapse_rate_rejects_mismatched_columns() {
        let hght = [0.0, 500.0];
        let tmpc = [20.0];
        assert!(lapse_rate_agl_core(&hght, &tmpc, 0, 0.0, 1000.0).is_err());
    }

    // -----------------------------------------------------------------------
    // Pressure-coordinate lapse rate
    // -----------------------------------------------------------------------

    #[test]
    fn pres_lapse_rate_interpolates_in_log_pressure_space() {
        // pres 1000 and 100 hPa; the upper bound is exactly 10**2.5, midway
        // in log10 space, so T: 20 -> 15 and z: 0 -> 5000.
        let logp = [3.0, 2.0];
        let hght = [0.0, 10000.0];
        let tmpc = [20.0, 10.0];
        let upper = 10.0_f64.powf(2.5);
        assert_close(
            lapse_rate_pres_core(&logp, &hght, &tmpc, 1000.0, upper).unwrap(),
            1.0,
        );
        // Reversing the bounds mirrors both differences, so the same lapse.
        assert_close(
            lapse_rate_pres_core(&logp, &hght, &tmpc, upper, 1000.0).unwrap(),
            1.0,
        );
    }

    #[test]
    fn pres_lapse_rate_hits_reported_levels_exactly() {
        // log10 of 1000, 900, 800, 700 hPa, derived like the Python column.
        let logp = [
            1000.0_f64.log10(),
            900.0_f64.log10(),
            800.0_f64.log10(),
            700.0_f64.log10(),
        ];
        let hght = [100.0, 900.0, 1500.0, 2100.0];
        let tmpc = [20.0, 16.0, 14.0, 8.0];
        // 1000 -> 800 hPa: T 20 -> 14, z 100 -> 1500 => 6/1.4 C/km.
        assert_close(
            lapse_rate_pres_core(&logp, &hght, &tmpc, 1000.0, 800.0).unwrap(),
            6.0 / 1.4,
        );
        // An isothermal column yields a zero lapse rate, not missing data.
        let isothermal = [20.0, 20.0, 20.0, 20.0];
        assert_close(
            lapse_rate_pres_core(&logp, &hght, &isothermal, 1000.0, 800.0).unwrap(),
            0.0,
        );
    }

    #[test]
    fn pres_lapse_rate_returns_missing_for_undefined_layers() {
        let logp = [3.0, 2.0];
        let hght = [0.0, 10000.0];
        let tmpc = [20.0, 10.0];
        // Coinciding bounds.
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, 500.0, 500.0).unwrap());
        // Non-positive pressures have no log10.
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, 0.0, 500.0).unwrap());
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, 1000.0, 0.0).unwrap());
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, -1013.0, 500.0).unwrap());
        // Non-finite bounds.
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, f64::NAN, 500.0).unwrap());
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, f64::INFINITY, 500.0).unwrap());
        // Bounds outside the reported range cannot be interpolated.
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, 2000.0, 100.0).unwrap());
        assert_missing(lapse_rate_pres_core(&logp, &hght, &tmpc, 1000.0, 2000.0).unwrap());
        // The interpolated heights collapse (an isothermal-height column).
        let flat = [100.0, 100.0];
        assert_missing(lapse_rate_pres_core(&logp, &flat, &tmpc, 1000.0, 100.0).unwrap());
    }

    #[test]
    fn pres_lapse_rate_rejects_mismatched_columns() {
        let logp = [3.0, 2.0];
        let hght = [0.0, 10000.0];
        let tmpc = [20.0];
        assert!(lapse_rate_pres_core(&logp, &hght, &tmpc, 1000.0, 500.0).is_err());
    }

    // -----------------------------------------------------------------------
    // Export filename extraction
    // -----------------------------------------------------------------------

    #[test]
    fn filenames_take_the_final_component_across_both_separators() {
        // The spec's anchor cases, matching pathlib.Path.name.
        assert_eq!(validate_filename_core("foo/bar/"), Some("bar".to_string()));
        assert_eq!(
            validate_filename_core("result.csv"),
            Some("result.csv".to_string())
        );
        assert_eq!(
            validate_filename_core("foo\\bar\\baz.csv"),
            Some("baz.csv".to_string())
        );
        assert_eq!(
            validate_filename_core("foo/bar\\baz"),
            Some("baz".to_string())
        );
        assert_eq!(
            validate_filename_core("/abs/path/name.png"),
            Some("name.png".to_string())
        );
        assert_eq!(
            validate_filename_core("D:\\data\\x.npz"),
            Some("x.npz".to_string())
        );
        assert_eq!(
            validate_filename_core("D:/data/x.npz"),
            Some("x.npz".to_string())
        );
    }

    #[test]
    fn filenames_trim_dot_components_and_duplicate_separators() {
        assert_eq!(validate_filename_core("foo/."), Some("foo".to_string()));
        assert_eq!(validate_filename_core("./foo"), Some("foo".to_string()));
        assert_eq!(validate_filename_core("foo//bar"), Some("bar".to_string()));
        // ".." stays an ordinary mid-path component.
        assert_eq!(validate_filename_core("a/../b"), Some("b".to_string()));
        // Dot-prefixed names are filenames, not "." components.
        assert_eq!(
            validate_filename_core(".hidden"),
            Some(".hidden".to_string())
        );
        assert_eq!(validate_filename_core("..foo"), Some("..foo".to_string()));
        assert_eq!(
            validate_filename_core("..foo/..bar"),
            Some("..bar".to_string())
        );
    }

    #[test]
    fn filenames_strip_windows_drive_prefixes() {
        assert_eq!(validate_filename_core("C:foo"), Some("foo".to_string()));
        assert_eq!(
            validate_filename_core("C:\\foo\\bar"),
            Some("bar".to_string())
        );
        assert_eq!(validate_filename_core("c:foo/bar"), Some("bar".to_string()));
        // A drive alone names no file.
        assert_eq!(validate_filename_core("C:"), None);
        assert_eq!(validate_filename_core("C:."), None);
        // Mid-path colons are ordinary filename characters.
        assert_eq!(
            validate_filename_core("foo/bar/C:baz"),
            Some("C:baz".to_string())
        );
    }

    #[test]
    fn filenames_reject_empty_dot_and_dotdot_names() {
        for raw in [
            "",
            ".",
            "..",
            "/",
            "\\",
            "C:",
            "C:/",
            "foo/..",
            "..\\.",
            "foo/bar/..",
        ] {
            assert!(
                validate_filename_core(raw).is_none(),
                "{raw:?} must be rejected"
            );
        }
    }

    // -----------------------------------------------------------------------
    // Export directory write probe
    // -----------------------------------------------------------------------

    #[test]
    fn write_probe_round_trips_a_writable_directory() {
        let root = std::env::temp_dir().join(format!(
            ".sharpmod-lapse-paths-probe-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&root).unwrap();
        assert!(probe_writable_dir_core(&root).is_ok());
        let leftovers: Vec<_> = std::fs::read_dir(&root)
            .unwrap()
            .filter_map(|entry| entry.ok())
            .filter(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".sharpmod-export-write-test-")
            })
            .collect();
        assert!(leftovers.is_empty(), "the probe file must be removed");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn write_probe_reports_missing_and_non_directory_paths() {
        let missing = std::env::temp_dir().join("sharpmod-lapse-paths-missing-dir");
        assert!(probe_writable_dir_core(&missing).is_err());
        let blocked = std::env::temp_dir().join(format!(
            ".sharpmod-lapse-paths-blocked-{}",
            std::process::id()
        ));
        std::fs::write(&blocked, b"not a directory").unwrap();
        assert!(probe_writable_dir_core(&blocked).is_err());
        let _ = std::fs::remove_file(&blocked);
    }
}
