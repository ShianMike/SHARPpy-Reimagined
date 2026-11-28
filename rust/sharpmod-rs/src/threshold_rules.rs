//! Native ensemble-ingredient threshold validation and member-state rules
//! (milestone M41).
//!
//! Rust ports of the pure validation and bookkeeping half of
//! ``sharpmod.analysis.ensemble_thresholds``:
//!
//! * ``ThresholdCondition.__post_init__``
//!   ([`threshold_validate_condition`]);
//! * ``ThresholdDefinition.__post_init__``
//!   ([`threshold_validate_definition`]);
//! * ``ThresholdMemberResult.__post_init__``'s state derivation and its
//!   unsupported-state guard ([`threshold_derive_member_state`]);
//! * the per-member outcome aggregation of ``evaluate_threshold``
//!   ([`threshold_evaluate_member_conditions`]);
//! * ``ThresholdResult``'s derived counts, ``reconciles`` funnel, and
//!   ``state_counts`` ([`threshold_result_counts_reconcile`]);
//! * the resume-consistency checks of ``evaluate_threshold``
//!   ([`threshold_validate_resume_members`]).
//!
//! Error messages match the Python source verbatim.  Validation failures
//! raise the Python ``ThresholdError`` class, falling back to ``ValueError``
//! when the class cannot be imported (``ThresholdError`` subclasses
//! ``ValueError``).  Missing values keep the module's ``-9998`` sentinel
//! bound exactly like its ``_finite`` helper, and the comparison semantics
//! of ``threshold_evaluate_condition`` -- the floating-point tolerance of
//! ``==`` and the inclusive ``between`` band -- are reproduced so both
//! implementations agree member for member.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// The comparisons a threshold condition accepts, in the order every
/// control lists them (Python ``OPERATOR_ORDER``); validation only needs
/// membership.
const OPERATOR_ORDER: [&str; 6] = [">", ">=", "<", "<=", "==", "between"];

/// A member with every condition satisfied.
const MEMBER_QUALIFYING: &str = "qualifying";
/// A usable member that failed at least one condition.
const MEMBER_NONQUALIFYING: &str = "nonqualifying";
/// A loaded member missing a required diagnostic value.
const MEMBER_UNUSABLE: &str = "unusable";
/// A loaded member with no profile at the exact valid time.
const MEMBER_NO_PROFILE: &str = "no_profile";
/// A loaded member whose diagnostic calculation raised.
const MEMBER_FAILED: &str = "failed";
/// A member that never downloaded.
const MEMBER_NOT_LOADED: &str = "not_loaded";
/// A member withheld because the run was cancelled first.
const MEMBER_NOT_EVALUATED: &str = "not_evaluated";

/// Where one member stands for one definition at one valid time, in the
/// order ``state_counts`` reports them (Python ``MEMBER_STATES``).
const MEMBER_STATES: [&str; 7] = [
    MEMBER_QUALIFYING,
    MEMBER_NONQUALIFYING,
    MEMBER_UNUSABLE,
    MEMBER_NO_PROFILE,
    MEMBER_FAILED,
    MEMBER_NOT_LOADED,
    MEMBER_NOT_EVALUATED,
];

/// Values at or below ``-9998`` are the missing-data sentinel, never a
/// threshold operand (Python ``_finite``).
const MISSING_SENTINEL: f64 = -9998.0;

// ---------------------------------------------------------------------------
// Python formatting and error helpers
// ---------------------------------------------------------------------------

/// Python's ``repr`` for the strings that appear in threshold messages:
/// single quotes by default, double quotes when the text already contains
/// one, and backslash/quote escapes otherwise.
fn py_str_repr(text: &str) -> String {
    if !text.contains('\'') {
        format!("'{text}'")
    } else if !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

/// Raise the module's ``ThresholdError`` with ``message``, falling back to
/// ``ValueError`` when the class cannot be imported.
fn threshold_error(py: Python<'_>, message: String) -> PyErr {
    match PyModule::import(py, "sharpmod.analysis.ensemble_thresholds")
        .and_then(|module| module.getattr("ThresholdError"))
        .and_then(|class| class.call1((message.clone(),)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => PyValueError::new_err(message),
    }
}

/// The module's ``_finite``: a usable threshold operand is finite and
/// strictly above the missing-data sentinel.
fn finite_core(value: Option<f64>) -> Option<f64> {
    value.filter(|number| number.is_finite() && *number > MISSING_SENTINEL)
}

// ---------------------------------------------------------------------------
// Condition validation (ThresholdCondition.__post_init__)
// ---------------------------------------------------------------------------

/// One condition as ``ThresholdCondition.__post_init__`` normalizes it:
/// ``(metric, operator, value, upper, units)``.
pub(crate) type ValidatedConditionRow = (String, String, f64, Option<f64>, String);

/// The pure half of ``ThresholdCondition.__post_init__``: trim the metric
/// and operator, drop non-finite or sentinel ``value``/``upper`` inputs,
/// and enforce each operator's arity rules with the original messages.
pub(crate) fn validate_condition_core(
    metric: &str,
    operator: &str,
    value: Option<f64>,
    upper: Option<f64>,
    units: &str,
) -> Result<ValidatedConditionRow, String> {
    let metric = metric.trim();
    let operator = operator.trim();
    let value = finite_core(value);
    let upper = finite_core(upper);
    if metric.is_empty() {
        return Err("a threshold metric is required".to_string());
    }
    if !OPERATOR_ORDER.contains(&operator) {
        return Err(format!("unsupported threshold operator: {operator}"));
    }
    let Some(value) = value else {
        return Err("threshold value must be finite".to_string());
    };
    if operator == "between" {
        // Python: ``upper is None or upper < value`` fails the check.
        if upper.is_none_or(|upper| upper < value) {
            return Err("between requires an upper value at least as large".to_string());
        }
    } else if upper.is_some() {
        return Err("upper is only valid with the between operator".to_string());
    }
    Ok((
        metric.to_string(),
        operator.to_string(),
        value,
        upper,
        units.to_string(),
    ))
}

/// Validate and normalize one threshold condition.
///
/// Mirrors ``ThresholdCondition.__post_init__``: the metric and operator are
/// trimmed, ``value`` and ``upper`` keep only finite operands strictly above
/// the ``-9998`` sentinel, ``between`` requires an upper bound at least as
/// large, and every other operator rejects one.  Failures raise the module's
/// ``ThresholdError`` with the original message; success returns
/// ``(metric, operator, value, upper, units)``.
#[pyfunction]
#[pyo3(signature = (metric, operator, value, upper = None, units = ""))]
fn threshold_validate_condition(
    py: Python<'_>,
    metric: &str,
    operator: &str,
    value: Option<f64>,
    upper: Option<f64>,
    units: &str,
) -> PyResult<ValidatedConditionRow> {
    validate_condition_core(metric, operator, value, upper, units)
        .map_err(|message| threshold_error(py, message))
}

// ---------------------------------------------------------------------------
// Definition validation (ThresholdDefinition.__post_init__)
// ---------------------------------------------------------------------------

/// The pure half of ``ThresholdDefinition.__post_init__``: a trimmed
/// non-empty name, at least one condition, and no metric twice -- compared
/// on the trimmed metric, the way the Python dataclass sees each condition.
pub(crate) fn validate_definition_core(name: &str, metrics: &[String]) -> Result<String, String> {
    let name = name.trim();
    if name.is_empty() {
        return Err("a threshold name is required".to_string());
    }
    if metrics.is_empty() {
        return Err("at least one threshold condition is required".to_string());
    }
    let mut seen: Vec<&str> = Vec::with_capacity(metrics.len());
    for metric in metrics {
        let metric = metric.trim();
        if seen.contains(&metric) {
            return Err("a metric may appear only once in a joint threshold".to_string());
        }
        seen.push(metric);
    }
    Ok(name.to_string())
}

/// Validate one threshold definition's name and metric list.
///
/// Mirrors ``ThresholdDefinition.__post_init__``: the name must be non-empty
/// after trimming, at least one condition is required, and no metric may
/// appear twice in a joint threshold.  Failures raise the module's
/// ``ThresholdError``; success returns the trimmed name.
#[pyfunction]
fn threshold_validate_definition(
    py: Python<'_>,
    name: &str,
    metrics: Vec<String>,
) -> PyResult<String> {
    validate_definition_core(name, &metrics).map_err(|message| threshold_error(py, message))
}

// ---------------------------------------------------------------------------
// Member-state derivation (ThresholdMemberResult.__post_init__)
// ---------------------------------------------------------------------------

/// The pure half of ``ThresholdMemberResult.__post_init__``: an explicit
/// state must be one of ``MEMBER_STATES``; an empty one is derived from the
/// flags -- ``satisfied is True`` qualifies, ``satisfied is False`` does
/// not, an unloaded member never loaded, and everything else is unusable.
pub(crate) fn derive_member_state_core(
    state: &str,
    loaded: bool,
    satisfied: Option<bool>,
) -> Result<String, String> {
    // Python's truthiness: any non-empty string, even blank, is an explicit
    // state and is checked verbatim rather than trimmed.
    if !state.is_empty() {
        if MEMBER_STATES.contains(&state) {
            Ok(state.to_string())
        } else {
            Err(format!("unsupported member state {}", py_str_repr(state)))
        }
    } else {
        let derived = match satisfied {
            Some(true) => MEMBER_QUALIFYING,
            Some(false) => MEMBER_NONQUALIFYING,
            None if !loaded => MEMBER_NOT_LOADED,
            None => MEMBER_UNUSABLE,
        };
        Ok(derived.to_string())
    }
}

/// Validate or derive one member's state.
///
/// A non-empty ``state`` must be one of the module's member states; an
/// empty one is derived from the flags in exactly the order
/// ``ThresholdMemberResult.__post_init__`` uses.  An unsupported state
/// raises ``ThresholdError`` with the Python repr of the offending word.
#[pyfunction]
fn threshold_derive_member_state(
    py: Python<'_>,
    state: &str,
    loaded: bool,
    satisfied: Option<bool>,
) -> PyResult<String> {
    derive_member_state_core(state, loaded, satisfied)
        .map_err(|message| threshold_error(py, message))
}

// ---------------------------------------------------------------------------
// Member condition evaluation (evaluate_threshold's member loop)
// ---------------------------------------------------------------------------

/// One condition's operands: ``(metric, operator, value, upper)``.
pub(crate) type ConditionTuple = (String, String, f64, Option<f64>);

/// One member's aggregated outcome: ``(usable, satisfied, state, reason)``.
pub(crate) type MemberEvaluationRow = (bool, Option<bool>, String, Option<String>);

/// One comparison, mirroring ``threshold_evaluate_condition``'s operator
/// table: ``==`` keeps its floating-point tolerance and ``between`` is the
/// inclusive ``value <= x <= upper`` band (a missing upper collapses to
/// ``value``).  An unknown operator yields no verdict.
fn evaluate_operator(
    operator: &str,
    value: f64,
    upper: Option<f64>,
    candidate: f64,
) -> Option<bool> {
    match operator {
        ">" => Some(candidate > value),
        ">=" => Some(candidate >= value),
        "<" => Some(candidate < value),
        "<=" => Some(candidate <= value),
        "==" => {
            let tolerance = (1e-9 * candidate.abs().max(value.abs())).max(1e-9);
            Some((candidate - value).abs() <= tolerance)
        }
        "between" => Some(candidate >= value && candidate <= upper.unwrap_or(value)),
        _ => None,
    }
}

/// The pure half of ``evaluate_threshold``'s per-member aggregation: filter
/// each candidate value through ``_finite``, evaluate every condition, and
/// derive the verdict -- a usable member has all values and is
/// ``qualifying``/``nonqualifying`` by the conjunction of its outcomes,
/// while an unusable one names every missing diagnostic in the reason.
pub(crate) fn evaluate_member_conditions_core(
    conditions: &[ConditionTuple],
    candidate_values: &[Option<f64>],
) -> MemberEvaluationRow {
    let mut outcomes: Vec<Option<bool>> = Vec::with_capacity(conditions.len());
    for (index, (_, operator, value, upper)) in conditions.iter().enumerate() {
        // A missing candidate row counts as a missing value, exactly like
        // an absent key in Python's values mapping.
        let candidate = finite_core(candidate_values.get(index).copied().flatten());
        outcomes.push(match candidate {
            Some(number) => evaluate_operator(operator, *value, *upper, number),
            None => None,
        });
    }
    let usable = outcomes.iter().all(|outcome| outcome.is_some());
    if usable {
        let satisfied = outcomes.iter().all(|outcome| outcome == &Some(true));
        let state = if satisfied {
            MEMBER_QUALIFYING
        } else {
            MEMBER_NONQUALIFYING
        };
        (true, Some(satisfied), state.to_string(), None)
    } else {
        let absent: Vec<&str> = conditions
            .iter()
            .zip(outcomes.iter())
            .filter(|(_, outcome)| outcome.is_none())
            .map(|(condition, _)| condition.0.as_str())
            .collect();
        (
            false,
            None,
            MEMBER_UNUSABLE.to_string(),
            Some(format!("no value for {}", absent.join(", "))),
        )
    }
}

/// Evaluate one member's conditions against its candidate values.
///
/// ``conditions`` are ``(metric, operator, value, upper)`` rows aligned with
/// ``candidate_values``; each candidate passes the ``-9998`` sentinel filter
/// first.  The verdict is ``(usable, satisfied, state, reason)`` exactly as
/// ``evaluate_threshold`` records it, including the ``no value for ...``
/// reason naming every missing diagnostic in condition order.
#[pyfunction]
fn threshold_evaluate_member_conditions(
    conditions: Vec<ConditionTuple>,
    candidate_values: Vec<Option<f64>>,
) -> MemberEvaluationRow {
    evaluate_member_conditions_core(&conditions, &candidate_values)
}

// ---------------------------------------------------------------------------
// Result count reconciliation (ThresholdResult)
// ---------------------------------------------------------------------------

/// The funnel ``ThresholdResult`` reports: ``(reconciles, nonqualifying,
/// loaded_unusable, not_loaded, not_evaluated, resolved, state_counts)``.
pub(crate) type ReconcileRow = (bool, i64, i64, i64, i64, i64, Vec<(String, i64)>);

/// The pure half of ``ThresholdResult``'s derived counts: the four terminal
/// groups that must partition the requested ensemble, the cancelled members
/// withheld from ``resolved``, and the per-state tally with zeros included,
/// in ``MEMBER_STATES`` order.
pub(crate) fn result_counts_reconcile_core(
    requested: i64,
    loaded: i64,
    usable: i64,
    qualifying: i64,
    member_states: &[String],
) -> ReconcileRow {
    let nonqualifying = usable - qualifying;
    let loaded_unusable = loaded - usable;
    let not_loaded = requested - loaded;
    let not_evaluated = member_states
        .iter()
        .filter(|state| state.as_str() == MEMBER_NOT_EVALUATED)
        .count() as i64;
    let resolved = requested - not_evaluated;
    // Python's chained comparison: the four terminal groups add up to the
    // requested ensemble, which is also exactly the member count.
    let reconciles = qualifying + nonqualifying + loaded_unusable + not_loaded == requested
        && requested == member_states.len() as i64;
    let state_counts = MEMBER_STATES
        .iter()
        .map(|state| {
            let count = member_states
                .iter()
                .filter(|member| member.as_str() == *state)
                .count() as i64;
            ((*state).to_string(), count)
        })
        .collect();
    (
        reconciles,
        nonqualifying,
        loaded_unusable,
        not_loaded,
        not_evaluated,
        resolved,
        state_counts,
    )
}

/// Reconcile one threshold result's counts.
///
/// ``member_states`` are the recorded states of every member in the result.
/// The returned row is ``(reconciles, nonqualifying, loaded_unusable,
/// not_loaded, not_evaluated, resolved, state_counts)``: the four terminal
/// groups (``qualifying``, ``nonqualifying``, ``loaded_unusable``,
/// ``not_loaded``) must add up to the requested ensemble and to the member
/// count for ``reconciles`` to hold, and the tally reports each member state
/// in ``MEMBER_STATES`` order with zero entries included.
#[pyfunction]
fn threshold_result_counts_reconcile(
    requested: i64,
    loaded: i64,
    usable: i64,
    qualifying: i64,
    member_states: Vec<String>,
) -> ReconcileRow {
    result_counts_reconcile_core(requested, loaded, usable, qualifying, &member_states)
}

// ---------------------------------------------------------------------------
// Resume consistency (evaluate_threshold's resume checks)
// ---------------------------------------------------------------------------

/// The pure half of ``evaluate_threshold``'s resume checks: the resumed
/// member sequence must equal the requested one, each resumed member's
/// loaded flag must match the live collection, and a loaded member recorded
/// as ``no_profile`` must match the live profile availability (and vice
/// versa).  ``loaded_flags`` and ``has_profile_flags`` align with
/// ``expected_members``; a missing flag entry counts as ``false``.
pub(crate) fn validate_resume_members_core(
    expected_members: &[String],
    resume_members: &[(String, bool, String)],
    loaded_flags: &[bool],
    has_profile_flags: &[bool],
) -> Result<(), String> {
    let sequence_matches = resume_members.len() == expected_members.len()
        && resume_members
            .iter()
            .map(|(member, _, _)| member.as_str())
            .eq(expected_members.iter().map(String::as_str));
    if !sequence_matches {
        return Err("threshold resume member request does not match".to_string());
    }
    for (index, (_, resume_loaded, resume_state)) in resume_members.iter().enumerate() {
        let loaded_flag = loaded_flags.get(index).copied().unwrap_or(false);
        let has_profile_flag = has_profile_flags.get(index).copied().unwrap_or(false);
        if *resume_loaded != loaded_flag {
            return Err("threshold resume loaded-member set does not match".to_string());
        }
        if *resume_loaded && ((resume_state.as_str() == MEMBER_NO_PROFILE) == has_profile_flag) {
            return Err("threshold resume profile availability does not match".to_string());
        }
    }
    Ok(())
}

/// Check a resume state against the live collection before a retry.
///
/// ``resume_members`` are the ``(member, loaded, state)`` rows of the result
/// being resumed, in order; ``loaded_flags`` and ``has_profile_flags``
/// carry the collection's current answer for each expected member.  Any
/// mismatch raises ``ThresholdError`` with the original message: the member
/// request, the loaded-member set, or the profile availability.
#[pyfunction]
fn threshold_validate_resume_members(
    py: Python<'_>,
    expected_members: Vec<String>,
    resume_members: Vec<(String, bool, String)>,
    loaded_flags: Vec<bool>,
    has_profile_flags: Vec<bool>,
) -> PyResult<()> {
    validate_resume_members_core(
        &expected_members,
        &resume_members,
        &loaded_flags,
        &has_profile_flags,
    )
    .map_err(|message| threshold_error(py, message))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(threshold_validate_condition, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_validate_definition, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_derive_member_state, module)?)?;
    module.add_function(wrap_pyfunction!(
        threshold_evaluate_member_conditions,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(threshold_result_counts_reconcile, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_validate_resume_members, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owned(strings: &[&str]) -> Vec<String> {
        strings.iter().map(|string| (*string).to_string()).collect()
    }

    fn condition(metric: &str, operator: &str, value: f64, upper: Option<f64>) -> ConditionTuple {
        (metric.to_string(), operator.to_string(), value, upper)
    }

    // -----------------------------------------------------------------
    // Condition validation
    // -----------------------------------------------------------------

    #[test]
    fn finite_values_keep_only_operands_above_the_sentinel() {
        assert_eq!(finite_core(None), None);
        assert_eq!(finite_core(Some(f64::NAN)), None);
        assert_eq!(finite_core(Some(f64::INFINITY)), None);
        assert_eq!(finite_core(Some(f64::NEG_INFINITY)), None);
        assert_eq!(finite_core(Some(MISSING_SENTINEL)), None);
        assert_eq!(finite_core(Some(-9998.1)), None);
        assert_eq!(finite_core(Some(-9997.9)), Some(-9997.9));
        assert_eq!(finite_core(Some(0.0)), Some(0.0));
    }

    #[test]
    fn conditions_validate_trim_and_pass_units_through() {
        let validated =
            validate_condition_core(" mlcape ", " >= ", Some(1000.0), None, "J kg-1").unwrap();
        assert_eq!(
            validated,
            (
                "mlcape".to_string(),
                ">=".to_string(),
                1000.0,
                None,
                "J kg-1".to_string()
            )
        );
        // ``str(units or "")`` is the identity for a string input.
        let bare = validate_condition_core("m", ">", Some(1.0), None, "").unwrap();
        assert_eq!(bare.4, "");
    }

    #[test]
    fn conditions_reject_with_python_messages_in_order() {
        assert_eq!(
            validate_condition_core("", ">=", Some(1.0), None, "").unwrap_err(),
            "a threshold metric is required"
        );
        assert_eq!(
            validate_condition_core("mlcape", "~", Some(1.0), None, "").unwrap_err(),
            "unsupported threshold operator: ~"
        );
        // The trimmed operator appears in the message.
        assert_eq!(
            validate_condition_core("mlcape", " ~ ", Some(1.0), None, "").unwrap_err(),
            "unsupported threshold operator: ~"
        );
        for value in [None, Some(f64::NAN), Some(MISSING_SENTINEL)] {
            assert_eq!(
                validate_condition_core("mlcape", ">=", value, None, "").unwrap_err(),
                "threshold value must be finite"
            );
        }
        assert_eq!(
            validate_condition_core("mlcape", "between", Some(2.0), None, "").unwrap_err(),
            "between requires an upper value at least as large"
        );
        assert_eq!(
            validate_condition_core("mlcape", "between", Some(2.0), Some(1.0), "").unwrap_err(),
            "between requires an upper value at least as large"
        );
        assert_eq!(
            validate_condition_core("mlcape", ">", Some(5.0), Some(6.0), "").unwrap_err(),
            "upper is only valid with the between operator"
        );
    }

    #[test]
    fn conditions_accept_between_boundaries_and_filter_upper() {
        let closed = validate_condition_core("m", "between", Some(2.0), Some(2.0), "").unwrap();
        assert_eq!(closed.3, Some(2.0), "upper at least as large as value");
        // A non-finite upper is dropped by ``_finite`` first, so it never
        // reaches the "only valid with between" check.
        let dropped =
            validate_condition_core("m", ">", Some(5.0), Some(f64::INFINITY), "").unwrap();
        assert_eq!(dropped.3, None);
    }

    // -----------------------------------------------------------------
    // Definition validation
    // -----------------------------------------------------------------

    #[test]
    fn definitions_require_a_name_conditions_and_unique_metrics() {
        assert_eq!(
            validate_definition_core(" CAPE and shear ", &owned(&["mlcape", "shear"])).unwrap(),
            "CAPE and shear"
        );
        assert_eq!(
            validate_definition_core("  ", &owned(&["mlcape"])).unwrap_err(),
            "a threshold name is required"
        );
        assert_eq!(
            validate_definition_core("x", &[]).unwrap_err(),
            "at least one threshold condition is required"
        );
        assert_eq!(
            validate_definition_core("x", &owned(&["mlcape", "mlcape"])).unwrap_err(),
            "a metric may appear only once in a joint threshold"
        );
        // Compared on the trimmed metric, the way the Python dataclass
        // sees each condition.
        assert_eq!(
            validate_definition_core("x", &owned(&["mlcape", " mlcape "])).unwrap_err(),
            "a metric may appear only once in a joint threshold"
        );
        assert!(validate_definition_core("x", &owned(&["mlcape", "shear"])).is_ok());
    }

    // -----------------------------------------------------------------
    // Member-state derivation
    // -----------------------------------------------------------------

    #[test]
    fn explicit_states_pass_through_verbatim() {
        for state in MEMBER_STATES {
            assert_eq!(derive_member_state_core(state, true, None).unwrap(), state);
        }
        // An explicit state wins over the flags.
        assert_eq!(
            derive_member_state_core("qualifying", false, Some(false)).unwrap(),
            "qualifying"
        );
        // Python truthiness: a blank-but-non-empty state is checked, not
        // trimmed or derived from.
        assert_eq!(
            derive_member_state_core(" ", false, None).unwrap_err(),
            "unsupported member state ' '"
        );
    }

    #[test]
    fn unsupported_states_carry_the_python_repr() {
        assert_eq!(
            derive_member_state_core("bogus", true, None).unwrap_err(),
            "unsupported member state 'bogus'"
        );
        assert_eq!(
            derive_member_state_core("it's", true, None).unwrap_err(),
            "unsupported member state \"it's\""
        );
        assert_eq!(
            derive_member_state_core("bo'th\"", true, None).unwrap_err(),
            "unsupported member state 'bo\\'th\"'"
        );
    }

    #[test]
    fn states_derive_from_the_flags_in_python_order() {
        assert_eq!(
            derive_member_state_core("", true, Some(true)).unwrap(),
            MEMBER_QUALIFYING
        );
        assert_eq!(
            derive_member_state_core("", true, Some(false)).unwrap(),
            MEMBER_NONQUALIFYING
        );
        assert_eq!(
            derive_member_state_core("", false, None).unwrap(),
            MEMBER_NOT_LOADED
        );
        assert_eq!(
            derive_member_state_core("", true, None).unwrap(),
            MEMBER_UNUSABLE
        );
        // ``satisfied`` outranks ``loaded`` in both directions.
        assert_eq!(
            derive_member_state_core("", false, Some(false)).unwrap(),
            MEMBER_NONQUALIFYING
        );
    }

    // -----------------------------------------------------------------
    // Member condition evaluation
    // -----------------------------------------------------------------

    #[test]
    fn members_qualify_only_when_every_condition_passes() {
        let conditions = [
            condition("mlcape", ">=", 1000.0, None),
            condition("shear", ">=", 40.0, None),
        ];
        let winner = evaluate_member_conditions_core(&conditions, &[Some(1800.0), Some(42.0)]);
        assert_eq!(winner, (true, Some(true), "qualifying".to_string(), None));
        let loser = evaluate_member_conditions_core(&conditions, &[Some(900.0), Some(48.0)]);
        assert_eq!(
            loser,
            (true, Some(false), "nonqualifying".to_string(), None)
        );
    }

    #[test]
    fn unusable_members_name_every_missing_diagnostic() {
        let conditions = [
            condition("mlcape", ">=", 1000.0, None),
            condition("shear", ">=", 40.0, None),
        ];
        let missing_one = evaluate_member_conditions_core(&conditions, &[None, Some(50.0)]);
        assert_eq!(
            missing_one,
            (
                false,
                None,
                "unusable".to_string(),
                Some("no value for mlcape".to_string())
            )
        );
        let missing_both =
            evaluate_member_conditions_core(&conditions, &[Some(f64::NAN), Some(MISSING_SENTINEL)]);
        assert_eq!(
            missing_both.3,
            Some("no value for mlcape, shear".to_string()),
            "conditions are named in condition order"
        );
        // A short candidate list is missing values, not an error.
        let short = evaluate_member_conditions_core(&conditions, &[]);
        assert_eq!(short.3, Some("no value for mlcape, shear".to_string()));
    }

    #[test]
    fn sentinel_and_non_finite_candidates_are_missing() {
        let conditions = [condition("mlcape", ">=", 1000.0, None)];
        for value in [None, Some(f64::NAN), Some(MISSING_SENTINEL)] {
            let verdict = evaluate_member_conditions_core(&conditions, &[value]);
            assert_eq!((verdict.0, verdict.1), (false, None));
        }
        let present = evaluate_member_conditions_core(&conditions, &[Some(-9997.0)]);
        assert_eq!(
            present,
            (true, Some(false), "nonqualifying".to_string(), None)
        );
    }

    #[test]
    fn between_bands_are_inclusive_on_both_ends() {
        let conditions = [condition("mlcape", "between", 800.0, Some(1900.0))];
        for candidate in [800.0, 1900.0, 1200.0] {
            let verdict = evaluate_member_conditions_core(&conditions, &[Some(candidate)]);
            assert_eq!(
                verdict,
                (true, Some(true), "qualifying".to_string(), None),
                "candidate {candidate}"
            );
        }
        for candidate in [799.9, 1900.1] {
            let verdict = evaluate_member_conditions_core(&conditions, &[Some(candidate)]);
            assert_eq!(
                verdict,
                (true, Some(false), "nonqualifying".to_string(), None),
                "candidate {candidate}"
            );
        }
    }

    #[test]
    fn equality_keeps_the_shared_tolerance() {
        assert_eq!(evaluate_operator("==", 100.0, None, 100.0), Some(true));
        // The tolerance mirrors threshold_evaluate_condition, which the
        // Python evaluator delegates to for every == condition.
        assert_eq!(
            evaluate_operator("==", 100.0, None, 100.0000000001),
            Some(true)
        );
        assert_eq!(evaluate_operator("==", 100.0, None, 100.1), Some(false));
        // A missing upper collapses to the value, like the shared rule.
        assert_eq!(evaluate_operator("between", 5.0, None, 5.0), Some(true));
        assert_eq!(evaluate_operator("~", 1.0, None, 2.0), None);
    }

    #[test]
    fn unknown_operators_leave_the_member_unusable() {
        let conditions = [condition("m", "~", 1.0, None)];
        let verdict = evaluate_member_conditions_core(&conditions, &[Some(2.0)]);
        assert!(!verdict.0);
        assert_eq!(verdict.3, Some("no value for m".to_string()));
    }

    // -----------------------------------------------------------------
    // Count reconciliation
    // -----------------------------------------------------------------

    #[test]
    fn counts_partition_the_requested_ensemble() {
        let states = owned(&["qualifying", "nonqualifying", "unusable", "not_loaded"]);
        let row = result_counts_reconcile_core(4, 3, 2, 1, &states);
        let expected_counts = vec![
            ("qualifying".to_string(), 1),
            ("nonqualifying".to_string(), 1),
            ("unusable".to_string(), 1),
            ("no_profile".to_string(), 0),
            ("failed".to_string(), 0),
            ("not_loaded".to_string(), 1),
            ("not_evaluated".to_string(), 0),
        ];
        assert_eq!(
            row,
            (true, 1, 1, 1, 0, 4, expected_counts),
            "the funnel from test_joint_threshold_uses_only_the_common_valid_subset"
        );
    }

    #[test]
    fn cancelled_members_resolve_but_never_qualify() {
        let states = owned(&["qualifying", "not_evaluated", "not_evaluated", "not_loaded"]);
        let row = result_counts_reconcile_core(4, 3, 1, 1, &states);
        assert!(row.0);
        assert_eq!(row.1, 0);
        assert_eq!(row.2, 2);
        assert_eq!(row.3, 1);
        assert_eq!(row.4, 2);
        assert_eq!(row.5, 2);
        assert_eq!(row.6[6], ("not_evaluated".to_string(), 2));
    }

    #[test]
    fn mismatched_member_counts_do_not_reconcile() {
        let states = owned(&["qualifying", "not_loaded"]);
        let row = result_counts_reconcile_core(3, 2, 1, 1, &states);
        // Two terminal groups plus one phantom member: 2 != 3.
        assert!(!row.0);
        assert_eq!(row.5, 3, "resolved counts the requested ensemble");
        // The state tally still reports every state with its zeros.
        assert_eq!(row.6.len(), MEMBER_STATES.len());
        assert_eq!(row.6[0], ("qualifying".to_string(), 1));
        assert_eq!(row.6[5], ("not_loaded".to_string(), 1));
    }

    // -----------------------------------------------------------------
    // Resume consistency
    // -----------------------------------------------------------------

    fn resume(member: &str, loaded: bool, state: &str) -> (String, bool, String) {
        (member.to_string(), loaded, state.to_string())
    }

    #[test]
    fn consistent_resume_states_pass() {
        let expected = owned(&["c00", "p01", "p03"]);
        let rows = [
            resume("c00", true, "qualifying"),
            resume("p01", true, "no_profile"),
            resume("p03", false, "not_loaded"),
        ];
        let loaded = [true, true, false];
        let has_profile = [true, false, false];
        assert_eq!(
            validate_resume_members_core(&expected, &rows, &loaded, &has_profile),
            Ok(())
        );
    }

    #[test]
    fn resume_sequences_must_match_the_request() {
        let expected = owned(&["c00", "p01"]);
        let swapped = [
            resume("p01", true, "qualifying"),
            resume("c00", true, "qualifying"),
        ];
        assert_eq!(
            validate_resume_members_core(&expected, &swapped, &[true, true], &[true, true])
                .unwrap_err(),
            "threshold resume member request does not match"
        );
        let missing = [resume("c00", true, "qualifying")];
        assert_eq!(
            validate_resume_members_core(&expected, &missing, &[true], &[true]).unwrap_err(),
            "threshold resume member request does not match"
        );
        let extra = [
            resume("c00", true, "qualifying"),
            resume("p01", true, "qualifying"),
            resume("p02", true, "qualifying"),
        ];
        assert_eq!(
            validate_resume_members_core(
                &expected,
                &extra,
                &[true, true, true],
                &[true, true, true]
            )
            .unwrap_err(),
            "threshold resume member request does not match"
        );
    }

    #[test]
    fn resume_loaded_flags_must_match_the_collection() {
        let expected = owned(&["c00"]);
        let rows = [resume("c00", true, "qualifying")];
        assert_eq!(
            validate_resume_members_core(&expected, &rows, &[false], &[false]).unwrap_err(),
            "threshold resume loaded-member set does not match"
        );
        // An unloaded resume row skips the profile check entirely.
        let not_loaded = [resume("c00", false, "not_loaded")];
        assert_eq!(
            validate_resume_members_core(&expected, &not_loaded, &[false], &[true]),
            Ok(())
        );
    }

    #[test]
    fn resume_profile_availability_must_match_the_state() {
        let expected = owned(&["c00"]);
        // Recorded as no_profile while a profile exists.
        let claims_missing = [resume("c00", true, "no_profile")];
        assert_eq!(
            validate_resume_members_core(&expected, &claims_missing, &[true], &[true]).unwrap_err(),
            "threshold resume profile availability does not match"
        );
        // Recorded with a profile while none exists.
        let claims_present = [resume("c00", true, "qualifying")];
        assert_eq!(
            validate_resume_members_core(&expected, &claims_present, &[true], &[false])
                .unwrap_err(),
            "threshold resume profile availability does not match"
        );
        // Both consistent directions pass.
        assert_eq!(
            validate_resume_members_core(&expected, &claims_missing, &[true], &[false]),
            Ok(())
        );
        assert_eq!(
            validate_resume_members_core(&expected, &claims_present, &[true], &[true]),
            Ok(())
        );
        // A state that is not no_profile but also not a profile verdict
        // still agrees with an available profile.
        let failed = [resume("c00", true, "failed")];
        assert_eq!(
            validate_resume_members_core(&expected, &failed, &[true], &[true]),
            Ok(())
        );
    }

    #[test]
    fn member_state_reprs_match_python() {
        assert_eq!(py_str_repr("plain"), "'plain'");
        assert_eq!(py_str_repr("it's"), "\"it's\"");
        assert_eq!(py_str_repr("bo'th\""), "'bo\\'th\"'");
    }
}
