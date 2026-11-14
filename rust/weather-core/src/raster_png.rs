//! Encode model-overlay RGBA pixels with the native PNG runtime.

pub fn encode(width: u32, height: u32, pixels: &[u8], max_bytes: usize) -> Result<Vec<u8>, String> {
    let count = (width as usize)
        .checked_mul(height as usize)
        .and_then(|n| n.checked_mul(4));
    if width == 0 || height == 0 || count != Some(pixels.len()) {
        return Err("field image dimensions do not match its RGBA pixels".into());
    }
    let mut output = Vec::new();
    let mut encoder = png::Encoder::new(&mut output, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_compression(png::Compression::Fast);
    let mut writer = encoder.write_header().map_err(|error| error.to_string())?;
    writer
        .write_image_data(pixels)
        .map_err(|error| error.to_string())?;
    writer.finish().map_err(|error| error.to_string())?;
    if output.len() > max_bytes {
        return Err(format!(
            "field image is {} bytes, over the {max_bytes}-byte overlay limit",
            output.len()
        ));
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn encoded_pixels_keep_every_color_and_transparent_cell() {
        let pixels = [255, 0, 0, 255, 0, 255, 0, 0, 0, 0, 255, 128, 1, 2, 3, 255];
        let encoded = encode(2, 2, &pixels, 10000).unwrap();
        assert_eq!(&encoded[..8], b"\x89PNG\r\n\x1a\n");
        let mut reader = png::Decoder::new(std::io::Cursor::new(encoded))
            .read_info()
            .unwrap();
        let mut output = vec![0; reader.output_buffer_size().unwrap()];
        let frame = reader.next_frame(&mut output).unwrap();
        assert_eq!((frame.width, frame.height), (2, 2));
        assert_eq!(&output[..frame.buffer_size()], pixels);
    }
    #[test]
    fn invalid_dimensions_and_over_budget_images_are_errors() {
        assert!(encode(0, 1, &[], 1000).is_err());
        assert!(encode(2, 2, &[0; 4], 1000).is_err());
        assert!(encode(1, 1, &[0; 4], 1)
            .unwrap_err()
            .contains("overlay limit"));
    }
}
