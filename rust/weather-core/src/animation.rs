//! Lossless frame encoding shared by native desktop and browser exports.

use base64::{engine::general_purpose::STANDARD, Engine};
use std::io::Cursor;

// A single request and its reply must fit the existing eight-MiB bridge.
pub const MAX_FRAME_BYTES: usize = 6 * 1024 * 1024 - 16384;
const MAX_PIXELS: usize = 16 * 1024 * 1024;

#[derive(Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WebpFrame {
    pub width: u32,
    pub height: u32,
    pub data_base64: String,
}

pub fn encode_png(encoded: &str) -> Result<WebpFrame, String> {
    if encoded.len() > MAX_FRAME_BYTES * 4 / 3 + 4 {
        return Err("This frame is too large. Choose a smaller export resolution.".into());
    }
    let png = STANDARD
        .decode(encoded)
        .map_err(|_| "Expected a base64 PNG frame.")?;
    if png.len() > MAX_FRAME_BYTES {
        return Err("This frame exceeds the export request limit.".into());
    }
    let (width, height, webp) = encode_bytes(&png)?;
    Ok(WebpFrame {
        width,
        height,
        data_base64: STANDARD.encode(webp),
    })
}

fn encode_bytes(png: &[u8]) -> Result<(u32, u32, Vec<u8>), String> {
    let mut decoder = png::Decoder::new_with_limits(
        Cursor::new(png),
        png::Limits {
            bytes: MAX_PIXELS * 5,
        },
    );
    decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut reader = decoder
        .read_info()
        .map_err(|error| format!("Invalid PNG: {error}"))?;
    let info = reader.info();
    let (width, height) = (info.width, info.height);
    if width == 0
        || height == 0
        || width > 16383
        || height > 16383
        || width as usize * height as usize > MAX_PIXELS
        || info.animation_control.is_some()
    {
        return Err("Expected a still PNG within the export pixel limit.".into());
    }
    let size = reader
        .output_buffer_size()
        .ok_or("PNG dimensions exceed the decoder limit.")?;
    let mut pixels = vec![0; size];
    let frame = reader
        .next_frame(&mut pixels)
        .map_err(|error| error.to_string())?;
    pixels.truncate(frame.buffer_size());
    let mut rgba = Vec::with_capacity(width as usize * height as usize * 4);
    match frame.color_type {
        png::ColorType::Rgba => rgba = pixels,
        png::ColorType::Rgb => pixels
            .as_chunks::<3>()
            .0
            .iter()
            .for_each(|p| rgba.extend_from_slice(&[p[0], p[1], p[2], 255])),
        png::ColorType::Grayscale => pixels
            .iter()
            .for_each(|p| rgba.extend_from_slice(&[*p, *p, *p, 255])),
        png::ColorType::GrayscaleAlpha => pixels
            .as_chunks::<2>()
            .0
            .iter()
            .for_each(|p| rgba.extend_from_slice(&[p[0], p[0], p[0], p[1]])),
        png::ColorType::Indexed => return Err("PNG palette expansion failed.".into()),
    }
    let mut output = Vec::new();
    image_webp::WebPEncoder::new(&mut output)
        .encode(&rgba, width, height, image_webp::ColorType::Rgba8)
        .map_err(|error| error.to_string())?;
    if output.len() > MAX_FRAME_BYTES {
        return Err("The lossless frame exceeds the export limit. Choose a smaller size.".into());
    }
    Ok((width, height, output))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lossless_output_preserves_more_than_256_colors_and_alpha() {
        let pixels: Vec<u8> = (0..1024)
            .flat_map(|n| [(n % 256) as u8, (n / 4) as u8, (n / 256 * 60) as u8, 128])
            .collect();
        let png = crate::raster_png::encode(32, 32, &pixels, MAX_FRAME_BYTES).unwrap();
        let result = encode_png(&STANDARD.encode(png)).unwrap();
        assert_eq!((result.width, result.height), (32, 32));
        let webp = STANDARD.decode(result.data_base64).unwrap();
        assert_eq!(&webp[12..16], b"VP8L");
        let mut decoder = image_webp::WebPDecoder::new(Cursor::new(webp)).unwrap();
        let mut decoded = vec![0; pixels.len()];
        decoder.read_image(&mut decoded).unwrap();
        assert_eq!(decoded, pixels);
    }

    #[test]
    fn rejects_non_png_oversized_and_invalid_frame_data() {
        assert!(encode_png("not base64").is_err());
        assert!(encode_png(&STANDARD.encode(b"RIFFWEBP")).is_err());
        assert!(encode_png(&"A".repeat(MAX_FRAME_BYTES * 4 / 3 + 8)).is_err());
    }
}
