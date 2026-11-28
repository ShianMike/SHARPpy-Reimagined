//! Native model-domain geometry and forecast-hour selection rules
//! (milestone M43).
//!
//! Rust ports of the pure bookkeeping half of two Python modules:
//!
//! * ``sharpmod.tools.model_extract`` -- ``_longitude_segments``
//!   ([`model_longitude_segments`]);
//! * ``sharpmod.tools.model_extract_config`` -- the rectangular branch of
//!   ``domain_intersects_bounds`` ([`model_domain_rect_intersects`]), the
//!   rectangular branch of ``domain_contains_bounds``
//!   ([`model_domain_rect_contains`]), the ``(lat, lon)`` sample grid of the
//!   ``domain_outline`` branch of ``domain_intersects_bounds``
//!   ([`model_outline_sample_points`]), the outline-vertex fallback of that
//!   same branch ([`model_outline_vertex_in_bounds`]), and the HRRR /
//!   ECMWF IFS cycle-dependent cut-offs of ``forecast_hours``
//!   ([`model_filter_forecast_hours`]).
//!
//! Longitude clamping and antimeridian segmentation reproduce the Python
//! helpers exactly, including Python's two-argument ``min``/``max``
//! behaviour for NaN.  Bounds tuples keep the Python unpacking order
//! ``(lon0, lon1, lat0, lat1)`` in degrees, and outline vertices stay the
//! ``(lon, lat)`` pairs stored on ``ModelConfig.domain_outline``.  A model
//! that supplies a domain outline is tested against it instead of a box;
//! these rules supply the pure geometry, and the caller decides which
//! representation a model carries.

use pyo3::prelude::*;

/// HRRR publishes its full forecast length only on the major synoptic
/// cycles (Python checks ``int(cycle_hour) not in (0, 6, 12, 18)``).
const HRRR_SYNOPTIC_CYCLES: [i64; 4] = [0, 6, 12, 18];

/// Forecast-hour cut-off of an HRRR off-hour cycle.
const HRRR_OFF_HOUR_MAX_FXX: i64 = 18;

/// ECMWF IFS runs a short cut-off forecast at 06Z and 18Z
/// (Python ``IFS_SHORT_CUTOFF_CYCLES``).
const IFS_SHORT_CUTOFF_CYCLES: [i64; 2] = [6, 18];

/// Forecast-hour cut-off of the short IFS cut-off cycles
/// (Python ``IFS_SHORT_CUTOFF_MAX_FXX``).
const IFS_SHORT_CUTOFF_MAX_FXX: i64 = 144;

// ---------------------------------------------------------------------------
// Longitude segmentation
// (sharpmod.tools.model_extract._longitude_segments)
// ---------------------------------------------------------------------------

/// Python's ``max(-180.0, min(180.0, value))``.
///
/// Python's two-argument ``min``/``max`` keep the first operand whenever the
/// comparison against the second is not true, so a NaN longitude clamps to
/// ``180.0`` exactly like the source helper.
fn clamp_longitude(value: f64) -> f64 {
    // min(180.0, value): value wins only when strictly below 180.0.
    let upper = if value < 180.0 { value } else { 180.0 };
    // max(-180.0, upper): upper wins only when strictly above -180.0.
    if upper > -180.0 {
        upper
    } else {
        -180.0
    }
}

/// The pure half of ``_longitude_segments``: clamp both ends to
/// ``[-180.0, 180.0]`` and split an antimeridian-crossing range into the
/// ``(left, right)`` segments it covers.
pub(crate) fn longitude_segments_core(lon0: f64, lon1: f64) -> Vec<(f64, f64)> {
    let lon0 = clamp_longitude(lon0);
    let lon1 = clamp_longitude(lon1);
    if lon0 <= lon1 {
        vec![(lon0, lon1)]
    } else {
        vec![(lon0, 180.0), (-180.0, lon1)]
    }
}

/// Split one longitude range into contiguous ``(left, right)`` segments.
///
/// Both ends are clamped to ``[-180.0, 180.0]`` exactly like the Python
/// helper's ``max(-180.0, min(180.0, x))`` -- a NaN end clamps to ``180.0``
/// because Python's two-argument ``min``/``max`` keep the first operand on a
/// failed comparison.  A range that does not cross the antimeridian is one
/// segment; one that does (``lon0 > lon1``, as for the RRFS Alaska domain
/// ``160 -> -120``) is split at 180 degrees into
/// ``[(lon0, 180.0), (-180.0, lon1)]``.
#[pyfunction]
fn model_longitude_segments(lon0: f64, lon1: f64) -> Vec<(f64, f64)> {
    longitude_segments_core(lon0, lon1)
}

// ---------------------------------------------------------------------------
// Rectangular domain tests
// (sharpmod.tools.model_extract_config.domain_intersects_bounds /
//  domain_contains_bounds)
// ---------------------------------------------------------------------------

/// The pure half of the rectangular branch of ``domain_intersects_bounds``:
/// the model's latitude band must overlap the map extent, and at least one
/// model longitude segment must overlap at least one query segment, with
/// touching edges counting as an overlap.
pub(crate) fn domain_rect_intersects_core(
    model_bounds: (f64, f64, f64, f64),
    query_bounds: (f64, f64, f64, f64),
) -> bool {
    let (lon0, lon1, lat0, lat1) = model_bounds;
    let (blo0, blo1, bla0, bla1) = query_bounds;
    if lat1 < bla0 || lat0 > bla1 {
        return false;
    }
    let model_segments = longitude_segments_core(lon0, lon1);
    let query_segments = longitude_segments_core(blo0, blo1);
    model_segments.iter().copied().any(|(left, right)| {
        query_segments
            .iter()
            .copied()
            .any(|(other_left, other_right)| !(right < other_left || left > other_right))
    })
}

/// Whether a model's rectangular domain intersects a map extent.
///
/// ``model_bounds`` and ``query_bounds`` are ``(lon0, lon1, lat0, lat1)``
/// degree tuples -- the order ``domain_intersects_bounds`` unpacks them in.
/// Latitude bands must overlap, and at least one model longitude segment
/// must overlap at least one segment of the extent, with touching edges
/// counting (an extent ending exactly at the domain edge intersects).
/// Ranges that cross the antimeridian are segmented first, so an Alaska
/// domain like ``(160.0, -120.0, 40.0, 80.0)`` matches extents on either
/// side of 180 degrees.
#[pyfunction]
fn model_domain_rect_intersects(
    model_bounds: (f64, f64, f64, f64),
    query_bounds: (f64, f64, f64, f64),
) -> bool {
    domain_rect_intersects_core(model_bounds, query_bounds)
}

/// The pure half of the rectangular branch of ``domain_contains_bounds``:
/// the model's latitude band must cover the extent's, and every query
/// longitude segment must fit inside one model segment.
pub(crate) fn domain_rect_contains_core(
    model_bounds: (f64, f64, f64, f64),
    query_bounds: (f64, f64, f64, f64),
) -> bool {
    let (lon0, lon1, lat0, lat1) = model_bounds;
    let (blo0, blo1, bla0, bla1) = query_bounds;
    if lat0 > bla0 || lat1 < bla1 {
        return false;
    }
    let model_segments = longitude_segments_core(lon0, lon1);
    longitude_segments_core(blo0, blo1)
        .iter()
        .copied()
        .all(|(other_left, other_right)| {
            model_segments
                .iter()
                .copied()
                .any(|(left, right)| left <= other_left && right >= other_right)
        })
}

/// Whether a model's rectangular domain fully contains a map extent.
///
/// ``model_bounds`` and ``query_bounds`` are ``(lon0, lon1, lat0, lat1)``
/// degree tuples, as in [`model_domain_rect_intersects`].  The model's
/// latitude band must cover the extent's, and every longitude segment of
/// the extent must fit within one model segment, so an extent crossing the
/// antimeridian is contained only when the model covers both of its
/// segments.
#[pyfunction]
fn model_domain_rect_contains(
    model_bounds: (f64, f64, f64, f64),
    query_bounds: (f64, f64, f64, f64),
) -> bool {
    domain_rect_contains_core(model_bounds, query_bounds)
}

// ---------------------------------------------------------------------------
// Domain-outline sampling
// (sharpmod.tools.model_extract_config.domain_intersects_bounds)
// ---------------------------------------------------------------------------

/// The pure half of the outline branch's sample grid: for every longitude
/// segment of the map extent, the three sample latitudes and three sample
/// longitudes (each edge plus the midpoint) form nine ``(lat, lon)``
/// points, in the same nested order the Python generator produces.
pub(crate) fn outline_sample_points_core(bounds: (f64, f64, f64, f64)) -> Vec<(f64, f64)> {
    let (blo0, blo1, bla0, bla1) = bounds;
    let lat_mid = (bla0 + bla1) / 2.0;
    let mut samples = Vec::new();
    for (left, right) in longitude_segments_core(blo0, blo1) {
        let lon_mid = (left + right) / 2.0;
        for lat in [bla0, lat_mid, bla1] {
            for lon in [left, lon_mid, right] {
                samples.push((lat, lon));
            }
        }
    }
    samples
}

/// Sample the ``(lat, lon)`` grid ``domain_intersects_bounds`` tests against
/// a model outline.
///
/// ``bounds`` is the map extent as a ``(lon0, lon1, lat0, lat1)`` degree
/// tuple.  For every longitude segment of the extent -- one, or two when
/// the extent crosses the antimeridian -- the latitudes
/// ``(lat0, midpoint, lat1)`` are paired with the longitudes
/// ``(left, midpoint, right)`` of the segment, yielding nine ordered
/// ``(lat, lon)`` points per segment.  The caller tests each point with the
/// model's own ``point_in_domain`` rule; the geometry here matches the
/// Python generator exactly.
#[pyfunction]
fn model_outline_sample_points(bounds: (f64, f64, f64, f64)) -> Vec<(f64, f64)> {
    outline_sample_points_core(bounds)
}

/// The pure half of the outline branch's vertex fallback: any outline
/// vertex whose ``(lat, lon)`` falls inside the map extent means the
/// outline intersects it.
pub(crate) fn outline_vertex_in_bounds_core(
    outline: &[(f64, f64)],
    bounds: (f64, f64, f64, f64),
) -> bool {
    let (blo0, blo1, bla0, bla1) = bounds;
    let segments = longitude_segments_core(blo0, blo1);
    outline.iter().copied().any(|(lon, lat)| {
        (bla0 <= lat && lat <= bla1)
            && segments
                .iter()
                .copied()
                .any(|(left, right)| left <= lon && lon <= right)
    })
}

/// Whether any domain-outline vertex lies inside a map extent.
///
/// ``outline`` holds the model's perimeter as ``(lon, lat)`` pairs -- the
/// storage order of ``ModelConfig.domain_outline`` -- and ``bounds`` is the
/// map extent as a ``(lon0, lon1, lat0, lat1)`` degree tuple.  A vertex
/// matches when its latitude lies inside the extent's band and its
/// longitude lies inside any extent longitude segment, so an extent
/// crossing the antimeridian still matches vertices on both sides.  An
/// empty outline never matches, exactly like ``any()`` over an empty
/// generator.
#[pyfunction]
fn model_outline_vertex_in_bounds(outline: Vec<(f64, f64)>, bounds: (f64, f64, f64, f64)) -> bool {
    outline_vertex_in_bounds_core(&outline, bounds)
}

// ---------------------------------------------------------------------------
// Cycle-dependent forecast-hour cut-offs
// (sharpmod.tools.model_extract_config.forecast_hours)
// ---------------------------------------------------------------------------

/// The pure half of ``forecast_hours``: apply the model's cycle-dependent
/// forecast-hour cut-off.
///
/// ``model_key`` is the canonical config key (``ModelConfig.key``), not an
/// alias, and ``values`` are the model's configured hours.  HRRR keeps only
/// the hours up to 18 on a cycle that is not one of the major synoptic
/// cycles, and the ECMWF IFS open-data products keep only the hours up to
/// 144 on the short cut-off cycles (06Z and 18Z); every other model and
/// cycle keeps ``values`` unchanged, with their original order and
/// duplicates.
pub(crate) fn filter_forecast_hours_core(
    model_key: &str,
    values: &[i64],
    cycle_hour: Option<i64>,
) -> Vec<i64> {
    let Some(cycle) = cycle_hour else {
        return values.to_vec();
    };
    let max_fxx = match model_key {
        "hrrr" if !HRRR_SYNOPTIC_CYCLES.contains(&cycle) => Some(HRRR_OFF_HOUR_MAX_FXX),
        "ecmwf-ifs" | "ecmwf-ifs-9km" if IFS_SHORT_CUTOFF_CYCLES.contains(&cycle) => {
            Some(IFS_SHORT_CUTOFF_MAX_FXX)
        }
        _ => None,
    };
    match max_fxx {
        None => values.to_vec(),
        Some(limit) => values
            .iter()
            .copied()
            .filter(|hour| *hour <= limit)
            .collect(),
    }
}

/// Apply one model's cycle-dependent forecast-hour cut-off.
///
/// ``model_key`` is the canonical config key, ``values`` the model's
/// configured forecast hours, and ``cycle_hour`` the run's cycle (``None``
/// when the caller does not know it).  HRRR publishes a longer forecast on
/// the major synoptic cycles than on its off-hour cycles, so a cycle
/// outside ``(0, 6, 12, 18)`` keeps only the hours up to 18; the ECMWF IFS
/// products run a short cut-off forecast at 06Z and 18Z, so those cycles
/// keep only the hours up to 144.  Every other model, cycle, or missing
/// cycle returns ``values`` unchanged.  RRFS needs no such rule (its cycle
/// list already excludes off-hour cycles), and the RAP and Open-Meteo
/// cadences are resolved by their own adapters before this rule applies.
#[pyfunction]
fn model_filter_forecast_hours(
    model_key: &str,
    values: Vec<i64>,
    cycle_hour: Option<i64>,
) -> Vec<i64> {
    filter_forecast_hours_core(model_key, &values, cycle_hour)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(model_longitude_segments, module)?)?;
    module.add_function(wrap_pyfunction!(model_domain_rect_intersects, module)?)?;
    module.add_function(wrap_pyfunction!(model_domain_rect_contains, module)?)?;
    module.add_function(wrap_pyfunction!(model_outline_sample_points, module)?)?;
    module.add_function(wrap_pyfunction!(model_outline_vertex_in_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(model_filter_forecast_hours, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn longitude_segments_keep_simple_ranges_single() {
        assert_eq!(
            longitude_segments_core(-130.0, -60.0),
            vec![(-130.0, -60.0)]
        );
        assert_eq!(
            longitude_segments_core(-180.0, 180.0),
            vec![(-180.0, 180.0)]
        );
        assert_eq!(longitude_segments_core(5.0, 5.0), vec![(5.0, 5.0)]);
        assert_eq!(longitude_segments_core(0.0, 359.0), vec![(0.0, 180.0)]);
    }

    #[test]
    fn longitude_segments_split_antimeridian_crossings() {
        // The RRFS Alaska domain: 160E east across 180 to 120W.
        assert_eq!(
            longitude_segments_core(160.0, -120.0),
            vec![(160.0, 180.0), (-180.0, -120.0)]
        );
        // Clamping happens before the split, exactly like Python.
        assert_eq!(
            longitude_segments_core(200.0, -220.0),
            vec![(180.0, 180.0), (-180.0, -180.0)]
        );
        // Both ends beyond one clamp bound collapse to a degenerate pair.
        assert_eq!(
            longitude_segments_core(-500.0, -400.0),
            vec![(-180.0, -180.0)]
        );
        assert_eq!(longitude_segments_core(200.0, 300.0), vec![(180.0, 180.0)]);
    }

    #[test]
    fn longitude_segments_follow_python_min_max_for_nan() {
        // Python's max(-180.0, min(180.0, nan)) is 180.0: the two-argument
        // min/max keep the first operand when the comparison is not true.
        assert_eq!(
            longitude_segments_core(f64::NAN, 10.0),
            vec![(180.0, 180.0), (-180.0, 10.0)]
        );
        assert_eq!(longitude_segments_core(10.0, f64::NAN), vec![(10.0, 180.0)]);
        assert_eq!(
            longitude_segments_core(f64::NAN, f64::NAN),
            vec![(180.0, 180.0)]
        );
    }

    #[test]
    fn rect_intersects_matches_domain_intersects_bounds() {
        // The Python tests for the RRFS Alaska domain.
        let alaska = (160.0, -120.0, 40.0, 80.0);
        assert!(domain_rect_intersects_core(
            alaska,
            (170.0, 180.0, 50.0, 60.0)
        ));
        assert!(domain_rect_intersects_core(
            alaska,
            (-180.0, -150.0, 50.0, 60.0)
        ));
        assert!(!domain_rect_intersects_core(
            alaska,
            (-100.0, -80.0, 50.0, 60.0)
        ));
        // Latitude bands that miss reject before any longitude work.
        assert!(!domain_rect_intersects_core(
            alaska,
            (160.0, 180.0, 85.0, 90.0)
        ));
        assert!(!domain_rect_intersects_core(
            alaska,
            (160.0, 180.0, 10.0, 30.0)
        ));
    }

    #[test]
    fn rect_intersects_counts_touching_edges_and_wrapped_extents() {
        let conus = (-130.0, -60.0, 20.0, 55.0);
        assert!(domain_rect_intersects_core(
            conus,
            (-125.0, -65.0, 25.0, 50.0)
        ));
        // An extent starting exactly at the eastern edge still intersects.
        assert!(domain_rect_intersects_core(
            conus,
            (-60.0, -40.0, 25.0, 50.0)
        ));
        assert!(!domain_rect_intersects_core(
            conus,
            (-59.0, -40.0, 25.0, 50.0)
        ));
        // A global model intersects every extent, including wrapped ones.
        let global = (-180.0, 180.0, -90.0, 90.0);
        assert!(domain_rect_intersects_core(
            global,
            (170.0, -170.0, 0.0, 10.0)
        ));
        assert!(domain_rect_intersects_core(global, (10.0, 20.0, -5.0, 5.0)));
        // A CONUS model does not intersect a wrapped extent in Asia.
        assert!(!domain_rect_intersects_core(
            conus,
            (170.0, -170.0, 25.0, 50.0)
        ));
    }

    #[test]
    fn rect_contains_matches_domain_contains_bounds() {
        let conus = (-130.0, -60.0, 20.0, 55.0);
        assert!(domain_rect_contains_core(
            conus,
            (-125.0, -65.0, 25.0, 50.0)
        ));
        // The extent must fit inside in every direction.
        assert!(!domain_rect_contains_core(
            conus,
            (-140.0, -120.0, 25.0, 50.0)
        ));
        assert!(!domain_rect_contains_core(
            conus,
            (-125.0, -65.0, 10.0, 50.0)
        ));
        assert!(!domain_rect_contains_core(
            conus,
            (-125.0, -65.0, 25.0, 60.0)
        ));
        // A global model contains wrapped extents too.
        let global = (-180.0, 180.0, -90.0, 90.0);
        assert!(domain_rect_contains_core(
            global,
            (170.0, -170.0, -80.0, 80.0)
        ));
        assert!(domain_rect_contains_core(global, (10.0, 20.0, -5.0, 5.0)));
    }

    #[test]
    fn rect_contains_pairs_wrapped_segments_with_model_segments() {
        let alaska = (160.0, -120.0, 40.0, 80.0);
        assert!(domain_rect_contains_core(
            alaska,
            (170.0, 180.0, 50.0, 60.0)
        ));
        assert!(domain_rect_contains_core(
            alaska,
            (-180.0, -150.0, 50.0, 60.0)
        ));
        // Both wrapped pieces fit, so the wrapped extent is contained.
        assert!(domain_rect_contains_core(
            alaska,
            (170.0, -150.0, 50.0, 60.0)
        ));
        // The eastern piece reaches past the model's western edge.
        assert!(!domain_rect_contains_core(
            alaska,
            (170.0, -100.0, 50.0, 60.0)
        ));
        // A single unwrapped extent east of the dateline is contained.
        assert!(domain_rect_contains_core(
            alaska,
            (165.0, 175.0, 50.0, 60.0)
        ));
        // An end beyond 180 clamps back to it, so the segment still fits.
        assert!(domain_rect_contains_core(
            alaska,
            (165.0, 185.0, 50.0, 60.0)
        ));
        // ... but an extent starting west of the model edge does not.
        assert!(!domain_rect_contains_core(
            alaska,
            (150.0, 170.0, 50.0, 60.0)
        ));
    }

    #[test]
    fn outline_samples_cover_the_query_grid_in_order() {
        let points = outline_sample_points_core((-10.0, 10.0, 30.0, 40.0));
        assert_eq!(points.len(), 9);
        assert_eq!(points[0], (30.0, -10.0));
        assert_eq!(points[1], (30.0, 0.0));
        assert_eq!(points[2], (30.0, 10.0));
        assert_eq!(points[4], (35.0, 0.0));
        assert_eq!(points[6], (40.0, -10.0));
        assert_eq!(points[8], (40.0, 10.0));
    }

    #[test]
    fn outline_samples_split_wrapped_extents_per_segment() {
        let points = outline_sample_points_core((170.0, -170.0, 50.0, 60.0));
        assert_eq!(points.len(), 18);
        // First segment (170, 180) with midpoint 175.
        assert_eq!(points[0], (50.0, 170.0));
        assert_eq!(points[4], (55.0, 175.0));
        assert_eq!(points[8], (60.0, 180.0));
        // Second segment (-180, -170) with midpoint -175.
        assert_eq!(points[9], (50.0, -180.0));
        assert_eq!(points[13], (55.0, -175.0));
        assert_eq!(points[17], (60.0, -170.0));
    }

    #[test]
    fn outline_vertices_test_against_query_bounds() {
        let outline = vec![(-97.0, 35.0), (-88.0, 42.0)];
        assert!(outline_vertex_in_bounds_core(
            &outline,
            (-100.0, -90.0, 30.0, 40.0)
        ));
        // The second vertex is the one inside this extent.
        assert!(outline_vertex_in_bounds_core(
            &outline,
            (-90.0, -80.0, 40.0, 45.0)
        ));
        // No vertex longitude falls inside the extent.
        assert!(!outline_vertex_in_bounds_core(
            &outline,
            (-96.0, -90.0, 30.0, 40.0)
        ));
        // Latitude band rejection.
        assert!(!outline_vertex_in_bounds_core(
            &outline,
            (-100.0, -90.0, 45.0, 50.0)
        ));
        // An empty outline never intersects.
        assert!(!outline_vertex_in_bounds_core(
            &[],
            (-180.0, 180.0, -90.0, 90.0)
        ));
    }

    #[test]
    fn outline_vertices_match_wrapped_extents() {
        // A dateline-crossing extent matches vertices on both sides.
        assert!(outline_vertex_in_bounds_core(
            &[(175.0, 55.0)],
            (170.0, -170.0, 50.0, 60.0)
        ));
        assert!(outline_vertex_in_bounds_core(
            &[(-175.0, 55.0)],
            (170.0, -170.0, 50.0, 60.0)
        ));
        // A vertex outside both segments does not match.
        assert!(!outline_vertex_in_bounds_core(
            &[(10.0, 55.0)],
            (170.0, -170.0, 50.0, 60.0)
        ));
        // Latitude still rejects inside a wrapped extent.
        assert!(!outline_vertex_in_bounds_core(
            &[(175.0, 20.0)],
            (170.0, -170.0, 50.0, 60.0)
        ));
    }

    #[test]
    fn forecast_hours_cut_hrrr_off_hour_cycles_to_18() {
        let hours: Vec<i64> = (0..=48).collect();
        let filtered = filter_forecast_hours_core("hrrr", &hours, Some(5));
        assert_eq!(filtered.len(), 19);
        assert_eq!(*filtered.last().unwrap(), 18);
        assert!(filtered.iter().all(|hour| *hour <= 18));
        // The major synoptic cycles keep the full run.
        for cycle in HRRR_SYNOPTIC_CYCLES {
            assert_eq!(
                filter_forecast_hours_core("hrrr", &hours, Some(cycle)),
                hours
            );
        }
        // Without cycle information the configured cadence stands.
        assert_eq!(filter_forecast_hours_core("hrrr", &hours, None), hours);
    }

    #[test]
    fn forecast_hours_cut_ifs_short_cutoff_cycles_to_144() {
        let mut hours: Vec<i64> = (0..=144).step_by(3).collect();
        hours.extend((150..=360).step_by(6));
        for key in ["ecmwf-ifs", "ecmwf-ifs-9km"] {
            for cycle in IFS_SHORT_CUTOFF_CYCLES {
                let filtered = filter_forecast_hours_core(key, &hours, Some(cycle));
                assert_eq!(filtered.len(), 49);
                assert_eq!(*filtered.last().unwrap(), 144);
                assert!(filtered.iter().all(|hour| *hour <= 144));
            }
            // The long cut-off cycles keep the full run.
            assert_eq!(filter_forecast_hours_core(key, &hours, Some(0)), hours);
            assert_eq!(filter_forecast_hours_core(key, &hours, Some(12)), hours);
            assert_eq!(filter_forecast_hours_core(key, &hours, None), hours);
        }
    }

    #[test]
    fn forecast_hours_leave_other_models_and_keys_unchanged() {
        let hours = vec![0, 6, 12, 18, 24, 30, 36];
        for key in ["gfs", "rap", "nam", "ecmwf-aifs", "rrfs-a"] {
            assert_eq!(filter_forecast_hours_core(key, &hours, Some(5)), hours);
            assert_eq!(filter_forecast_hours_core(key, &hours, Some(6)), hours);
        }
        // A cycle outside the configured list is still a cycle: HRRR's rule
        // keys on the model, not on the cycle list membership of the config.
        assert_eq!(
            filter_forecast_hours_core("hrrr", &hours, Some(19)),
            vec![0, 6, 12, 18]
        );
    }

    #[test]
    fn forecast_hours_preserve_order_and_duplicates() {
        let hours = vec![48, 0, 12, 0];
        assert_eq!(
            filter_forecast_hours_core("hrrr", &hours, Some(3)),
            vec![0, 12, 0]
        );
        let ifs_hours = vec![240, 48, 0, 144, 0];
        assert_eq!(
            filter_forecast_hours_core("ecmwf-ifs", &ifs_hours, Some(18)),
            vec![48, 0, 144, 0]
        );
        assert_eq!(filter_forecast_hours_core("gfs", &hours, Some(3)), hours);
    }
}
