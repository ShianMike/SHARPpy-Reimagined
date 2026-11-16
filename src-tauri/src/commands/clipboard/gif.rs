//! Bounded structural validation at the clipboard IPC boundary.

const MAX_BYTES: usize = 16 * 1024 * 1024;
const MAX_DIMENSION: u16 = 8192;
const MAX_FRAMES: usize = 10_000;

/// Validate the complete container without allocating or decoding its pixels.
pub(super) fn validate(bytes: &[u8]) -> Result<(), String> {
    validate_structure(bytes).map_err(|error| format!("Invalid GIF: {error}"))
}

fn validate_structure(bytes: &[u8]) -> Result<(), &'static str> {
    if bytes.len() > MAX_BYTES {
        return Err("the animation exceeds the 16 MiB clipboard limit");
    }
    if bytes.len() < 14 || !matches!(&bytes[..6], b"GIF87a" | b"GIF89a") {
        return Err("missing GIF header or logical screen");
    }
    let width = u16::from_le_bytes([bytes[6], bytes[7]]);
    let height = u16::from_le_bytes([bytes[8], bytes[9]]);
    if width == 0 || height == 0 || width > MAX_DIMENSION || height > MAX_DIMENSION {
        return Err("unsupported image dimensions");
    }
    let packed = bytes[10];
    let global_color_table = packed & 0x80 != 0;
    let mut pos = 13;
    if global_color_table {
        skip(bytes, &mut pos, color_table_len(packed))?;
    }
    let mut frames = 0;
    loop {
        match read_byte(bytes, &mut pos)? {
            0x21 => {
                read_byte(bytes, &mut pos)?; // extension label
                skip_blocks(bytes, &mut pos)?;
            }
            0x2c => {
                let descriptor = bytes
                    .get(pos..pos + 9)
                    .ok_or("truncated image descriptor")?;
                pos += 9;
                let left = u16::from_le_bytes([descriptor[0], descriptor[1]]) as u32;
                let top = u16::from_le_bytes([descriptor[2], descriptor[3]]) as u32;
                let frame_width = u16::from_le_bytes([descriptor[4], descriptor[5]]) as u32;
                let frame_height = u16::from_le_bytes([descriptor[6], descriptor[7]]) as u32;
                if frame_width == 0
                    || frame_height == 0
                    || left + frame_width > width as u32
                    || top + frame_height > height as u32
                {
                    return Err("image frame is outside the logical screen");
                }
                let local = descriptor[8];
                if local & 0x80 != 0 {
                    skip(bytes, &mut pos, color_table_len(local))?;
                } else if !global_color_table {
                    return Err("image frame has no color table");
                }
                if !(2..=8).contains(&read_byte(bytes, &mut pos)?) {
                    return Err("invalid LZW code size");
                }
                if !skip_blocks(bytes, &mut pos)? {
                    return Err("image frame has no image data");
                }
                frames += 1;
                if frames > MAX_FRAMES {
                    return Err("too many image frames");
                }
            }
            0x3b if frames > 0 && pos == bytes.len() => return Ok(()),
            0x3b => return Err("missing frame or trailing data"),
            _ => return Err("unexpected GIF block"),
        }
    }
}

fn color_table_len(packed: u8) -> usize {
    3 * (1usize << ((packed & 7) + 1))
}

fn skip(bytes: &[u8], pos: &mut usize, len: usize) -> Result<(), &'static str> {
    *pos = pos
        .checked_add(len)
        .filter(|end| *end <= bytes.len())
        .ok_or("truncated GIF data")?;
    Ok(())
}

fn read_byte(bytes: &[u8], pos: &mut usize) -> Result<u8, &'static str> {
    let value = *bytes.get(*pos).ok_or("truncated GIF data")?;
    *pos += 1;
    Ok(value)
}

fn skip_blocks(bytes: &[u8], pos: &mut usize) -> Result<bool, &'static str> {
    let mut nonempty = false;
    loop {
        let len = read_byte(bytes, pos)? as usize;
        if len == 0 {
            return Ok(nonempty);
        }
        nonempty = true;
        skip(bytes, pos, len)?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // One opaque pixel with a global two-color palette.
    const GIF: &[u8] = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b";

    #[test]
    fn accepts_complete_static_and_multiframe_gifs() {
        assert!(validate(GIF).is_ok());
        let mut animation = GIF[..GIF.len() - 1].to_vec();
        animation.extend_from_slice(&GIF[19..]);
        assert!(validate(&animation).is_ok());
        animation[3..6].copy_from_slice(b"87a");
        assert!(validate(&animation).is_ok());
    }

    #[test]
    fn rejects_corruption_and_zero_sized_images() {
        for end in 0..GIF.len() {
            assert!(
                validate(&GIF[..end]).is_err(),
                "accepted truncation at {end}"
            );
        }
        let mut bad = GIF.to_vec();
        bad[6] = 0;
        assert!(validate(&bad).is_err());
        let mut bad = GIF.to_vec();
        bad[23] = 2; // frame extends past the logical screen
        assert!(validate(&bad).is_err());
        let mut bad = GIF.to_vec();
        bad.push(0);
        assert!(validate(&bad).is_err());
    }

    #[test]
    fn enforces_size_and_requires_image_data() {
        assert!(validate(&vec![0; MAX_BYTES + 1]).is_err());
        let mut bad = GIF.to_vec();
        bad[30] = 0; // empty image sub-block
        assert!(validate(&bad).is_err());
    }
}
