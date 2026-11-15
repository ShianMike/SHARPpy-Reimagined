//! Display-only magnitude scales. These do not change diagnostic thresholds.
use super::{panels::Palette, *};

// Neutral -> progressively stronger cold, warm, magnitude and moisture hues.
const RAMPS: [[[u8; 3]; 4]; 4] = [
    [
        [255, 255, 255],
        [160, 216, 255],
        [70, 177, 255],
        [197, 140, 231],
    ],
    [
        [255, 255, 255],
        [255, 225, 107],
        [255, 156, 76],
        [255, 95, 112],
    ],
    [
        [255, 255, 255],
        [255, 255, 0],
        [255, 64, 64],
        [255, 64, 255],
    ],
    [
        [255, 255, 255],
        [160, 216, 255],
        [64, 220, 235],
        [100, 255, 177],
    ],
];

#[derive(Clone, Copy)]
pub(super) enum Scale {
    Temperature,
    WindChill,
    Energy,
    ColdDepth,
    WarmDepth,
    Humidity,
    Omega,
    Oprh,
    Wind,
    SnowRatio,
    DgzWater,
    ColumnWater,
    SnowSquall,
    LapseRate,
}

fn hex([r, g, b]: [u8; 3]) -> String {
    format!("#{r:02x}{g:02x}{b:02x}")
}

pub(super) struct ValueColors {
    ramps: [[[u8; 3]; 4]; 4],
    neutral: [u8; 3],
}

impl ValueColors {
    pub fn from_palette(py: Python<'_>, palette: &Palette) -> PyResult<Self> {
        // Use the existing theme-contrast policy once at the palette boundary.
        let resolve =
            PyModule::import(py, "sharpmod.viz.colors")?.getattr("resolve_theme_color")?;
        let qcolor = PyModule::import(py, "qtpy.QtGui")?.getattr("QColor")?;
        let rgb = |color: &Bound<'_, PyAny>| -> PyResult<[u8; 3]> {
            let c = qcolor.call1((color,))?;
            Ok([
                c.call_method0("red")?.extract()?,
                c.call_method0("green")?.extract()?,
                c.call_method0("blue")?.extract()?,
            ])
        };
        let neutral = rgb(palette.fg.as_str().into_pyobject(py)?.as_any())?;
        let mut ramps = RAMPS;
        for stops in &mut ramps {
            stops[0] = neutral;
            for stop in &mut stops[1..] {
                *stop = rgb(&resolve.call1((hex(*stop), &palette.bg, &palette.fg))?)?;
            }
        }
        Ok(Self { ramps, neutral })
    }

    pub fn check_color(&self, state: Option<bool>) -> String {
        hex(match state {
            Some(true) => self.ramps[3][3],
            Some(false) => self.ramps[1][2],
            None => self.neutral,
        })
    }

    pub fn color(&self, scale: Scale, value: f64) -> String {
        if !value.is_finite() || value == 0. {
            return hex(self.neutral);
        }
        let cold = value < 0.;
        let (ramp, full) = match scale {
            Scale::Temperature => (if cold { 0 } else { 1 }, if cold { 30. } else { 10. }),
            Scale::WindChill => (if cold { 0 } else { 1 }, if cold { 40. } else { 10. }),
            Scale::Energy => (if cold { 0 } else { 1 }, if cold { 250. } else { 15. }),
            Scale::ColdDepth => (0, 2000.),
            Scale::WarmDepth => (1, 2000.),
            Scale::Humidity => (3, 100.),
            Scale::Omega => (if cold { 0 } else { 1 }, 10.),
            Scale::Oprh => (if cold { 0 } else { 1 }, 0.5),
            Scale::Wind => (2, 60.),
            Scale::SnowRatio => (0, 30.),
            Scale::DgzWater => (3, 0.3),
            Scale::ColumnWater => (3, 2.),
            Scale::SnowSquall => (2, 4.),
            Scale::LapseRate => (if cold { 0 } else { 1 }, 10.),
        };
        let fraction = (value.abs() / full).min(1.) * 3.;
        let index = (fraction.floor() as usize).min(2);
        let blend = fraction - index as f64;
        let stops = self.ramps[ramp];
        hex(std::array::from_fn(|i| {
            (f64::from(stops[index][i]) * (1. - blend) + f64::from(stops[index + 1][i]) * blend)
                .round() as u8
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn winter_value_colors_keep_missing_and_zero_neutral() {
        let colors = ValueColors {
            ramps: RAMPS,
            neutral: [255; 3],
        };
        for scale in [
            Scale::Temperature,
            Scale::Energy,
            Scale::Wind,
            Scale::Humidity,
            Scale::Omega,
            Scale::SnowSquall,
        ] {
            for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0., -0.] {
                assert_eq!(colors.color(scale, value), "#ffffff");
            }
        }
    }

    #[test]
    fn winter_value_colors_recompute_strength_and_keep_thermal_signs_distinct() {
        let colors = ValueColors {
            ramps: RAMPS,
            neutral: [255; 3],
        };
        assert_ne!(
            colors.color(Scale::Energy, -10.),
            colors.color(Scale::Energy, 10.)
        );
        assert_ne!(
            colors.color(Scale::Wind, 10.),
            colors.color(Scale::Wind, 50.)
        );
        assert_eq!(
            colors.color(Scale::Wind, 60.),
            colors.color(Scale::Wind, 120.)
        );
        assert_eq!(
            colors.color(Scale::Omega, -10.),
            colors.color(Scale::Energy, -250.)
        );
        assert_ne!(
            colors.color(Scale::Omega, -10.),
            colors.color(Scale::Omega, 10.)
        );
    }
}
