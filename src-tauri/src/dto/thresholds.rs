//! `evaluate_threshold` DTOs: one definition's member verdicts with their
//! denominators. Read from the bridge in snake_case (the evaluator's own
//! `as_dict`), written to the webview in camelCase, like
//! `ensemble::EnsembleSummary`. Every label, unit, member state, and
//! interpretation sentence arrives from sharpmod, so the webview renders the
//! tables without naming or computing any quantity. The form's catalog and the
//! CSV download live in `dto::threshold_catalog`.
//!
//! Rust is the trust boundary in front of the webview: every payload is
//! checked with `validate` before it is returned, and the TypeScript parser in
//! `lib/contracts/thresholds.ts` mirrors each rule here.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_opt_text, check_text, unique};

pub const MAX_CONDITIONS: usize = 3;
pub const MAX_NAME: usize = 120;
pub const MAX_METRIC: usize = 32;
pub const MAX_MEMBERS: usize = 32;
/// Generously beyond every numeric-control bound; the engine only requires
/// finite values (bridge/thresholds.py `VALUE_BOUND`).
pub const VALUE_BOUND: f64 = 1e9;

/// The comparison rules, in the order every control lists them.
pub const OPERATORS: [&str; 6] = [">", ">=", "<", "<=", "==", "between"];

/// The seven member states, exactly one of which every requested member lands
/// in (analysis/ensemble_thresholds.py `MEMBER_STATES`).
pub const STATES: [&str; 7] = [
    "qualifying",
    "nonqualifying",
    "unusable",
    "no_profile",
    "failed",
    "not_loaded",
    "not_evaluated",
];

fn finite(value: f64) -> bool {
    value.is_finite() && value.abs() <= VALUE_BOUND
}

/// One condition as the form sends it. Units are filled from the registry
/// server-side, so the frontend never carries a unit string of its own.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ThresholdConditionInput {
    pub metric: String,
    pub operator: String,
    pub value: f64,
    #[serde(default)]
    pub upper: Option<f64>,
}

impl ThresholdConditionInput {
    pub fn validate(&self) -> Result<(), String> {
        check_text("metric", &self.metric, 1, MAX_METRIC)?;
        check_text("operator", &self.operator, 1, 10)?;
        if !OPERATORS.contains(&self.operator.as_str()) {
            return Err("operator must be one of >, >=, <, <=, ==, between".into());
        }
        if !finite(self.value) {
            return Err("value must be finite".into());
        }
        match (self.operator.as_str(), self.upper) {
            ("between", None) => Err("between requires an upper value at least as large".into()),
            ("between", Some(upper)) if !finite(upper) || upper < self.value => {
                Err("between requires an upper value at least as large".into())
            }
            (other, Some(_)) if other != "between" => {
                Err("upper is only valid with the between operator".into())
            }
            _ => Ok(()),
        }
    }
}

/// The `definition` argument of `evaluate_threshold`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ThresholdDefinitionInput {
    pub name: String,
    pub conditions: Vec<ThresholdConditionInput>,
}

impl ThresholdDefinitionInput {
    pub fn validate(&self) -> Result<(), String> {
        check_text("name", &self.name, 1, MAX_NAME)?;
        if !(1..=MAX_CONDITIONS).contains(&self.conditions.len()) {
            return Err("conditions must hold 1 to 3 conditions".into());
        }
        for condition in &self.conditions {
            condition.validate()?;
        }
        let metrics: Vec<String> = self.conditions.iter().map(|c| c.metric.clone()).collect();
        unique(&metrics, "definition metrics")
    }

    /// The definition as snake_case bridge parameters (units filled there).
    pub fn bridge_value(&self) -> serde_json::Value {
        serde_json::json!({
            "name": self.name,
            "conditions": self.conditions.iter().map(|c| serde_json::json!({
                "metric": c.metric,
                "operator": c.operator,
                "value": c.value,
                "upper": c.upper,
            })).collect::<Vec<_>>(),
        })
    }
}

/// One condition as the evaluator returns it: the same rule with the registry
/// units the bridge filled in.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdCondition {
    pub metric: String,
    pub operator: String,
    pub value: f64,
    pub upper: Option<f64>,
    pub units: String,
}

impl ThresholdCondition {
    pub fn validate(&self) -> Result<(), String> {
        check_text("metric", &self.metric, 1, MAX_METRIC)?;
        check_text("units", &self.units, 0, 40)?;
        ThresholdConditionInput {
            metric: self.metric.clone(),
            operator: self.operator.clone(),
            value: self.value,
            upper: self.upper,
        }
        .validate()
    }
}

/// The rule the evaluator tested, in the document shape.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdDefinition {
    pub name: String,
    pub conditions: Vec<ThresholdCondition>,
}

impl ThresholdDefinition {
    pub fn validate(&self) -> Result<(), String> {
        check_text("name", &self.name, 1, MAX_NAME)?;
        if !(1..=MAX_CONDITIONS).contains(&self.conditions.len()) {
            return Err("conditions must hold 1 to 3 conditions".into());
        }
        for condition in &self.conditions {
            condition.validate()?;
        }
        let metrics: Vec<String> = self.conditions.iter().map(|c| c.metric.clone()).collect();
        unique(&metrics, "definition metrics")
    }

    /// The same rule as the form sends it: the server-filled units dropped.
    pub fn to_input(&self) -> ThresholdDefinitionInput {
        ThresholdDefinitionInput {
            name: self.name.clone(),
            conditions: self
                .conditions
                .iter()
                .map(|c| ThresholdConditionInput {
                    metric: c.metric.clone(),
                    operator: c.operator.clone(),
                    value: c.value,
                    upper: c.upper,
                })
                .collect(),
        }
    }
}

/// Where one requested member landed, with the values the rule read.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdMember {
    pub member: String,
    pub loaded: bool,
    pub usable: bool,
    pub satisfied: Option<bool>,
    pub state: String,
    pub state_label: String,
    pub values: BTreeMap<String, Option<f64>>,
    pub reason: Option<String>,
}

impl ThresholdMember {
    fn validate(&self) -> Result<(), String> {
        check_text("member", &self.member, 1, 16)?;
        check_text("stateLabel", &self.state_label, 1, 80)?;
        check_opt_text("reason", self.reason.as_ref(), 300)?;
        if !STATES.contains(&self.state.as_str()) {
            return Err("state must be one of the seven member states".into());
        }
        let verdict = matches!(self.state.as_str(), "qualifying" | "nonqualifying");
        if self.usable != verdict {
            return Err("only a qualifying or nonqualifying member is usable".into());
        }
        if self.satisfied.is_some() != self.usable {
            return Err("a member reports a verdict exactly when it is usable".into());
        }
        if self.state == "not_loaded" && (self.loaded || self.usable) {
            return Err("a member that never loaded reports neither".into());
        }
        for (key, value) in &self.values {
            check_text("values key", key, 1, MAX_METRIC)?;
            if value.is_some_and(|v| !v.is_finite()) {
                return Err("a member value must be finite when present".into());
            }
        }
        Ok(())
    }
}

/// One definition's verdicts across the requested members (the evaluator's
/// `as_dict`): the counts with their denominators, the agreement and coverage
/// sentences, and one row per member.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdEvaluation {
    pub definition: ThresholdDefinition,
    pub definition_summary: String,
    pub valid_time: Option<String>,
    pub requested_member_count: u32,
    pub loaded_member_count: u32,
    pub usable_member_count: u32,
    pub qualifying_member_count: u32,
    pub nonqualifying_member_count: u32,
    pub loaded_unusable_member_count: u32,
    pub not_loaded_member_count: u32,
    pub resolved_member_count: u32,
    pub not_evaluated_member_count: u32,
    pub agreement_fraction: Option<f64>,
    pub agreement: String,
    pub requested_coverage: Option<f64>,
    pub coverage: String,
    pub reconciles: bool,
    pub cancelled: bool,
    pub label: String,
    pub members: Vec<ThresholdMember>,
}

impl ThresholdEvaluation {
    pub fn validate(&self) -> Result<(), String> {
        self.definition.validate()?;
        check_text("definitionSummary", &self.definition_summary, 1, 300)?;
        match &self.valid_time {
            None => {}
            Some(text) if (10..=40).contains(&text.chars().count()) && text.is_ascii() => {}
            Some(_) => return Err("validTime must be an RFC 3339 time".into()),
        }
        check_text("agreement", &self.agreement, 1, 40)?;
        check_text("coverage", &self.coverage, 1, 40)?;
        check_text("label", &self.label, 1, 120)?;
        if !self.reconciles {
            return Err("the reported counts must reconcile".into());
        }
        if !(2..=MAX_MEMBERS).contains(&(self.requested_member_count as usize)) {
            return Err("requestedMemberCount must be 2 to 32".into());
        }
        if self.members.len() != self.requested_member_count as usize {
            return Err("every requested member keeps one row".into());
        }
        let labels: Vec<String> = self.members.iter().map(|m| m.member.clone()).collect();
        unique(&labels, "members")?;
        for member in &self.members {
            member.validate()?;
        }
        // The counts are the member rows' own tallies, and the four terminal
        // groups partition the request (the engine's `reconciles` claim).
        let count = |f: fn(&ThresholdMember) -> bool| -> u32 {
            self.members.iter().filter(|m| f(m)).count() as u32
        };
        let loaded = count(|m| m.loaded);
        let usable = count(|m| m.usable);
        let qualifying = count(|m| m.satisfied == Some(true));
        let nonqualifying = count(|m| m.satisfied == Some(false));
        let loaded_unusable = count(|m| m.loaded && !m.usable);
        let not_loaded = count(|m| m.state == "not_loaded");
        let not_evaluated = count(|m| m.state == "not_evaluated");
        if self.loaded_member_count != loaded
            || self.usable_member_count != usable
            || self.qualifying_member_count != qualifying
            || self.nonqualifying_member_count != nonqualifying
            || self.loaded_unusable_member_count != loaded_unusable
            || self.not_loaded_member_count != not_loaded
            || self.not_evaluated_member_count != not_evaluated
        {
            return Err("a reported count disagrees with the member rows".into());
        }
        if qualifying + nonqualifying != usable {
            return Err("the usable members split into qualifying and nonqualifying".into());
        }
        if usable + loaded_unusable != loaded {
            return Err("the loaded members split into usable and unusable".into());
        }
        if qualifying + nonqualifying + loaded_unusable + not_loaded != self.requested_member_count
        {
            return Err("the four terminal groups must partition the request".into());
        }
        if self.resolved_member_count + not_evaluated != self.requested_member_count {
            return Err("resolved and not-evaluated must partition the request".into());
        }
        // Every ratio names its denominator: present exactly when that
        // denominator is, and within [0, 1].
        if self.agreement_fraction.is_some() != (usable > 0) {
            return Err("an agreement fraction exists exactly when usable members do".into());
        }
        if self
            .agreement_fraction
            .is_some_and(|v| !v.is_finite() || !(0.0..=1.0).contains(&v))
        {
            return Err("an agreement fraction must lie within [0, 1]".into());
        }
        if self.requested_coverage.is_some() != (self.requested_member_count > 0) {
            return Err("a coverage exists exactly when members were requested".into());
        }
        if self
            .requested_coverage
            .is_some_and(|v| !v.is_finite() || !(0.0..=1.0).contains(&v))
        {
            return Err("a coverage must lie within [0, 1]".into());
        }
        // Each member reports the definition's diagnostics and nothing else.
        let wanted: std::collections::BTreeSet<&str> = self
            .definition
            .conditions
            .iter()
            .map(|c| c.metric.as_str())
            .collect();
        for member in &self.members {
            let got: std::collections::BTreeSet<&str> =
                member.values.keys().map(String::as_str).collect();
            if got != wanted {
                return Err("a member row carries the definition's diagnostics".into());
            }
        }
        Ok(())
    }
}
