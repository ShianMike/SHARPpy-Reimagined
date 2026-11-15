//! Machine-readable reports for PNGs produced by the headless renderer.

use std::{
    fs::File,
    io::{self, Read},
    path::{self, PathBuf},
};

use pyo3::prelude::*;
use serde_json::json;

fn dimensions(header: &[u8; 24]) -> io::Result<(u32, u32)> {
    let width = u32::from_be_bytes(header[16..20].try_into().unwrap());
    let height = u32::from_be_bytes(header[20..24].try_into().unwrap());
    if &header[..16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" || width == 0 || height == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "the sounding renderer did not produce a PNG image with valid dimensions",
        ));
    }
    Ok((width, height))
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
    module.add_function(wrap_pyfunction!(sounding_png_report, module)?)
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
}
