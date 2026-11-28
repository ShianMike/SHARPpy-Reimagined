//! Native forecast-timeline ledger and animation-planning helpers (milestone M20).
//!
//! Rust ports of the pure bookkeeping half of ``sharpmod.analysis``:
//!
//! * ``profile_timeline.forecast_hour_range`` -- [`timeline_forecast_hour_range`]
//! * ``timeline_frames.normalize_state`` / ``TimelineFrame.describe`` /
//!   ``TimelineCoverage.summary`` -- [`timeline_normalize_state`],
//!   [`timeline_frame_describe`], [`timeline_coverage_summary`]
//! * ``animation_exports._frame_caption`` / ``prepare_animation_frames`` --
//!   [`animation_frame_caption`], [`animation_plan_frames`]
//! * ``ensemble_members.EnsembleAcquisition`` / ``MemberFailure`` validation --
//!   [`ensemble_validate_ledger`]
//!
//! Error messages match the Python sources verbatim so existing tests keep
//! passing against either implementation.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use time::OffsetDateTime;

/// Every canonical frame state, in lifecycle order.
pub(crate) const FRAME_STATES: [&str; 7] = [
    "requested",
    "loading",
    "loaded",
    "failed",
    "canceled",
    "unavailable",
    "unknown",
];

/// Human wording for each state (``timeline_frames.STATE_LABELS``).
fn state_label(state: &str) -> &'static str {
    match state {
        "requested" => "requested, not started",
        "loading" => "loading",
        "loaded" => "loaded",
        "failed" => "failed",
        "canceled" => "canceled",
        "unavailable" => "not published by the source",
        _ => "outcome unknown",
    }
}

/// Canonicalise a frame state, translating known synonyms (Python
/// ``normalize_state``; anything unrecognised becomes ``unknown``).
pub(crate) fn normalize_state_text(value: &str) -> String {
    let lowered = value.trim().to_lowercase();
    let mapped = match lowered.as_str() {
        "cancelled" => "canceled",
        "complete" | "completed" | "success" | "succeeded" => "loaded",
        "error" => "failed",
        "working" => "loading",
        "pending" | "queued" => "requested",
        "missing" => "unavailable",
        _ => lowered.as_str(),
    };
    if FRAME_STATES.contains(&mapped) {
        mapped.to_string()
    } else {
        "unknown".to_string()
    }
}

/// Inclusive, validated forecast-hour selection (Python ``forecast_hour_range``).
pub(crate) fn compute_forecast_hour_range(
    available: &[i64],
    start: i64,
    end: i64,
    step: i64,
) -> Result<Vec<i64>, String> {
    let mut values = available.to_vec();
    values.sort_unstable();
    values.dedup();
    if values.is_empty() {
        return Err("no forecast hours are available".to_string());
    }
    if step < 1 {
        return Err("timeline step must be at least one hour".to_string());
    }
    if start > end {
        return Err("timeline start must not be after its end".to_string());
    }
    let selected: Vec<i64> = values
        .iter()
        .copied()
        .filter(|&hour| (start..=end).contains(&hour) && (hour - start) % step == 0)
        .collect();
    if selected.is_empty() {
        return Err("the selected range contains no available forecast hour".to_string());
    }
    if selected[0] != start || selected[selected.len() - 1] != end {
        return Err("timeline start and end must be available forecast hours".to_string());
    }
    Ok(selected)
}

/// One line naming the hour, its state, and any reason (Python ``describe``).
pub(crate) fn frame_describe(forecast_hour: i64, state: &str, reason: Option<&str>) -> String {
    let state = normalize_state_text(state);
    let mut text = format!("F{:03}: {}", forecast_hour, state_label(&state));
    if let Some(reason_text) = reason {
        if !reason_text.is_empty() {
            text.push_str(&format!(" \u{2014} {reason_text}"));
        }
    }
    text
}

/// One-sentence coverage statement (Python ``TimelineCoverage.summary``).
pub(crate) fn coverage_summary(states: &[String]) -> String {
    if states.is_empty() {
        return "No forecast hours were requested.".to_string();
    }
    let normalized: Vec<String> = states
        .iter()
        .map(|state| normalize_state_text(state))
        .collect();
    let mut parts = vec![format!(
        "{} of {} requested hour(s) loaded",
        normalized
            .iter()
            .filter(|state| state.as_str() == "loaded")
            .count(),
        normalized.len()
    )];
    for state in [
        "loading",
        "requested",
        "failed",
        "canceled",
        "unavailable",
        "unknown",
    ] {
        let count = normalized
            .iter()
            .filter(|item| item.as_str() == state)
            .count();
        if count > 0 {
            parts.push(format!("{count} {}", state_label(state)));
        }
    }
    let mut summary = parts.join("; ");
    summary.push('.');
    summary
}

/// Caption bar text for one animation frame (Python ``_frame_caption``).
pub(crate) fn frame_caption(
    mode: &str,
    label: &str,
    valid_text: &str,
    run_text: &str,
    forecast_hour: Option<i64>,
) -> String {
    if mode == "forecast-timeline" {
        let lead = match forecast_hour {
            Some(hour) if label.trim().to_uppercase() != format!("F{hour:03}") => {
                format!(" \u{00b7} F{hour:03}")
            }
            _ => String::new(),
        };
        return format!("{label} \u{00b7} valid {valid_text}{lead}");
    }
    format!("{label} \u{00b7} run {run_text} \u{00b7} valid {valid_text}")
}

/// Format a Unix-epoch timestamp as `%Y-%m-%d %H:%MZ` in UTC.
///
/// Component getters avoid the time crate's `formatting` feature (not enabled
/// for this package) and any timezone ambiguity.
fn format_utc_minute(epoch: f64) -> String {
    if !epoch.is_finite() {
        return "Unknown".to_string();
    }
    let seconds = epoch.floor() as i64;
    match OffsetDateTime::from_unix_timestamp(seconds) {
        Ok(moment) => format!(
            "{:04}-{:02}-{:02} {:02}:{:02}Z",
            moment.year(),
            u8::from(moment.month()),
            moment.day(),
            moment.hour(),
            moment.minute(),
        ),
        Err(_) => "Unknown".to_string(),
    }
}

type AnimationPlanRow = (
    bool,
    Option<usize>,
    Option<String>,
    Option<f64>,
    Option<i64>,
    Option<String>,
);
type EnsembleLedger = (
    Vec<String>,
    Vec<String>,
    Vec<(String, String, Option<String>)>,
);

/// One planned output frame: a gap descriptor or a reference to a source frame.
#[derive(Debug)]
pub(crate) struct PlanEntry {
    is_gap: bool,
    source_index: Option<usize>,
    label: String,
    has_png: bool,
    missing_epoch: Option<f64>,
    lead_hour: Option<i64>,
    reason: Option<String>,
}

impl PlanEntry {
    fn into_tuple(self) -> AnimationPlanRow {
        let gap_label = self.is_gap.then(|| self.label.clone());
        (
            self.is_gap,
            self.source_index,
            gap_label,
            self.missing_epoch,
            self.lead_hour,
            self.reason,
        )
    }
}

/// Apply the explicit missing/discontinuity policy (Python
/// ``prepare_animation_frames``) and return the ordered plan entries.
///
/// `continuity_epochs` carry the per-frame continuity timestamps
/// (valid time for ``forecast-timeline`` mode, run time for ``run-to-run``).
#[allow(clippy::too_many_arguments)]
pub(crate) fn compute_plan_frames(
    mode: &str,
    gap_policy: &str,
    expected_interval_seconds: Option<f64>,
    labels: &[String],
    has_png: &[bool],
    continuity_epochs: &[Option<f64>],
    forecast_hours: &[Option<i64>],
    missing_reasons: &[Option<String>],
) -> Result<Vec<PlanEntry>, String> {
    if !matches!(mode, "forecast-timeline" | "run-to-run") {
        return Err(format!("unsupported animation mode '{mode}'"));
    }
    let policy_normalized = gap_policy.trim().to_lowercase();
    let policy = if policy_normalized.is_empty() {
        "cards"
    } else {
        policy_normalized.as_str()
    };
    if !matches!(policy, "cards" | "stop" | "error") {
        return Err(format!(
            "unsupported gap policy '{gap_policy}'; expected cards, stop, or error"
        ));
    }
    let count = labels.len();
    if has_png.len() != count
        || continuity_epochs.len() != count
        || forecast_hours.len() != count
        || missing_reasons.len() != count
    {
        return Err("animation frame inputs must have matching lengths".to_string());
    }
    if count == 0 {
        return Err("animation needs at least one selected frame".to_string());
    }
    let expected = match expected_interval_seconds {
        Some(interval) if !interval.is_finite() || interval <= 0.0 => {
            return Err("expected frame interval must be positive".to_string());
        }
        interval => interval,
    };

    let timeline_mode = mode == "forecast-timeline";
    let mut entries: Vec<PlanEntry> = Vec::with_capacity(2 * count);
    for index in 0..count {
        if let (Some(interval), Some(previous)) = (expected, index.checked_sub(1)) {
            if let (Some(before), Some(after)) =
                (continuity_epochs[previous], continuity_epochs[index])
            {
                if after - before > interval * 1.5 {
                    let gap_label = if timeline_mode { "Time gap" } else { "Run gap" };
                    let lead_hour = if timeline_mode {
                        forecast_hours[previous]
                            .map(|hour| (hour as f64 + interval / 3600.0).round_ties_even() as i64)
                    } else {
                        None
                    };
                    let gap_reason = format!(
                        "time discontinuity from {} to {}; the displayed step exceeds the smallest source interval; cadence may change or intermediate times may be unselected",
                        format_utc_minute(before),
                        format_utc_minute(after),
                    );
                    entries.push(PlanEntry {
                        is_gap: true,
                        source_index: None,
                        label: gap_label.to_string(),
                        has_png: false,
                        missing_epoch: Some(before + interval),
                        lead_hour,
                        reason: Some(gap_reason),
                    });
                }
            }
        }
        entries.push(PlanEntry {
            is_gap: false,
            source_index: Some(index),
            label: labels[index].clone(),
            has_png: has_png[index],
            missing_epoch: None,
            lead_hour: None,
            reason: missing_reasons[index].clone(),
        });
    }

    match policy {
        "error" => {
            if let Some(first) = entries.iter().find(|entry| !entry.has_png) {
                return Err(format!(
                    "gap policy is Fail export and frame '{}' is missing: {}",
                    first.label,
                    first.reason.as_deref().unwrap_or("None"),
                ));
            }
        }
        "stop" => match entries.iter().position(|entry| !entry.has_png) {
            Some(0) => {
                return Err(format!(
                    "gap policy is Stop before first gap, but the first frame is missing: {}",
                    entries[0].reason.as_deref().unwrap_or("None"),
                ));
            }
            Some(cut) => entries.truncate(cut),
            None => {}
        },
        _ => {}
    }
    Ok(entries)
}

/// Strip an ensemble member label; empty labels are rejected (Python ``_member``).
fn normalize_member_label(label: &str) -> Result<String, String> {
    let text = label.trim();
    if text.is_empty() {
        return Err("ensemble member labels must be non-empty".to_string());
    }
    Ok(text.to_string())
}

/// Normalise a member failure status (Python ``MemberFailure``).
fn normalize_member_status(status: &str) -> Result<String, String> {
    let text = status.trim().to_lowercase();
    if matches!(text.as_str(), "failed" | "cancelled" | "unknown") {
        Ok(text)
    } else {
        Err(format!("unsupported ensemble member status '{text}'"))
    }
}

fn has_duplicates<T: PartialEq>(items: &[T]) -> bool {
    items
        .iter()
        .enumerate()
        .any(|(index, item)| items.iter().skip(index + 1).any(|other| other == item))
}

/// Validate an ensemble acquisition ledger (Python ``EnsembleAcquisition``
/// plus ``MemberFailure`` post-init rules), returning the normalised records.
pub(crate) fn compute_ensemble_ledger(
    requested: Vec<String>,
    loaded: Vec<String>,
    failures: Vec<(String, String, Option<String>)>,
) -> Result<EnsembleLedger, String> {
    // MemberFailure validation ran at construction time in Python, so member
    // and status errors surface before the ledger-level checks.
    let mut normalised_failures: Vec<(String, String, Option<String>)> =
        Vec::with_capacity(failures.len());
    for (member, status, reason) in failures {
        normalised_failures.push((
            normalize_member_label(&member)?,
            normalize_member_status(&status)?,
            reason,
        ));
    }

    let mut requested_members = Vec::with_capacity(requested.len());
    for member in requested {
        requested_members.push(normalize_member_label(&member)?);
    }
    let mut loaded_members = Vec::with_capacity(loaded.len());
    for member in loaded {
        loaded_members.push(normalize_member_label(&member)?);
    }

    if has_duplicates(&requested_members) {
        return Err("requested ensemble members must be unique".to_string());
    }
    if has_duplicates(&loaded_members) {
        return Err("loaded ensemble members must be unique".to_string());
    }
    if loaded_members
        .iter()
        .any(|member| !requested_members.contains(member))
    {
        return Err("loaded members must belong to the original request".to_string());
    }
    let failed_names: Vec<&str> = normalised_failures
        .iter()
        .map(|failure| failure.0.as_str())
        .collect();
    if has_duplicates(&failed_names) {
        return Err("each unavailable member needs one final status".to_string());
    }
    if failed_names
        .iter()
        .any(|member| !requested_members.iter().any(|item| item == member))
    {
        return Err("failed members must belong to the original request".to_string());
    }
    if loaded_members
        .iter()
        .any(|member| failed_names.contains(&member.as_str()))
    {
        return Err("a member cannot be both loaded and unavailable".to_string());
    }
    Ok((requested_members, loaded_members, normalised_failures))
}

/// Inclusive, validated subset of selectable forecast hours.
#[pyfunction]
fn timeline_forecast_hour_range(
    available: Vec<i64>,
    start: i64,
    end: i64,
    step: i64,
) -> PyResult<Vec<i64>> {
    compute_forecast_hour_range(&available, start, end, step).map_err(PyValueError::new_err)
}

/// Canonical frame state for any status word (``None`` or unknown -> ``unknown``).
#[pyfunction]
fn timeline_normalize_state(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let text = if value.is_truthy()? {
        value.str()?.to_str()?.to_owned()
    } else {
        String::new()
    };
    Ok(normalize_state_text(&text))
}

/// One line naming the hour, its state, and any reason given.
#[pyfunction]
fn timeline_frame_describe(forecast_hour: i64, state: &str, reason: Option<&str>) -> String {
    frame_describe(forecast_hour, state, reason)
}

/// State the coverage in one sentence, counting every category present.
#[pyfunction]
fn timeline_coverage_summary(states: Vec<String>) -> String {
    coverage_summary(&states)
}

/// Caption bar text for one animation frame.
#[pyfunction]
fn animation_frame_caption(
    mode: &str,
    label: &str,
    valid_text: &str,
    run_text: &str,
    forecast_hour: Option<i64>,
) -> String {
    frame_caption(mode, label, valid_text, run_text, forecast_hour)
}

/// Apply an explicit missing/discontinuity policy before rendering.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn animation_plan_frames(
    mode: &str,
    gap_policy: &str,
    expected_interval_seconds: Option<f64>,
    labels: Vec<String>,
    has_png: Vec<bool>,
    continuity_epochs: Vec<Option<f64>>,
    forecast_hours: Vec<Option<i64>>,
    missing_reasons: Vec<Option<String>>,
) -> PyResult<Vec<AnimationPlanRow>> {
    compute_plan_frames(
        mode,
        gap_policy,
        expected_interval_seconds,
        &labels,
        &has_png,
        &continuity_epochs,
        &forecast_hours,
        &missing_reasons,
    )
    .map(|entries| entries.into_iter().map(PlanEntry::into_tuple).collect())
    .map_err(PyValueError::new_err)
}

/// Validate and normalise an ensemble acquisition ledger.
#[pyfunction]
fn ensemble_validate_ledger(
    requested: Vec<String>,
    loaded: Vec<String>,
    failures: Vec<(String, String, Option<String>)>,
) -> PyResult<EnsembleLedger> {
    compute_ensemble_ledger(requested, loaded, failures).map_err(PyValueError::new_err)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(timeline_forecast_hour_range, module)?)?;
    module.add_function(wrap_pyfunction!(timeline_normalize_state, module)?)?;
    module.add_function(wrap_pyfunction!(timeline_frame_describe, module)?)?;
    module.add_function(wrap_pyfunction!(timeline_coverage_summary, module)?)?;
    module.add_function(wrap_pyfunction!(animation_frame_caption, module)?)?;
    module.add_function(wrap_pyfunction!(animation_plan_frames, module)?)?;
    module.add_function(wrap_pyfunction!(ensemble_validate_ledger, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forecast_hour_range_selects_inclusive_steps() {
        assert_eq!(
            compute_forecast_hour_range(&[12, 6, 0, 6], 0, 12, 6).unwrap(),
            vec![0, 6, 12]
        );
        assert_eq!(
            compute_forecast_hour_range(&[0, 12], 0, 12, 4).unwrap(),
            vec![0, 12]
        );
        // Out-of-range hours never leak into the selection.
        assert_eq!(
            compute_forecast_hour_range(&[-3, 0, 6, 12, 15], 0, 12, 3).unwrap(),
            vec![0, 6, 12]
        );
    }

    #[test]
    fn forecast_hour_range_errors_match_python_messages() {
        assert_eq!(
            compute_forecast_hour_range(&[], 0, 12, 1).unwrap_err(),
            "no forecast hours are available"
        );
        assert_eq!(
            compute_forecast_hour_range(&[0], 0, 12, 0).unwrap_err(),
            "timeline step must be at least one hour"
        );
        assert_eq!(
            compute_forecast_hour_range(&[0], 12, 0, 1).unwrap_err(),
            "timeline start must not be after its end"
        );
        assert_eq!(
            compute_forecast_hour_range(&[5], 0, 10, 3).unwrap_err(),
            "the selected range contains no available forecast hour"
        );
        assert_eq!(
            compute_forecast_hour_range(&[0, 12], 2, 12, 5).unwrap_err(),
            "timeline start and end must be available forecast hours"
        );
        // An empty request keeps its own message even with other bad inputs.
        assert_eq!(
            compute_forecast_hour_range(&[], 12, 0, 0).unwrap_err(),
            "no forecast hours are available"
        );
    }

    #[test]
    fn normalize_state_translates_aliases_and_rejects_unknowns() {
        assert_eq!(normalize_state_text(""), "unknown");
        assert_eq!(normalize_state_text("  "), "unknown");
        assert_eq!(normalize_state_text("Loaded "), "loaded");
        assert_eq!(normalize_state_text("Cancelled"), "canceled");
        assert_eq!(normalize_state_text("COMPLETE"), "loaded");
        assert_eq!(normalize_state_text("succeeded"), "loaded");
        assert_eq!(normalize_state_text("Success"), "loaded");
        assert_eq!(normalize_state_text("ERROR"), "failed");
        assert_eq!(normalize_state_text("Working"), "loading");
        assert_eq!(normalize_state_text("pending"), "requested");
        assert_eq!(normalize_state_text("Queued"), "requested");
        assert_eq!(normalize_state_text("missing"), "unavailable");
        assert_eq!(normalize_state_text("unavailable"), "unavailable");
        assert_eq!(normalize_state_text("bogus"), "unknown");
    }

    #[test]
    fn frame_describe_matches_timeline_frame() {
        assert_eq!(frame_describe(18, "loaded", None), "F018: loaded");
        assert_eq!(
            frame_describe(18, "FAILED", Some("reader died")),
            "F018: failed \u{2014} reader died"
        );
        // Empty reasons are dropped, matching Python truthiness.
        assert_eq!(frame_describe(18, "loading", Some("")), "F018: loading");
        assert_eq!(
            frame_describe(18, "loading", Some("still queued")),
            "F018: loading \u{2014} still queued"
        );
        // Negative hours format with the sign inside the zero pad.
        assert_eq!(frame_describe(-5, "unknown", None), "F-05: outcome unknown");
        assert_eq!(frame_describe(3, "canceled", None), "F003: canceled");
    }

    #[test]
    fn coverage_summary_counts_every_category_present() {
        assert_eq!(coverage_summary(&[]), "No forecast hours were requested.");
        assert_eq!(
            coverage_summary(&[
                "loaded".to_string(),
                "Loaded ".to_string(),
                "loading".to_string(),
                "unknown".to_string(),
                "canceled".to_string(),
            ]),
            "2 of 5 requested hour(s) loaded; 1 loading; 1 canceled; 1 outcome unknown."
        );
        assert_eq!(
            coverage_summary(&["requested".to_string(), "failed".to_string()]),
            "0 of 2 requested hour(s) loaded; 1 requested, not started; 1 failed."
        );
    }

    #[test]
    fn animation_caption_matches_frame_caption() {
        // Matching label: no lead hour suffix.
        assert_eq!(
            frame_caption("forecast-timeline", "F018", "12Z Sun", "12Z Sat", Some(18)),
            "F018 \u{00b7} valid 12Z Sun"
        );
        assert_eq!(
            frame_caption("forecast-timeline", "F018", "12Z Sun", "12Z Sat", None),
            "F018 \u{00b7} valid 12Z Sun"
        );
        // Distinct label: the lead hour is appended.
        assert_eq!(
            frame_caption(
                "forecast-timeline",
                "18z sounding",
                "12Z Sun",
                "12Z Sat",
                Some(18)
            ),
            "18z sounding \u{00b7} valid 12Z Sun \u{00b7} F018"
        );
        // Run-to-run captions always quote run and valid text.
        assert_eq!(
            frame_caption("run-to-run", "F018", "12Z Sun", "12Z Sat", Some(18)),
            "F018 \u{00b7} run 12Z Sat \u{00b7} valid 12Z Sun"
        );
    }

    #[test]
    fn plan_frames_inserts_time_gap_card() {
        let entries = compute_plan_frames(
            "forecast-timeline",
            "cards",
            Some(3600.0),
            &["F001".to_string(), "F002".to_string()],
            &[true, true],
            &[Some(0.0), Some(7200.0)],
            &[Some(1), Some(2)],
            &[None, None],
        )
        .unwrap();
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].source_index, Some(0));
        let gap = &entries[1];
        assert!(gap.is_gap);
        assert_eq!(gap.label, "Time gap");
        assert_eq!(gap.missing_epoch, Some(3600.0));
        assert_eq!(gap.lead_hour, Some(2));
        assert_eq!(
            gap.reason.as_deref(),
            Some("time discontinuity from 1970-01-01 00:00Z to 1970-01-01 02:00Z; the displayed step exceeds the smallest source interval; cadence may change or intermediate times may be unselected")
        );
        assert_eq!(entries[2].source_index, Some(1));
    }

    #[test]
    fn plan_frames_gap_lead_hour_uses_bankers_rounding() {
        // 2710 - 0 > 1.5 * 1800 inserts a gap; 2 + 1800/3600 = 2.5 rounds
        // to 2 (ties to even), matching Python round().
        let entries = compute_plan_frames(
            "forecast-timeline",
            "cards",
            Some(1800.0),
            &["F002".to_string(), "F003".to_string()],
            &[true, true],
            &[Some(0.0), Some(2710.0)],
            &[Some(2), Some(3)],
            &[None, None],
        )
        .unwrap();
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[1].lead_hour, Some(2));
    }

    #[test]
    fn plan_frames_run_to_run_uses_run_gap_without_lead() {
        let entries = compute_plan_frames(
            "run-to-run",
            "cards",
            Some(3600.0),
            &["r12".to_string(), "r18".to_string()],
            &[true, true],
            &[Some(0.0), Some(7200.0)],
            &[Some(1), Some(2)],
            &[None, None],
        )
        .unwrap();
        assert_eq!(entries.len(), 3);
        let gap = &entries[1];
        assert!(gap.is_gap);
        assert_eq!(gap.label, "Run gap");
        assert_eq!(gap.lead_hour, None);
        assert_eq!(gap.missing_epoch, Some(3600.0));
    }

    #[test]
    fn plan_frames_skips_gap_below_threshold_and_missing_epochs() {
        // 2700 - 0 is not > 1.5 * 1800: no gap card.
        let tight = compute_plan_frames(
            "forecast-timeline",
            "cards",
            Some(1800.0),
            &["F001".to_string(), "F002".to_string()],
            &[true, true],
            &[Some(0.0), Some(2700.0)],
            &[Some(1), Some(2)],
            &[None, None],
        )
        .unwrap();
        assert_eq!(tight.len(), 2);
        // Without an expected interval there is never a gap card.
        let no_expected = compute_plan_frames(
            "forecast-timeline",
            "cards",
            None,
            &["F001".to_string(), "F002".to_string()],
            &[true, true],
            &[Some(0.0), Some(86_400.0)],
            &[Some(1), Some(2)],
            &[None, None],
        )
        .unwrap();
        assert_eq!(no_expected.len(), 2);
        // A missing continuity epoch on either side blocks the detection.
        let missing_epoch = compute_plan_frames(
            "forecast-timeline",
            "cards",
            Some(3600.0),
            &["F001".to_string(), "F002".to_string()],
            &[true, true],
            &[None, Some(86_400.0)],
            &[Some(1), Some(2)],
            &[None, None],
        )
        .unwrap();
        assert_eq!(missing_epoch.len(), 2);
    }

    #[test]
    fn plan_frames_policy_errors_match_python() {
        assert_eq!(
            compute_plan_frames(
                "nope",
                "cards",
                None,
                &["F001".to_string()],
                &[true],
                &[Some(0.0)],
                &[Some(1)],
                &[None],
            )
            .unwrap_err(),
            "unsupported animation mode 'nope'"
        );
        // The rejected policy is quoted verbatim, not normalized.
        assert_eq!(
            compute_plan_frames(
                "run-to-run",
                " Nope ",
                None,
                &["F001".to_string()],
                &[true],
                &[Some(0.0)],
                &[Some(1)],
                &[None],
            )
            .unwrap_err(),
            "unsupported gap policy ' Nope '; expected cards, stop, or error"
        );
        assert_eq!(
            compute_plan_frames("run-to-run", "cards", None, &[], &[], &[], &[], &[],).unwrap_err(),
            "animation needs at least one selected frame"
        );
        assert_eq!(
            compute_plan_frames(
                "run-to-run",
                "cards",
                Some(0.0),
                &["F001".to_string()],
                &[true],
                &[Some(0.0)],
                &[Some(1)],
                &[None],
            )
            .unwrap_err(),
            "expected frame interval must be positive"
        );
        assert_eq!(
            compute_plan_frames(
                "run-to-run",
                "cards",
                Some(f64::NAN),
                &["F001".to_string()],
                &[true],
                &[Some(0.0)],
                &[Some(1)],
                &[None],
            )
            .unwrap_err(),
            "expected frame interval must be positive"
        );
        // Blank policy strings default to cards; any casing works.
        assert!(!compute_plan_frames(
            "run-to-run",
            "",
            None,
            &["F001".to_string()],
            &[false],
            &[None],
            &[Some(1)],
            &[Some("skip".to_string())],
        )
        .unwrap()
        .is_empty());
        assert!(!compute_plan_frames(
            "run-to-run",
            " Cards ",
            None,
            &["F001".to_string()],
            &[false],
            &[None],
            &[Some(1)],
            &[Some("skip".to_string())],
        )
        .unwrap()
        .is_empty());
    }

    #[test]
    fn plan_frames_rejects_mismatched_lengths() {
        assert_eq!(
            compute_plan_frames(
                "run-to-run",
                "cards",
                None,
                &["F001".to_string(), "F002".to_string()],
                &[true],
                &[Some(0.0), Some(3600.0)],
                &[Some(1), Some(2)],
                &[None, None],
            )
            .unwrap_err(),
            "animation frame inputs must have matching lengths"
        );
    }

    #[test]
    fn plan_frames_error_policy_names_first_missing_frame() {
        // A real missing frame carries the source label and its reason.
        assert_eq!(
            compute_plan_frames(
                "forecast-timeline",
                "error",
                None,
                &["F001".to_string(), "F002".to_string()],
                &[true, false],
                &[Some(0.0), Some(3600.0)],
                &[Some(1), Some(2)],
                &[None, Some("download failed".to_string())],
            )
            .unwrap_err(),
            "gap policy is Fail export and frame 'F002' is missing: download failed"
        );
        // Gap cards are themselves missing frames for the error policy.
        assert_eq!(
            compute_plan_frames(
                "forecast-timeline",
                "error",
                Some(3600.0),
                &["F001".to_string(), "F002".to_string()],
                &[true, true],
                &[Some(0.0), Some(7200.0)],
                &[Some(1), Some(2)],
                &[None, None],
            )
            .unwrap_err(),
            "gap policy is Fail export and frame 'Time gap' is missing: time discontinuity from 1970-01-01 00:00Z to 1970-01-01 02:00Z; the displayed step exceeds the smallest source interval; cadence may change or intermediate times may be unselected"
        );
    }

    #[test]
    fn plan_frames_stop_policy_truncates_before_first_gap() {
        let truncated = compute_plan_frames(
            "forecast-timeline",
            "stop",
            None,
            &["F001".to_string(), "F002".to_string(), "F003".to_string()],
            &[true, false, true],
            &[Some(0.0), Some(3600.0), Some(7200.0)],
            &[Some(1), Some(2), Some(3)],
            &[None, Some("timeout".to_string()), None],
        )
        .unwrap();
        assert_eq!(truncated.len(), 1);
        assert_eq!(truncated[0].source_index, Some(0));
        // A missing first frame makes the stop policy fail outright.
        assert_eq!(
            compute_plan_frames(
                "forecast-timeline",
                "stop",
                None,
                &["F001".to_string(), "F002".to_string()],
                &[false, true],
                &[Some(0.0), Some(3600.0)],
                &[Some(1), Some(2)],
                &[Some("nope".to_string()), None],
            )
            .unwrap_err(),
            "gap policy is Stop before first gap, but the first frame is missing: nope"
        );
        // Cards keeps every frame, missing ones included.
        let cards = compute_plan_frames(
            "forecast-timeline",
            "cards",
            None,
            &["F001".to_string(), "F002".to_string()],
            &[true, false],
            &[Some(0.0), Some(3600.0)],
            &[Some(1), Some(2)],
            &[None, Some("timeout".to_string())],
        )
        .unwrap();
        assert_eq!(cards.len(), 2);
        assert!(!cards[1].has_png);
    }

    #[test]
    fn ensemble_ledger_normalises_and_returns_valid_records() {
        // Member labels are stripped but NOT lower-cased, matching Python _member.
        let (requested, loaded, failures) = compute_ensemble_ledger(
            vec![" c00 ".to_string(), "p01".to_string()],
            vec!["  c00 ".to_string()],
            vec![(
                " p01 ".to_string(),
                " FAILED ".to_string(),
                Some("boom".to_string()),
            )],
        )
        .unwrap();
        assert_eq!(requested, vec!["c00".to_string(), "p01".to_string()]);
        assert_eq!(loaded, vec!["c00".to_string()]);
        assert_eq!(
            failures,
            vec![(
                "p01".to_string(),
                "failed".to_string(),
                Some("boom".to_string())
            )]
        );
    }

    #[test]
    fn ensemble_ledger_validation_errors_match_python() {
        assert_eq!(
            compute_ensemble_ledger(vec!["   ".to_string()], vec![], vec![]).unwrap_err(),
            "ensemble member labels must be non-empty"
        );
        assert_eq!(
            compute_ensemble_ledger(
                vec!["c00".to_string()],
                vec![],
                vec![("p01".to_string(), " ok ".to_string(), None)],
            )
            .unwrap_err(),
            "unsupported ensemble member status 'ok'"
        );
        assert_eq!(
            compute_ensemble_ledger(vec!["c00".to_string(), "c00".to_string()], vec![], vec![],)
                .unwrap_err(),
            "requested ensemble members must be unique"
        );
        assert_eq!(
            compute_ensemble_ledger(
                vec!["c00".to_string()],
                vec!["c00".to_string(), "c00".to_string()],
                vec![],
            )
            .unwrap_err(),
            "loaded ensemble members must be unique"
        );
        assert_eq!(
            compute_ensemble_ledger(vec!["c00".to_string()], vec!["p01".to_string()], vec![],)
                .unwrap_err(),
            "loaded members must belong to the original request"
        );
        assert_eq!(
            compute_ensemble_ledger(
                vec!["p01".to_string()],
                vec![],
                vec![
                    ("p01".to_string(), "failed".to_string(), None),
                    (" p01 ".to_string(), "cancelled".to_string(), None),
                ],
            )
            .unwrap_err(),
            "each unavailable member needs one final status"
        );
        assert_eq!(
            compute_ensemble_ledger(
                vec!["c00".to_string()],
                vec![],
                vec![("p01".to_string(), "unknown".to_string(), None)],
            )
            .unwrap_err(),
            "failed members must belong to the original request"
        );
        assert_eq!(
            compute_ensemble_ledger(
                vec!["p01".to_string()],
                vec!["p01".to_string()],
                vec![("p01".to_string(), "failed".to_string(), None)],
            )
            .unwrap_err(),
            "a member cannot be both loaded and unavailable"
        );
    }

    #[test]
    fn ensemble_empty_ledger_is_valid() {
        let (requested, loaded, failures) =
            compute_ensemble_ledger(vec![], vec![], vec![]).unwrap();
        assert!(requested.is_empty());
        assert!(loaded.is_empty());
        assert!(failures.is_empty());
    }
}
