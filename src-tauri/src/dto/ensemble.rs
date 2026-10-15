//! `get_ensemble_summary` DTOs: one ensemble run's member ledger and metric
//! spreads at one valid time. Read from the bridge in snake_case, written to
//! the webview in camelCase, like `soundings::SoundingSummary`. Every quantity
//! arrives labelled (key, label, units, decimals) from sharpmod, so the
//! webview renders the tables without naming or computing any of them.
//!
//! Rust is the trust boundary in front of the webview: every payload is
//! checked with `validate` before it is returned, and the TypeScript parser
//! in `lib/contracts/ensemble.ts` mirrors each rule here.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_opt_text, check_text};

pub const MAX_MEMBERS: usize = 32;
pub const MIN_MEMBERS: usize = 2;
pub const MAX_METRICS: usize = 16;

/// One requested member that did not produce a loaded profile: the ledger's
/// final status and the provider's own reason.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct MemberFailure {
    pub member: String,
    pub status: String,
    pub reason: Option<String>,
}

/// One diagnostic's spread across the usable members. All five statistics are
/// present together, or all absent when no member reported the diagnostic.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct MetricSpread {
    pub key: String,
    pub label: String,
    pub units: String,
    pub decimals: u8,
    pub count: u32,
    pub p10: Option<f64>,
    pub median: Option<f64>,
    pub p90: Option<f64>,
    pub minimum: Option<f64>,
    pub maximum: Option<f64>,
}

/// One requested member's own row: its outcome, its reason when it has one,
/// and one value per metric (absence stays absent).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct MemberSample {
    pub member: String,
    pub outcome: String,
    pub reason: Option<String>,
    pub values: Vec<Option<f64>>,
}

/// What one ensemble run's members said at one valid time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct EnsembleSummary {
    pub valid_time: Option<String>,
    pub reason: Option<String>,
    pub requested_members: Vec<String>,
    pub loaded_members: Vec<String>,
    pub failures: Vec<MemberFailure>,
    pub metrics: Vec<MetricSpread>,
    pub samples: Vec<MemberSample>,
}

fn unique(values: &[String], what: &str) -> Result<(), String> {
    let mut seen = std::collections::BTreeSet::new();
    for value in values {
        if !seen.insert(value) {
            return Err(format!("{what} must be unique; {value:?} appears twice"));
        }
    }
    Ok(())
}

fn finite(value: f64) -> bool {
    value.is_finite()
}

fn time_ok(value: &Option<String>) -> Result<(), String> {
    match value {
        None => Ok(()),
        Some(text) if (10..=40).contains(&text.chars().count()) && text.is_ascii() => Ok(()),
        Some(_) => Err("an ensemble time must be an RFC 3339 time".into()),
    }
}

impl MetricSpread {
    pub fn validate(&self) -> Result<(), String> {
        check_text("key", &self.key, 1, 16)?;
        check_text("label", &self.label, 1, 120)?;
        check_text("units", &self.units, 0, 40)?;
        if self.decimals > 6 {
            return Err("decimals must be at most 6".into());
        }
        if self.count as usize > MAX_MEMBERS {
            return Err("a diagnostic cannot report more members than were requested".into());
        }
        let stats = [self.p10, self.median, self.p90, self.minimum, self.maximum];
        if self.count == 0 {
            if stats.iter().any(Option::is_some) {
                return Err("a spread with no members carries no statistics".into());
            }
            return Ok(());
        }
        let [Some(p10), Some(median), Some(p90), Some(minimum), Some(maximum)] = stats else {
            return Err("a spread with members carries every statistic".into());
        };
        if ![p10, median, p90, minimum, maximum]
            .iter()
            .all(|v| finite(*v))
        {
            return Err("a statistic must be finite".into());
        }
        if !(minimum <= p10 && p10 <= median && median <= p90 && p90 <= maximum) {
            return Err("a spread must read minimum to p10 to median to p90 to maximum".into());
        }
        Ok(())
    }
}

impl MemberSample {
    pub fn validate(&self, metrics: usize) -> Result<(), String> {
        check_text("member", &self.member, 1, 16)?;
        check_opt_text("reason", self.reason.as_ref(), 300)?;
        if !matches!(
            self.outcome.as_str(),
            "completed" | "failed" | "cancelled" | "unavailable"
        ) {
            return Err("an outcome must be completed, failed, cancelled, or unavailable".into());
        }
        if self.values.len() != metrics {
            return Err("a member row carries one value per metric".into());
        }
        if self.outcome != "completed" && self.values.iter().any(Option::is_some) {
            return Err("only a completed member reports values".into());
        }
        if self.values.iter().flatten().any(|value| !finite(*value)) {
            return Err("a member value must be finite when present".into());
        }
        Ok(())
    }
}

impl EnsembleSummary {
    pub fn validate(&self) -> Result<(), String> {
        time_ok(&self.valid_time)?;
        check_opt_text("reason", self.reason.as_ref(), 300)?;
        if !(MIN_MEMBERS..=MAX_MEMBERS).contains(&self.requested_members.len()) {
            return Err("requestedMembers must hold 2 to 32 member labels".into());
        }
        for member in &self.requested_members {
            check_text("member", member, 1, 16)?;
        }
        unique(&self.requested_members, "requestedMembers")?;
        unique(&self.loaded_members, "loadedMembers")?;
        unique(
            &self
                .failures
                .iter()
                .map(|f| f.member.clone())
                .collect::<Vec<_>>(),
            "failures",
        )?;
        if !(1..=MAX_METRICS).contains(&self.metrics.len()) {
            return Err("metrics must hold 1 to 16 rows".into());
        }
        unique(
            &self
                .metrics
                .iter()
                .map(|m| m.key.clone())
                .collect::<Vec<_>>(),
            "metrics",
        )?;
        if self.samples.len() != self.requested_members.len() {
            return Err("every requested member keeps one row".into());
        }
        unique(
            &self
                .samples
                .iter()
                .map(|s| s.member.clone())
                .collect::<Vec<_>>(),
            "samples",
        )?;

        let requested: std::collections::BTreeSet<&str> =
            self.requested_members.iter().map(String::as_str).collect();
        let loaded: std::collections::BTreeSet<&str> =
            self.loaded_members.iter().map(String::as_str).collect();
        if !loaded.is_subset(&requested) {
            return Err("a loaded member must belong to the request".into());
        }
        let mut resolved: std::collections::BTreeSet<&str> = loaded.clone();
        for failure in &self.failures {
            failure.validate()?;
            if !requested.contains(failure.member.as_str()) {
                return Err("a failed member must belong to the request".into());
            }
            if !resolved.insert(failure.member.as_str()) {
                return Err("a member cannot be both loaded and unavailable".into());
            }
        }
        if resolved.len() != requested.len() {
            return Err("every requested member is loaded or carries one status".into());
        }
        let sampled: std::collections::BTreeSet<&str> =
            self.samples.iter().map(|s| s.member.as_str()).collect();
        if sampled != requested {
            return Err("the member rows must cover the request".into());
        }

        for metric in &self.metrics {
            metric.validate()?;
        }
        for sample in &self.samples {
            sample.validate(self.metrics.len())?;
        }
        // The counts are the samples' own tallies: each spread says how many
        // members reported it, and the member rows must agree.
        for (index, metric) in self.metrics.iter().enumerate() {
            let reported = self
                .samples
                .iter()
                .filter(|s| s.outcome == "completed" && s.values[index].is_some())
                .count() as u32;
            if metric.count != reported {
                return Err(format!(
                    "metrics[{index}] count disagrees with the member rows"
                ));
            }
        }
        Ok(())
    }
}

impl MemberFailure {
    pub fn validate(&self) -> Result<(), String> {
        check_text("member", &self.member, 1, 16)?;
        check_opt_text("reason", self.reason.as_ref(), 300)?;
        if !matches!(self.status.as_str(), "failed" | "cancelled" | "unknown") {
            return Err("a status must be failed, cancelled, or unknown".into());
        }
        Ok(())
    }
}
