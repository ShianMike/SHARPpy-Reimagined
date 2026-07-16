//! Unit-preserving meteorological wind-vector conversion kernels.
//!
//! A wind direction denotes the direction *from* which the wind blows. Thus,
//! for a direction `d` in degrees and a speed `s`, the components are
//! `u = -s * sin(d)` and `v = -s * cos(d)`.

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

/// Convert meteorological wind direction and speed to `(u, v)` components.
///
/// The conversion is unit-preserving: the output components have the same unit
/// as `speed`. Non-finite values and values equal to `missing` propagate as a
/// pair of NaNs. Direction and speed must have identical lengths.
pub fn wind_to_components(
    direction: &[f64],
    speed: &[f64],
    missing: Option<f64>,
) -> Result<(Vec<f64>, Vec<f64>), String> {
    if direction.len() != speed.len() {
        return Err(format!(
            "direction and speed lengths differ: {} != {}",
            direction.len(),
            speed.len()
        ));
    }

    let mut u = Vec::with_capacity(direction.len());
    let mut v = Vec::with_capacity(direction.len());

    for (&direction_degrees, &magnitude) in direction.iter().zip(speed) {
        if is_missing(direction_degrees, missing) || is_missing(magnitude, missing) {
            u.push(f64::NAN);
            v.push(f64::NAN);
            continue;
        }

        let radians = direction_degrees.to_radians();
        u.push(-magnitude * radians.sin());
        v.push(-magnitude * radians.cos());
    }

    Ok((u, v))
}

/// Convert `(u, v)` components to meteorological wind direction and speed.
///
/// Direction is normalized to `[0, 360)` degrees and speed is returned in the
/// same unit as the input components. In particular, positive-zero calm wind
/// `(u=+0, v=+0)` has direction `270` degrees, matching the reference formula
/// `(270 - degrees(atan2(v, u))) % 360`. Non-finite values and values equal to
/// `missing` propagate as a pair of NaNs. Components must have equal lengths.
pub fn components_to_wind(
    u: &[f64],
    v: &[f64],
    missing: Option<f64>,
) -> Result<(Vec<f64>, Vec<f64>), String> {
    if u.len() != v.len() {
        return Err(format!(
            "u and v lengths differ: {} != {}",
            u.len(),
            v.len()
        ));
    }

    let mut direction = Vec::with_capacity(u.len());
    let mut speed = Vec::with_capacity(u.len());

    for (&u_component, &v_component) in u.iter().zip(v) {
        if is_missing(u_component, missing) || is_missing(v_component, missing) {
            direction.push(f64::NAN);
            speed.push(f64::NAN);
            continue;
        }

        direction.push((270.0 - v_component.atan2(u_component).to_degrees()).rem_euclid(360.0));
        speed.push(u_component.hypot(v_component));
    }

    Ok((direction, speed))
}

#[cfg(test)]
mod tests {
    use super::{components_to_wind, wind_to_components};

    const EPSILON: f64 = 1.0e-12;

    fn assert_close(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() <= EPSILON,
            "expected {expected}, got {actual}"
        );
    }

    #[test]
    fn direction_and_speed_convert_to_cardinal_components() {
        let direction = [0.0, 90.0, 180.0, 270.0];
        let speed = [10.0; 4];
        let (u, v) = wind_to_components(&direction, &speed, None).unwrap();

        for (actual, expected) in u.iter().zip([0.0, -10.0, 0.0, 10.0]) {
            assert_close(*actual, expected);
        }
        for (actual, expected) in v.iter().zip([-10.0, 0.0, 10.0, 0.0]) {
            assert_close(*actual, expected);
        }
    }

    #[test]
    fn extreme_directions_are_periodic() {
        let direction = [-450.0, -90.0, 270.0, 630.0, 990.0];
        let speed = [17.0; 5];
        let (u, v) = wind_to_components(&direction, &speed, None).unwrap();

        for actual in u {
            assert_close(actual, 17.0);
        }
        for actual in v {
            assert_close(actual, 0.0);
        }
    }

    #[test]
    fn components_convert_to_direction_and_speed() {
        let u = [0.0, -10.0, 0.0, 10.0, 0.0];
        let v = [-10.0, 0.0, 10.0, 0.0, 0.0];
        let (direction, speed) = components_to_wind(&u, &v, None).unwrap();

        for (actual, expected) in direction.iter().zip([0.0, 90.0, 180.0, 270.0, 270.0]) {
            assert_close(*actual, expected);
        }
        for (actual, expected) in speed.iter().zip([10.0, 10.0, 10.0, 10.0, 0.0]) {
            assert_close(*actual, expected);
        }
    }

    #[test]
    fn missing_and_nonfinite_inputs_propagate_as_nan_pairs() {
        let missing = -9999.0;
        let direction = [0.0, missing, f64::NAN, 180.0, 270.0];
        let speed = [10.0, 20.0, 30.0, f64::INFINITY, missing];
        let (u, v) = wind_to_components(&direction, &speed, Some(missing)).unwrap();

        assert_close(u[0], 0.0);
        assert_close(v[0], -10.0);
        for index in 1..direction.len() {
            assert!(u[index].is_nan());
            assert!(v[index].is_nan());
        }

        let u = [0.0, missing, f64::NEG_INFINITY, 4.0];
        let v = [-10.0, 5.0, 6.0, f64::NAN];
        let (direction, speed) = components_to_wind(&u, &v, Some(missing)).unwrap();
        assert_close(direction[0], 0.0);
        assert_close(speed[0], 10.0);
        for index in 1..u.len() {
            assert!(direction[index].is_nan());
            assert!(speed[index].is_nan());
        }
    }

    #[test]
    fn length_mismatches_are_rejected_and_empty_inputs_are_valid() {
        assert!(wind_to_components(&[0.0], &[], None).is_err());
        assert!(components_to_wind(&[0.0], &[], None).is_err());

        let (u, v) = wind_to_components(&[], &[], None).unwrap();
        assert!(u.is_empty() && v.is_empty());

        let (direction, speed) = components_to_wind(&[], &[], None).unwrap();
        assert!(direction.is_empty() && speed.is_empty());
    }

    #[test]
    fn conversion_round_trip_normalizes_direction() {
        let original_direction = [-725.0, -1.0, 37.5, 359.0, 721.0];
        let original_speed = [1.0, 5.0, 12.5, 40.0, 100.0];
        let (u, v) = wind_to_components(&original_direction, &original_speed, None).unwrap();
        let (direction, speed) = components_to_wind(&u, &v, None).unwrap();

        for ((actual_direction, source_direction), (actual_speed, source_speed)) in direction
            .iter()
            .zip(original_direction)
            .zip(speed.iter().zip(original_speed))
        {
            assert_close(*actual_direction, source_direction.rem_euclid(360.0));
            assert_close(*actual_speed, source_speed);
        }
    }
}
