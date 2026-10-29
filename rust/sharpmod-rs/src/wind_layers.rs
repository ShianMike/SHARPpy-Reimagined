//! SHARPpy display layers: one-hPa pressure-weighted means and endpoint shear.

use crate::interpolation::PreparedInterpolation;

pub(crate) type Vector = [Option<f64>; 2];

pub(crate) struct WindLayer {
    pub mean: Vector,
    pub shear: Vector,
}

pub(crate) struct WindProfile {
    height_to_logp: PreparedInterpolation,
    logp_to_u: PreparedInterpolation,
    logp_to_v: PreparedInterpolation,
    has_wind: bool,
}

impl WindProfile {
    pub fn new(columns: [&[f64]; 4], has_wind: bool) -> Result<Self, String> {
        let [height, logp, u, v] = columns;
        Ok(Self {
            height_to_logp: PreparedInterpolation::new(height, logp, None)?,
            logp_to_u: PreparedInterpolation::new(logp, u, None)?,
            logp_to_v: PreparedInterpolation::new(logp, v, None)?,
            has_wind,
        })
    }

    pub fn pressure(&self, height: Option<f64>) -> Option<f64> {
        let pressure = self.height_to_logp.scalar_sharppy(height?, true);
        pressure.is_finite().then_some(pressure)
    }

    pub fn components(&self, pressure: Option<f64>) -> Vector {
        if !self.has_wind {
            return [None; 2];
        }
        let Some(pressure) = pressure else {
            return [None; 2];
        };
        [&self.logp_to_u, &self.logp_to_v].map(|series| {
            let value = series.scalar_sharppy(pressure.log10(), false);
            value.is_finite().then_some(value)
        })
    }

    pub fn layer(&self, bottom: Option<f64>, top: Option<f64>) -> Result<WindLayer, String> {
        let mut result = WindLayer {
            mean: [None; 2],
            shear: [None; 2],
        };
        let (Some(bottom), Some(top)) = (bottom, top) else {
            return Ok(result);
        };
        if !self.has_wind {
            return Ok(result);
        }
        let lower = self.components(Some(bottom));
        let upper = self.components(Some(top));
        result.shear = [0, 1].map(|i| upper[i].zip(lower[i]).map(|(a, b)| a - b));
        // np.arange(bottom, top - 1, -1) can sample beyond a fractional top.
        let count = ((top - 1.0 - bottom) / -1.0).ceil().max(0.0);
        if !bottom.is_finite() || !top.is_finite() || count > 1_000_000.0 {
            return Err("wind layer has invalid or excessive pressure samples".into());
        }
        if count == 0.0 {
            result.mean = [Some(f64::NAN); 2];
            return Ok(result);
        }
        let step = (bottom - 1.0) - bottom;
        let mut sums = [0.0; 2];
        let mut weights = [0.0; 2];
        for index in 0..count as usize {
            let pressure = bottom + index as f64 * step;
            for (component, value) in self.components(Some(pressure)).into_iter().enumerate() {
                if let Some(value) = value {
                    sums[component] += value * pressure;
                    weights[component] += pressure;
                }
            }
        }
        result.mean = [0, 1].map(|i| (weights[i] != 0.0).then(|| sums[i] / weights[i]));
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_pressure_means_calm_and_missing_winds_keep_their_meanings() {
        let profile =
            WindProfile::new([&[0., 6000.], &[3., 2.], &[0., 0.], &[0., 0.]], true).unwrap();
        let layer = profile.layer(Some(1000.), Some(1000.)).unwrap();
        assert_eq!(layer.mean, [Some(0.), Some(0.)]);
        assert_eq!(layer.shear, [Some(0.), Some(0.)]);
        assert!(profile
            .layer(Some(500.), Some(501.))
            .unwrap()
            .mean
            .into_iter()
            .all(|value| value.unwrap().is_nan()));
        assert_eq!(profile.layer(None, Some(500.)).unwrap().mean, [None; 2]);
        let missing =
            WindProfile::new([&[0., 6000.], &[3., 2.], &[0., 0.], &[0., 0.]], false).unwrap();
        assert_eq!(
            missing.layer(Some(1000.), Some(500.)).unwrap().mean,
            [None; 2]
        );
        assert_eq!(missing.components(Some(1000.)), [None; 2]);
    }

    #[test]
    fn gaps_keep_independent_component_weights_and_bounds_remain_bounded() {
        let profile = WindProfile::new(
            [
                &[0., 3000., 6000.],
                &[3., 2.7, 2.4],
                &[0., f64::NAN, 60.],
                &[10., 20., 30.],
            ],
            true,
        )
        .unwrap();
        let at_middle = profile.components(Some(10.0f64.powf(2.7)));
        assert!((at_middle[0].unwrap() - 30.).abs() < 1e-12);
        assert_eq!(at_middle[1], Some(20.));
        let beyond = profile.layer(Some(1100.), Some(1050.)).unwrap();
        assert_eq!(beyond.mean, [None; 2]);
        assert_eq!(beyond.shear, [None; 2]);
        assert!(profile.layer(Some(f64::NAN), Some(500.)).is_err());
        assert!(profile.layer(Some(1e20), Some(1.)).is_err());
        assert!(WindProfile::new([&[0.], &[3., 2.], &[0.], &[0.]], true).is_err());
    }

    #[test]
    fn published_profile_layers_match_the_reference_sampling_and_weights() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("wind_layers_reference.json")).unwrap();
        let column = |name: &str| {
            fixture[name]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_f64().unwrap_or(f64::NAN))
                .collect::<Vec<_>>()
        };
        let columns = ["hght", "logp", "u", "v"].map(column);
        let profile = WindProfile::new([0, 1, 2, 3].map(|i| columns[i].as_slice()), true).unwrap();
        for row in fixture["layers"].as_array().unwrap() {
            let bounds = row["bounds"].as_array().unwrap();
            let actual = profile
                .layer(bounds[0].as_f64(), bounds[1].as_f64())
                .unwrap();
            for (name, values) in [("mean", actual.mean), ("shear", actual.shear)] {
                for (value, expected) in values.into_iter().zip(row[name].as_array().unwrap()) {
                    match (value, expected.as_f64()) {
                        (Some(value), Some(expected)) => assert!(
                            (value - expected).abs() < 1e-10,
                            "{name}: {value} vs {expected}"
                        ),
                        (None, None) => {}
                        pair => panic!("{name}: {pair:?}"),
                    }
                }
            }
        }
    }
}
