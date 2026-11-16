//! Machine-readable reports for PNGs produced by the headless renderer.

use std::{
    fs::File,
    io::{self, Read},
    path::{self, PathBuf},
};

use pyo3::{prelude::*, types::PyBytes};
use serde_json::json;

fn dimensions(header: &[u8]) -> io::Result<(u32, u32)> {
    if header.len() < 24 || &header[..16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "the sounding renderer did not produce a PNG image",
        ));
    }
    let width = u32::from_be_bytes(header[16..20].try_into().unwrap());
    let height = u32::from_be_bytes(header[20..24].try_into().unwrap());
    if width == 0 || height == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "the sounding renderer did not produce a PNG image with valid dimensions",
        ));
    }
    Ok((width, height))
}

#[pyfunction]
fn sounding_png_size(data: &Bound<'_, PyBytes>) -> PyResult<(u32, u32)> {
    dimensions(data.as_bytes()).map_err(|error| {
        let py = data.py();
        match PyModule::import(py, "sharpmod.bridge.errors")
            .and_then(|module| module.getattr("BridgeError"))
            .and_then(|class| class.call1(("RENDER_FAILED", error.to_string(), false)))
        {
            Ok(error) => PyErr::from_value(error),
            Err(_) => pyo3::exceptions::PyValueError::new_err(error.to_string()),
        }
    })
}

#[pyfunction]
fn sounding_png_report(output: PathBuf, mode: &str) -> PyResult<String> {
    let output = path::absolute(output)?;
    let mut file = File::open(&output)?;
    let mut header = [0; 24];
    file.read_exact(&mut header)?;
    let (width, height) = dimensions(&header)?;
    Ok(json!({
        "schemaVersion": 1,
        "path": output.to_str().ok_or_else(|| io::Error::new(
            io::ErrorKind::InvalidData, "the PNG path is not valid Unicode",
        ))?,
        "contentType": "image/png",
        "soundingMode": mode,
        "width": width,
        "height": height,
        "byteLength": file.metadata()?.len(),
    })
    .to_string())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sounding_png_report, module)?)?;
    module.add_function(wrap_pyfunction!(sounding_png_size, module)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_png_dimensions_in_network_byte_order() {
        let header = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x0c\xbc\0\0\x08\x96";
        assert_eq!(dimensions(header).unwrap(), (3260, 2198));
    }

    #[test]
    fn rejects_non_png_or_empty_headers() {
        let mut header = *b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x0c\xbc\0\0\0\0";
        assert!(dimensions(&header).is_err());
        header[23] = 32;
        header[15] = b'X';
        assert!(dimensions(&header).is_err());
    }

    #[test]
    fn bridge_rejects_short_headers_wrong_ihdr_lengths_and_zero_dimensions() {
        let header = *b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x0c\xbc\0\0\x08\x96";
        for len in 0..24 {
            assert!(dimensions(&header[..len]).is_err());
        }
        for offset in [11, 16, 20] {
            let mut invalid = header;
            if offset == 11 {
                invalid[offset] = 12;
            } else {
                invalid[offset..offset + 4].fill(0);
            }
            assert!(dimensions(&invalid).is_err());
        }
        Python::initialize();
        Python::attach(|py| {
            let bytes = PyBytes::new(py, &header);
            assert_eq!(sounding_png_size(&bytes).unwrap(), (3260, 2198));
            let error = sounding_png_size(&PyBytes::new(py, b"not png")).unwrap_err();
            assert_eq!(
                error
                    .value(py)
                    .getattr("code")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                "RENDER_FAILED"
            );
        });
    }
}
