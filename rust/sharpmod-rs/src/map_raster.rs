//! Model-overlay colors and raster contours, independent of Python rendering.

use crate::numeric_grid::Buffer;
use rayon::prelude::*;

pub(crate) struct Palette {
    stops: Vec<(f64, [u8; 3])>,
    stepped: bool,
    floor: Option<f64>,
    ceiling: Option<f64>,
}

pub(crate) fn rgb(hex: &str) -> Result<[u8; 3], String> {
    let bytes = hex.as_bytes();
    if bytes.len() != 7 || bytes[0] != b'#' {
        return Err("expected a #rrggbb color".into());
    }
    let mut output = [0; 3];
    for (channel, pair) in bytes[1..].as_chunks::<2>().0.iter().enumerate() {
        let digit = |byte: u8| (byte as char).to_digit(16).ok_or("invalid hex color");
        output[channel] = (digit(pair[0])? * 16 + digit(pair[1])?) as u8;
    }
    Ok(output)
}

impl Palette {
    pub fn new(
        stops: Vec<(f64, String)>,
        stepped: bool,
        floor: Option<f64>,
        ceiling: Option<f64>,
    ) -> Result<Self, String> {
        if stops.len() < 2
            || stops.iter().any(|(value, _)| !value.is_finite())
            || stops.windows(2).any(|pair| pair[1].0 <= pair[0].0)
        {
            return Err("palette stops must be finite and strictly ascending".into());
        }
        let stops = stops
            .into_iter()
            .map(|(value, color)| Ok((value, rgb(&color)?)))
            .collect::<Result<_, String>>()?;
        Ok(Self {
            stops,
            stepped,
            floor,
            ceiling,
        })
    }

    pub fn limits(&self) -> (f64, f64) {
        (self.stops[0].0, self.stops[self.stops.len() - 1].0)
    }

    fn band(&self, value: f64) -> [u8; 3] {
        let index = if value.is_nan() {
            self.stops.len() - 1
        } else {
            self.stops
                .partition_point(|(edge, _)| *edge <= value)
                .saturating_sub(1)
        };
        self.stops[index].1
    }

    fn interpolate(&self, value: f64) -> [u8; 3] {
        let high = self.stops.partition_point(|(edge, _)| *edge <= value);
        if high == 0 {
            return self.stops[0].1;
        }
        if high == self.stops.len() {
            return self.stops[high - 1].1;
        }
        let (a, first) = self.stops[high - 1];
        let (b, last) = self.stops[high];
        std::array::from_fn(|channel| {
            let slope = (f64::from(last[channel]) - f64::from(first[channel])) / (b - a);
            (slope * (value - a) + f64::from(first[channel])).clamp(0.0, 255.0) as u8
        })
    }

    pub fn lut(&self, size: usize) -> Result<Vec<u8>, String> {
        if !(2..=65536).contains(&size) {
            return Err("color lookup size must be between 2 and 65536".into());
        }
        let (low, high) = self.limits();
        let step = (high - low) / (size - 1) as f64;
        let mut output = Vec::with_capacity(size * 4);
        for index in 0..size {
            let value = if index == size - 1 {
                high
            } else if step == 0.0 {
                index as f64 / (size - 1) as f64 * (high - low) + low
            } else {
                index as f64 * step + low
            };
            let color = if self.stepped {
                self.band(value)
            } else {
                self.interpolate(value)
            };
            output.extend_from_slice(&[color[0], color[1], color[2], 255]);
        }
        Ok(output)
    }

    pub fn colorize(&self, values: &Buffer, cells: usize, size: usize) -> Result<Vec<u8>, String> {
        let table = if self.stepped {
            Vec::new()
        } else {
            self.lut(size)?
        };
        let (low, high) = self.limits();
        let span = if high > low { high - low } else { 1.0 };
        let floor = self.floor.unwrap_or(low);
        let single = values.single();
        let mut output = vec![0; cells.checked_mul(4).ok_or("raster dimensions overflow")?];
        output
            .par_chunks_mut(4)
            .enumerate()
            .with_min_len(4096)
            .for_each(|(index, pixel)| {
                let value = values.value(index);
                let color = if self.stepped {
                    self.band(value)
                } else {
                    let normalized = if single {
                        f64::from(
                            ((value as f32 - low as f32) / span as f32).clamp(0.0, 1.0)
                                * (size - 1) as f32,
                        )
                    } else {
                        ((value - low) / span).clamp(0.0, 1.0) * (size - 1) as f64
                    };
                    let slot = if normalized.is_nan() {
                        0
                    } else {
                        normalized as usize
                    };
                    [table[4 * slot], table[4 * slot + 1], table[4 * slot + 2]]
                };
                let hidden = !value.is_finite()
                    || if single {
                        (value as f32) < floor as f32
                            || self
                                .ceiling
                                .is_some_and(|limit| value as f32 > limit as f32)
                    } else {
                        value < floor || self.ceiling.is_some_and(|limit| value > limit)
                    };
                pixel.copy_from_slice(&[
                    color[0],
                    color[1],
                    color[2],
                    if hidden { 0 } else { 255 },
                ]);
            });
        Ok(output)
    }
}

pub(crate) fn draw_contours(
    rgba: &mut [u8],
    values: &Buffer,
    height: usize,
    width: usize,
    interval: f64,
    color: [u8; 3],
    thickness: f64,
) {
    let cells = height * width;
    let level = |index| {
        let value = values.value(index);
        if values.single() {
            f64::from((value as f32 / interval as f32).floor())
        } else {
            (value / interval).floor()
        }
    };
    let mut edges = vec![false; cells];
    edges
        .par_iter_mut()
        .enumerate()
        .with_min_len(4096)
        .for_each(|(index, edge)| {
            if !values.value(index).is_finite() {
                return;
            }
            let current = level(index);
            *edge = (index % width + 1 < width
                && values.value(index + 1).is_finite()
                && current != level(index + 1))
                || (index / width + 1 < height
                    && values.value(index + width).is_finite()
                    && current != level(index + width));
        });
    rgba.par_chunks_mut(4)
        .enumerate()
        .with_min_len(4096)
        .for_each(|(index, pixel)| {
            let edge = edges[index]
                || (thickness >= 2.0
                    && ((index >= width && edges[index - width])
                        || (index % width > 0 && edges[index - 1])));
            if edge {
                pixel.copy_from_slice(&[color[0], color[1], color[2], 255]);
            }
        });
}

#[cfg(test)]
mod tests;
