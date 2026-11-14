//! Shared map diagnostics, preserving the existing fixed-layer definitions.

use numpy::ndarray::NdFloat;

#[derive(Clone, Copy)]
pub(crate) enum Diagnostic {
    Ehi,
    Scp,
    Stp,
    Lapse,
    PrecipitationType,
    ThetaE,
}

impl Diagnostic {
    pub fn parse(name: &str) -> Result<Self, &'static str> {
        match name {
            "ehi" => Ok(Self::Ehi),
            "scp" => Ok(Self::Scp),
            "stp" => Ok(Self::Stp),
            "lapse-700-500" => Ok(Self::Lapse),
            "precipitation-type" => Ok(Self::PrecipitationType),
            "thetae" => Ok(Self::ThetaE),
            _ => Err("unknown map diagnostic"),
        }
    }
    pub fn arity(self) -> usize {
        match self {
            Self::Ehi | Self::PrecipitationType => 2,
            Self::Scp | Self::Lapse => 4,
            Self::Stp => 6,
            Self::ThetaE => 3,
        }
    }
}

fn clip<T: NdFloat>(value: T, low: T, high: T) -> T {
    if value < low {
        low
    } else if value > high {
        high
    } else {
        value
    }
}

pub(crate) fn precipitation_type<T: NdFloat>(rate: T, flag: f64) -> T {
    let rate = rate * T::from(3600.0).unwrap();
    if !rate.is_finite() || rate < T::zero() || (flag != 0.0 && flag != 1.0) {
        T::nan()
    } else if flag == 1.0 {
        rate
    } else {
        T::zero()
    }
}

pub(crate) fn evaluate<T: NdFloat>(kind: Diagnostic, v: &[T]) -> T {
    let n = |value| T::from(value).expect("finite diagnostic constant");
    match kind {
        Diagnostic::Ehi => v[0] * v[1] / n(160000.0),
        Diagnostic::Scp => {
            let cape = if v[0].is_nan() {
                v[0]
            } else {
                v[0].max(n(0.0))
            };
            let shear = v[2].hypot(v[3]);
            let shear_term = if shear < n(10.0) {
                n(0.0)
            } else {
                clip(shear / n(20.0), n(0.0), n(1.0))
            };
            (cape / n(1000.0)) * (v[1] / n(50.0)) * shear_term
        }
        Diagnostic::Stp => {
            let cape = if v[0].is_nan() {
                v[0]
            } else {
                v[0].max(n(0.0))
            };
            let lcl = clip((n(2000.0) - v[1]) / n(1000.0), n(0.0), n(1.0));
            let shear = v[3].hypot(v[4]);
            let shear_term = if shear < n(12.5) {
                n(0.0)
            } else {
                clip(shear / n(20.0), n(0.0), n(1.5))
            };
            let cin = clip((n(200.0) + v[5]) / n(150.0), n(0.0), n(1.0));
            (cape / n(1500.0)) * lcl * (v[2] / n(150.0)) * shear_term * cin
        }
        Diagnostic::Lapse => {
            let depth = (v[3] - v[2]) / n(1000.0);
            if depth > n(0.1) {
                (v[0] - v[1]) / depth
            } else {
                T::nan()
            }
        }
        Diagnostic::PrecipitationType => precipitation_type(v[0], v[1].to_f64().unwrap()),
        Diagnostic::ThetaE => {
            let values = [
                v[0].to_f64().unwrap(),
                v[1].to_f64().unwrap(),
                v[2].to_f64().unwrap(),
            ];
            if values.iter().any(|v| !v.is_finite() || *v <= 0.0) {
                return T::nan();
            }
            let result =
                crate::ecape::thermodynamics::theta_e(values[0] / 100.0, values[1], values[2]);
            let result = n(result);
            if result.is_finite() {
                result
            } else {
                T::nan()
            }
        }
    }
}

#[cfg(test)]
mod tests;
