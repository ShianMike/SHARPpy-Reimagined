//! Common NOAA grid-relative wind conventions for GRIB decoders and providers.

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct Reference {
    pub cone: f64,
    pub origin: f64,
}

pub(crate) struct Precision {
    pub source_f32: (bool, bool),
    pub longitude_f32: bool,
    pub components_f32: bool,
}

impl Reference {
    fn new(cone: f64, origin: f64) -> Result<Self, &'static str> {
        if !cone.is_finite() || !origin.is_finite() || cone.abs() < 1e-12 {
            return Err("invalid GRIB wind projection metadata");
        }
        Ok(Self { cone, origin })
    }

    pub fn lambert(first: f64, second: f64, origin: f64) -> Result<Self, &'static str> {
        let (first, second) = (first.to_radians(), second.to_radians());
        if first.abs() >= std::f64::consts::FRAC_PI_2 || second.abs() >= std::f64::consts::FRAC_PI_2
        {
            return Err("invalid Lambert standard parallels");
        }
        let cone = if (first - second).abs() < 1e-12 {
            first.sin()
        } else {
            (first.cos() / second.cos()).ln()
                / ((std::f64::consts::FRAC_PI_4 + second / 2.0).tan()
                    / (std::f64::consts::FRAC_PI_4 + first / 2.0).tan())
                .ln()
        };
        Self::new(cone, origin)
    }

    pub fn polar(southern: bool, origin: f64) -> Result<Self, &'static str> {
        Self::new(if southern { -1.0 } else { 1.0 }, origin)
    }

    pub fn rotate(self, u: f64, v: f64, longitude: f64) -> (f64, f64) {
        let angle =
            (self.cone * crate::lambert::normalize_longitude(longitude - self.origin)).to_radians();
        let (sine, cosine) = angle.sin_cos();
        (cosine * u + sine * v, -sine * u + cosine * v)
    }

    pub fn masked_rotate(
        self,
        u: f64,
        v: f64,
        longitude: f64,
        missing: f64,
        precision: &Precision,
    ) -> (f64, f64) {
        let source_missing = |single| {
            if single {
                f64::from(missing as f32)
            } else {
                missing
            }
        };
        if !u.is_finite()
            || !v.is_finite()
            || u == source_missing(precision.source_f32.0)
            || v == source_missing(precision.source_f32.1)
        {
            return (missing, missing);
        }
        let (sine, cosine) = if precision.longitude_f32 {
            let delta = ((longitude as f32 - self.origin as f32 + 180.0).rem_euclid(360.0)) - 180.0;
            let (s, c) = ((self.cone as f32 * delta).to_radians()).sin_cos();
            (f64::from(s), f64::from(c))
        } else {
            let angle = (self.cone * crate::lambert::normalize_longitude(longitude - self.origin))
                .to_radians();
            angle.sin_cos()
        };
        if precision.components_f32 {
            let (u, v, s, c) = (u as f32, v as f32, sine as f32, cosine as f32);
            (f64::from(c * u + s * v), f64::from(-s * u + c * v))
        } else {
            (cosine * u + sine * v, -sine * u + cosine * v)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn true_east_north_rotation_preserves_speed_meridian_and_wrap() {
        let reference = Reference::lambert(38.5, 38.5, -97.5).unwrap();
        assert_eq!(reference.rotate(3.0, 4.0, -97.5), (3.0, 4.0));
        let (u, v) = reference.rotate(3.0, 4.0, -120.0);
        assert!((u.hypot(v) - 5.0).abs() < 1e-12);
        assert_eq!(reference.rotate(3.0, 4.0, 240.0), (u, v));
        assert!(u < 3.0 && v > 4.0);
        assert!(Reference::lambert(0.0, 0.0, -97.5).is_err());
        assert!(Reference::lambert(90.0, 90.0, -97.5).is_err());
        assert!(Reference::polar(false, f64::NAN).is_err());
    }
    #[test]
    fn polar_hemispheres_rotate_in_opposite_directions() {
        let north = Reference::polar(false, 0.0)
            .unwrap()
            .rotate(10.0, 0.0, 90.0);
        let south = Reference::polar(true, 0.0).unwrap().rotate(10.0, 0.0, 90.0);
        assert!(north.0.abs() < 1e-12 && (north.1 + 10.0).abs() < 1e-12);
        assert!(south.0.abs() < 1e-12 && (south.1 - 10.0).abs() < 1e-12);
    }
    #[test]
    fn invalid_components_never_export_half_a_rotated_vector() {
        let reference = Reference::lambert(38.5, 38.5, -97.5).unwrap();
        for missing in [-9999.0, -1234.0] {
            for invalid in [missing, f64::NAN, f64::INFINITY] {
                let precision = Precision {
                    source_f32: (false, false),
                    longitude_f32: false,
                    components_f32: false,
                };
                assert_eq!(
                    reference.masked_rotate(3.0, invalid, -120.0, missing, &precision),
                    (missing, missing)
                );
                assert_eq!(
                    reference.masked_rotate(invalid, 4.0, -120.0, missing, &precision),
                    (missing, missing)
                );
            }
        }
    }

    #[test]
    fn configured_missing_values_use_the_precision_of_each_source_component() {
        let reference = Reference::lambert(38.5, 38.5, -97.5).unwrap();
        let missing = -9999.1234;
        let encoded = f64::from(missing as f32);
        let precision = Precision {
            source_f32: (false, true),
            longitude_f32: false,
            components_f32: false,
        };
        assert_eq!(
            reference.masked_rotate(3.0, encoded, -120.0, missing, &precision),
            (missing, missing)
        );
    }
}
