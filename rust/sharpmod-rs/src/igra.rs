//! IGRA v2 fixed-width records in the application's existing units.

use std::borrow::Cow;

const MISSING: f64 = -9999.;

#[derive(Debug)]
pub(crate) enum Error {
    PastEnd,
    DivisionByZero,
    Overflow,
}

pub(crate) struct Header {
    pub station: String,
    pub date: [i64; 4],
    pub release: i64,
    pub levels: i64,
    pub sources: [String; 2],
    pub position: [f64; 2],
    pub line: usize,
}

pub(crate) struct Level {
    pub values: [f64; 6],
    pub surface: bool,
    pub reconstructed: bool,
}

pub(crate) struct SoundingRows {
    pub columns: [Vec<f64>; 6],
    pub reconstructed: usize,
    pub dropped: usize,
}

fn field(line: &str, start: usize, end: usize) -> Cow<'_, str> {
    if line.is_ascii() {
        Cow::Borrowed(line.get(start..end.min(line.len())).unwrap_or(""))
    } else {
        Cow::Owned(line.chars().skip(start).take(end - start).collect())
    }
}

fn integer(line: &str, start: usize, end: usize, default: i64) -> i64 {
    let field = field(line, start, end);
    let token = field.trim();
    token.parse().unwrap_or_else(|_| {
        crate::parsing::python_float_literal(token)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(default)
    })
}

fn usable(value: i64) -> bool {
    !matches!(value, -9999 | -8888)
}

fn lines(text: &str) -> Box<dyn Iterator<Item = &str> + '_> {
    let bytes = text.as_bytes();
    // Published ASCII LF/CRLF archives need no allocated list of every record.
    if text.is_ascii()
        && memchr::memchr3(11, 12, 28, bytes).is_none()
        && memchr::memchr2(29, 30, bytes).is_none()
        && memchr::memchr_iter(b'\r', bytes).all(|index| bytes.get(index + 1) == Some(&b'\n'))
    {
        Box::new(text.lines())
    } else {
        Box::new(crate::parsing::python_splitlines(text).into_iter())
    }
}

pub(crate) fn header(line: &str, index: usize) -> Header {
    let position = [(55, 62, -98.8888), (63, 71, -998.8888)].map(|(start, end, mobile)| {
        let raw = integer(line, start, end, -9999);
        let coordinate = raw as f64 / 10000.;
        if usable(raw) && (coordinate - mobile).abs() > 1e-4 {
            coordinate
        } else {
            f64::NAN
        }
    });
    Header {
        station: field(line, 1, 12).trim().to_uppercase(),
        date: [(13, 17, 0), (18, 20, 0), (21, 23, 0), (24, 26, 99)]
            .map(|(start, end, default)| integer(line, start, end, default)),
        release: integer(line, 27, 31, 9999),
        levels: integer(line, 32, 36, 0),
        sources: [
            field(line, 37, 45).trim().into(),
            field(line, 46, 54).trim().into(),
        ],
        position,
        line: index,
    }
}

pub(crate) fn headers(text: &str) -> Vec<Header> {
    lines(text)
        .enumerate()
        .filter(|(_, line)| line.starts_with('#'))
        .map(|(index, line)| header(line, index))
        .collect()
}

pub(crate) fn dewpoint(temperature: f64, humidity: f64) -> Result<f64, Error> {
    if !temperature.is_finite() || !humidity.is_finite() || humidity <= 0. {
        return Ok(MISSING);
    }
    if temperature + 243.04 == 0. {
        return Err(Error::DivisionByZero);
    }
    let ratio = (17.625 * temperature) / (temperature + 243.04);
    let exponential = ratio.exp();
    if ratio.is_finite() && exponential.is_infinite() {
        return Err(Error::Overflow);
    }
    let saturation = 6.1094 * exponential;
    let vapour = saturation * humidity.clamp(0., 100.) / 100.;
    if vapour <= 0. {
        return Ok(MISSING);
    }
    let logarithm = (vapour / 6.1094).ln();
    let denominator = 17.625 - logarithm;
    Ok(if denominator.abs() < 1e-12 {
        MISSING
    } else {
        (243.04 * logarithm) / denominator
    })
}

pub(crate) fn level(line: &str) -> Result<Option<Level>, Error> {
    let pressure = integer(line, 9, 15, -9999);
    if !usable(pressure) || pressure <= 0 {
        return Ok(None);
    }
    let [height, temperature, humidity, depression, direction, speed] =
        [(16, 21), (22, 27), (28, 33), (34, 39), (40, 45), (46, 51)]
            .map(|(start, end)| integer(line, start, end, -9999));
    let temperature = if usable(temperature) {
        temperature as f64 / 10.
    } else {
        MISSING
    };
    let mut dewpoint_value = MISSING;
    let mut reconstructed = false;
    if temperature != MISSING {
        if usable(depression) {
            dewpoint_value = temperature - depression as f64 / 10.;
        } else if usable(humidity) {
            dewpoint_value = dewpoint(temperature, humidity as f64 / 10.)?;
            reconstructed = dewpoint_value != MISSING;
        }
    }
    Ok(Some(Level {
        values: [
            pressure as f64 / 100.,
            if usable(height) {
                height as f64
            } else {
                MISSING
            },
            temperature,
            dewpoint_value,
            if usable(direction) && (0..=360).contains(&direction) {
                direction as f64
            } else {
                MISSING
            },
            if usable(speed) {
                speed as f64 / 10. * (3600. / 1852.)
            } else {
                MISSING
            },
        ],
        surface: field(line, 1, 2) == "1",
        reconstructed,
    }))
}

pub(crate) fn sounding_rows(text: &str, index: usize, count: i64) -> Result<SoundingRows, Error> {
    let mut lines = lines(text);
    if lines.nth(index).is_none() {
        return Err(Error::PastEnd);
    }
    let mut result = SoundingRows {
        columns: std::array::from_fn(|_| Vec::new()),
        reconstructed: 0,
        dropped: 0,
    };
    let mut last_pressure = f64::INFINITY;
    for line in lines.take(count.max(0) as usize) {
        if line.starts_with('#') {
            break;
        }
        if let Some(level) = level(line)?.filter(|level| level.values[0] < last_pressure) {
            last_pressure = level.values[0];
            for (column, value) in result.columns.iter_mut().zip(level.values) {
                column.push(value);
            }
            result.reconstructed += usize::from(level.reconstructed);
        } else {
            result.dropped += 1;
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests;
