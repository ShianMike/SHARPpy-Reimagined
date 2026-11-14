//! Browser numeric field encoding with the existing uint16 missing-cell sentinel.

use crate::numeric_grid::Buffer;
use rayon::prelude::*;

pub(crate) fn quantize(values: &Buffer, count: usize) -> (Vec<u8>, f64, f64, f64) {
    let mut limits = None;
    for index in 0..count {
        let value = values.value(index);
        if !value.is_finite() {
            continue;
        }
        limits = Some(match limits {
            None => (value, value),
            Some((low, high)) => (value.min(low), value.max(high)),
        });
    }
    let (low, high) = limits.unwrap_or((0.0, 0.0));
    let scale = if high > low {
        (high - low) / 65534.0
    } else {
        1.0
    };
    let mut bytes = vec![0; count * 2];
    bytes
        .par_chunks_mut(2)
        .enumerate()
        .with_min_len(4096)
        .for_each(|(index, bytes)| {
            let value = values.value(index);
            let cell = if !value.is_finite() {
                u16::MAX
            } else {
                let rounded = if values.single() {
                    f64::from(((value as f32 - low as f32) / scale as f32).round_ties_even())
                } else {
                    ((value - low) / scale).round_ties_even()
                };
                // NumPy's float-to-uint16 assignment maps invalid arithmetic to zero.
                if rounded.is_finite() {
                    (rounded as u64 & 65535) as u16
                } else {
                    0
                }
            };
            bytes.copy_from_slice(&cell.to_le_bytes());
        });
    (bytes, scale, low, high)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cells_keep_little_endian_range_missing_values_and_constant_fields() {
        let values = Buffer::Single(vec![-10.0, 30.0, 70.0, f32::NAN, f32::INFINITY]);
        let (bytes, scale, low, high) = quantize(&values, 5);
        assert_eq!(bytes, [0, 0, 255, 127, 254, 255, 255, 255, 255, 255]);
        assert_eq!((scale, low, high), (80.0 / 65534.0, -10.0, 70.0));
        assert_eq!(
            quantize(&Buffer::Double(vec![42.0; 3]), 3),
            (vec![0; 6], 1.0, 42.0, 42.0)
        );
        assert_eq!(
            quantize(&Buffer::Double(vec![f64::NAN; 2]), 2),
            (vec![255; 4], 1.0, 0.0, 0.0)
        );
        assert_eq!(
            quantize(&Buffer::Single(Vec::new()), 0),
            (Vec::new(), 1.0, 0.0, 0.0)
        );
    }
    #[test]
    fn quantization_uses_even_ties_and_bounds_ordinary_reconstruction_error() {
        let values = Buffer::Double(vec![0.0, 0.5, 1.5, 2.5, 3.5, 65534.0]);
        let (bytes, scale, low, _) = quantize(&values, 6);
        let cells = bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|b| u16::from_le_bytes([b[0], b[1]]))
            .collect::<Vec<_>>();
        assert_eq!(cells, [0, 0, 2, 2, 4, 65534]);
        for (i, cell) in cells.into_iter().enumerate() {
            assert!((f64::from(cell) * scale + low - values.value(i)).abs() <= scale / 2.0);
        }
    }
}
