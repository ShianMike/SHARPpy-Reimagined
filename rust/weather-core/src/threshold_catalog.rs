//! Ensemble threshold parameter registry, numeric-control bounds, condition evaluation, and CSV export.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const OPERATOR_ORDER: [&str; 6] = [">", ">=", "<", "<=", "==", "between"];
pub const DEFAULT_METRICS: [&str; 3] = ["mlcape", "shear_6km", "srh_3km"];
pub const DISCLAIMER: &str =
    "Ensemble ingredient agreement; not a calibrated severe-weather probability.";

#[derive(Clone, Copy, Debug)]
pub struct ParameterSpec {
    pub key: &'static str,
    pub label: &'static str,
    pub units: &'static str,
    pub notable: &'static str,
    pub decimals: u8,
}

impl ParameterSpec {
    pub fn display_units(&self) -> &'static str {
        match self.units {
            "C" => "°C",
            "F" => "°F",
            "C/km" => "°C/km",
            "deg" => "°",
            "m2/s2" => "m²/s²",
            "m3/s3" => "m³/s³",
            other => other,
        }
    }

    pub fn display_label(&self) -> String {
        let units = self.display_units();
        if units.is_empty() {
            self.label.to_owned()
        } else {
            format!("{} ({units})", self.label)
        }
    }
}

const fn p(
    key: &'static str,
    label: &'static str,
    units: &'static str,
    notable: &'static str,
    decimals: u8,
) -> ParameterSpec {
    ParameterSpec {
        key,
        label,
        units,
        notable,
        decimals,
    }
}

pub const PARAMETERS: [ParameterSpec; 59] = [
    p("sbcape", "SBCAPE", "J/kg", "high", 0),
    p("sbcin", "SBCIN", "J/kg", "low", 0),
    p("mlcape", "MLCAPE", "J/kg", "high", 0),
    p("mlcin", "MLCIN", "J/kg", "low", 0),
    p("mucape", "MUCAPE", "J/kg", "high", 0),
    p("mucin", "MUCIN", "J/kg", "low", 0),
    p("mucape_3km", "MUCAPE 0-3 km", "J/kg", "high", 0),
    p("mucape_6km", "MUCAPE 0-6 km", "J/kg", "high", 0),
    p("eff_cape", "Effective CAPE", "J/kg", "high", 0),
    p("eff_cin", "Effective CIN", "J/kg", "low", 0),
    p("dcape", "DCAPE", "J/kg", "high", 0),
    p("downrush_t", "Downrush T", "C", "high", 1),
    p("ml_lcl", "ML LCL", "m AGL", "low", 0),
    p("ml_lfc", "ML LFC", "m AGL", "low", 0),
    p("mu_el", "MU EL", "m AGL", "high", 0),
    p("sb_lcl", "SB LCL", "m AGL", "low", 0),
    p("eff_inflow_base", "Eff inflow base", "hPa", "high", 0),
    p("eff_inflow_top", "Eff inflow top", "hPa", "low", 0),
    p("shear_1km", "0-1 km shear", "kt", "high", 0),
    p("shear_3km", "0-3 km shear", "kt", "high", 0),
    p("shear_6km", "0-6 km shear", "kt", "high", 0),
    p("shear_8km", "0-8 km shear", "kt", "high", 0),
    p("srh_1km", "0-1 km SRH", "m2/s2", "high", 0),
    p("srh_3km", "0-3 km SRH", "m2/s2", "high", 0),
    p("mean_wind_6km", "0-6 km mean wind", "kt", "high", 0),
    p("storm_motion_r", "Bunkers right", "kt", "high", 1),
    p("stp_cin", "STP (CIN)", "", "high", 2),
    p("stp_fixed", "STP (fixed)", "", "high", 2),
    p("scp", "Supercell composite", "", "high", 2),
    p("ship", "SHIP", "", "high", 2),
    p("sig_severe", "Sig severe", "m3/s3", "high", 0),
    p("mmp", "MCS maintenance", "", "high", 2),
    p("wndg", "Wind damage", "", "high", 2),
    p("esp", "Enhanced stretching", "", "high", 2),
    p("sherbe", "SHERBE", "", "high", 2),
    p("esrh", "Effective SRH", "m2/s2", "high", 0),
    p("ebwd", "Effective shear", "kt", "high", 0),
    p("critical_angle", "Critical angle", "deg", "high", 0),
    p("lhp", "Large hail parameter", "", "high", 2),
    p("hpi", "Hail possibility index", "", "high", 2),
    p("peskov", "Peskov index", "", "high", 1),
    p("mcs_index", "MCS index", "", "high", 2),
    p("left_scp", "Left-moving SCP", "", "high", 2),
    p("dcp", "Derecho composite", "", "high", 2),
    p("ehi_1km", "0-1 km EHI", "", "high", 2),
    p("ehi_3km", "0-3 km EHI", "", "high", 2),
    p("vgp", "Vorticity generation", "", "high", 2),
    p("nst", "Non-supercell tornado", "", "high", 2),
    p("pwat", "Precipitable water", "in", "high", 2),
    p("mean_mixr", "Mean mixing ratio", "g/kg", "high", 1),
    p("low_rh", "Low-level RH", "%", "high", 0),
    p("mid_rh", "Mid-level RH", "%", "high", 0),
    p("lapse_3km", "0-3 km lapse rate", "C/km", "high", 1),
    p("lapse_700_500", "700-500 mb lapse rate", "C/km", "high", 1),
    p("k_index", "K index", "", "high", 0),
    p("totals_totals", "Total totals", "", "high", 0),
    p("wbz", "Wet-bulb zero", "m", "high", 0),
    p("conv_t", "Convective temp", "F", "high", 0),
    p("max_t", "Max temp", "F", "high", 0),
];

pub fn parameter(key: &str) -> Option<&'static ParameterSpec> {
    PARAMETERS.iter().find(|p| p.key == key)
}

fn unit_range(units: &str) -> Option<(f64, f64, f64)> {
    match units {
        "J/kg" => Some((-1000.0, 8000.0, 100.0)),
        "kt" => Some((0.0, 200.0, 5.0)),
        "m2/s2" => Some((-500.0, 1500.0, 25.0)),
        "m3/s3" => Some((0.0, 100_000.0, 1000.0)),
        "m" | "m AGL" => Some((0.0, 20_000.0, 100.0)),
        "hPa" => Some((100.0, 1100.0, 10.0)),
        "C" => Some((-60.0, 60.0, 1.0)),
        "F" => Some((-40.0, 130.0, 5.0)),
        "C/km" => Some((-10.0, 15.0, 0.5)),
        "g/kg" => Some((0.0, 30.0, 0.5)),
        "%" => Some((0.0, 100.0, 5.0)),
        "in" => Some((0.0, 4.0, 0.1)),
        "deg" => Some((0.0, 360.0, 5.0)),
        _ => None,
    }
}

fn unitless_range(decimals: u8) -> Option<(f64, f64, f64)> {
    match decimals {
        2 => Some((-5.0, 20.0, 0.25)),
        1 => Some((-20.0, 60.0, 0.5)),
        0 => Some((-50.0, 100.0, 1.0)),
        _ => None,
    }
}

fn round_even(value: f64) -> f64 {
    let floor = value.floor();
    let frac = value - floor;
    if frac < 0.5 {
        floor
    } else if frac > 0.5 {
        floor + 1.0
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    }
}

fn round_decimals(value: f64, decimals: u8) -> f64 {
    if decimals == 0 {
        return round_even(value);
    }
    let scale = 10_f64.powi(i32::from(decimals));
    round_even(value * scale) / scale
}

fn nice_step(step: f64, decimals: u8) -> f64 {
    let smallest = 10_f64.powi(-i32::from(decimals));
    smallest.max(round_decimals(step, decimals))
}

fn derived_suggestion(minimum: f64, maximum: f64, step: f64, notable: &str) -> f64 {
    let extent = if notable.trim().eq_ignore_ascii_case("low") && minimum < 0.0 {
        minimum
    } else {
        maximum
    };
    let snapped = round_even(extent / 8.0 / step) * step;
    snapped.clamp(minimum, maximum)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ThresholdMetricEntry {
    pub key: String,
    pub label: String,
    pub units: String,
    pub display_label: String,
    pub decimals: u8,
    pub minimum: f64,
    pub maximum: f64,
    pub step: f64,
    pub suggested: f64,
    pub guidance: String,
    #[serde(skip)]
    pub derived: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ThresholdMetricsCatalog {
    pub metrics: Vec<ThresholdMetricEntry>,
    pub operators: Vec<String>,
    pub default_metrics: Vec<String>,
}

pub fn control_for(key: &str) -> ThresholdMetricEntry {
    let Some(item) = parameter(key) else {
        let label = key.replace('_', " ").to_ascii_uppercase();
        let entry = ThresholdMetricEntry {
            key: key.to_owned(),
            label: label.clone(),
            units: String::new(),
            display_label: label.clone(),
            decimals: 1,
            minimum: -100_000.0,
            maximum: 100_000.0,
            step: 1.0,
            suggested: 0.0,
            guidance: format!(
                "{label} in unitless values, to 1 decimal place, stepping by 1.0, between -100000.0 and 100000.0."
            ),
            derived: true,
        };
        return entry;
    };
    let bounds = if item.units.is_empty() {
        unitless_range(item.decimals)
    } else {
        unit_range(item.units)
    };
    let derived = bounds.is_none();
    let (minimum, maximum, raw_step) = bounds.unwrap_or((-100_000.0, 100_000.0, 1.0));
    let step = nice_step(raw_step, item.decimals);
    let raw_suggested = match item.key {
        "mlcape" => 1000.0,
        "shear_6km" => 35.0,
        "srh_3km" => 150.0,
        _ => derived_suggestion(minimum, maximum, step, item.notable),
    };
    let suggested = round_decimals(raw_suggested, item.decimals);
    let display_units = item.display_units();
    let units_text = if display_units.is_empty() {
        "unitless values"
    } else {
        display_units
    };
    let plural = if item.decimals == 1 { "" } else { "s" };
    let prec = usize::from(item.decimals);
    let guidance = format!(
        "{} in {units_text}, to {} decimal place{plural}, stepping by {:.prec$}, between {:.prec$} and {:.prec$}.",
        item.label,
        item.decimals,
        step,
        minimum,
        maximum,
        prec = prec,
    );
    ThresholdMetricEntry {
        key: item.key.to_owned(),
        label: item.label.to_owned(),
        units: display_units.to_owned(),
        display_label: item.display_label(),
        decimals: item.decimals,
        minimum,
        maximum,
        step,
        suggested,
        guidance,
        derived,
    }
}

pub fn metrics_catalog() -> ThresholdMetricsCatalog {
    ThresholdMetricsCatalog {
        metrics: PARAMETERS
            .iter()
            .map(|item| control_for(item.key))
            .collect(),
        operators: OPERATOR_ORDER.iter().map(|&op| op.to_owned()).collect(),
        default_metrics: DEFAULT_METRICS.iter().map(|&k| k.to_owned()).collect(),
    }
}

pub fn fraction_text(numerator: isize, denominator: isize, decimals: usize) -> String {
    if denominator <= 0 {
        return "—".to_owned();
    }
    let share = (numerator as f64) / (denominator as f64);
    format!("{numerator}/{denominator} ({:.decimals$}%)", share * 100.0)
}

pub fn condition_summary(
    metric: &str,
    operator: &str,
    value: f64,
    upper: Option<f64>,
    units: &str,
) -> String {
    let (label, display_units, decimals) = if let Some(item) = parameter(metric) {
        (
            item.label.to_owned(),
            item.display_units(),
            usize::from(item.decimals),
        )
    } else {
        (metric.replace('_', " ").to_ascii_uppercase(), units, 1)
    };
    let suffix = if display_units.is_empty() {
        String::new()
    } else {
        format!(" {display_units}")
    };
    let low = format!("{value:.decimals$}");
    if operator == "between" {
        let high = format!("{:.decimals$}", upper.unwrap_or(value));
        return format!("{label} between {low} and {high}{suffix}");
    }
    let symbol = match operator {
        ">=" => "≥",
        "<=" => "≤",
        "==" => "=",
        other => other,
    };
    format!("{label} {symbol} {low}{suffix}")
}

pub fn evaluate_condition(
    operator: &str,
    value: f64,
    upper: Option<f64>,
    candidate: f64,
) -> Option<bool> {
    if !candidate.is_finite() || candidate <= -9998.0 {
        return None;
    }
    match operator {
        ">" => Some(candidate > value),
        ">=" => Some(candidate >= value),
        "<" => Some(candidate < value),
        "<=" => Some(candidate <= value),
        "==" => {
            let tol = (1e-9 * candidate.abs().max(value.abs())).max(1e-9);
            Some((candidate - value).abs() <= tol)
        }
        "between" => Some(candidate >= value && candidate <= upper.unwrap_or(value)),
        _ => None,
    }
}

#[derive(Deserialize)]
pub struct CsvMemberInput {
    pub member: String,
    pub loaded: bool,
    pub usable: bool,
    pub satisfied: Option<bool>,
    pub reason: Option<String>,
    pub state: String,
    pub state_label: String,
    pub values: BTreeMap<String, Option<f64>>,
}

#[derive(Deserialize)]
pub struct CsvResultInput {
    pub definition_name: String,
    pub definition_summary: String,
    pub valid_time: Option<String>,
    pub qualifying_count: usize,
    pub nonqualifying_count: usize,
    pub loaded_unusable_count: usize,
    pub not_loaded_count: usize,
    pub usable_count: usize,
    pub loaded_count: usize,
    pub requested_count: usize,
    pub resolved_count: usize,
    pub not_evaluated_count: usize,
    pub agreement_fraction: Option<f64>,
    pub agreement: String,
    pub requested_coverage: Option<f64>,
    pub coverage: String,
    pub counts_reconcile: bool,
    pub cancelled: bool,
    pub members: Vec<CsvMemberInput>,
}

fn py_bool(value: bool) -> &'static str {
    if value {
        "True"
    } else {
        "False"
    }
}

fn format_py_json_values(values: &BTreeMap<String, Option<f64>>) -> Result<String, String> {
    let mut out = String::from("{");
    for (idx, (k, v)) in values.iter().enumerate() {
        if idx > 0 {
            out.push_str(", ");
        }
        out.push_str(&serde_json::to_string(k).map_err(|e| e.to_string())?);
        out.push_str(": ");
        match v {
            Some(num) => out.push_str(&serde_json::to_string(num).map_err(|e| e.to_string())?),
            None => out.push_str("null"),
        }
    }
    out.push('}');
    Ok(out)
}

pub fn export_csv_text(results: &[CsvResultInput]) -> Result<String, String> {
    let mut writer = csv::WriterBuilder::new()
        .terminator(csv::Terminator::Any(b'\n'))
        .from_writer(Vec::new());
    writer
        .write_record([
            "definition",
            "definition_summary",
            "valid_time",
            "member",
            "loaded",
            "usable",
            "satisfied",
            "reason",
            "state",
            "state_label",
            "qualifying_count",
            "nonqualifying_count",
            "loaded_unusable_count",
            "not_loaded_count",
            "usable_count",
            "loaded_count",
            "requested_count",
            "resolved_count",
            "not_evaluated_count",
            "agreement_fraction",
            "agreement",
            "requested_coverage",
            "coverage",
            "counts_reconcile",
            "cancelled",
            "diagnostic_values",
            "interpretation",
        ])
        .map_err(|e| e.to_string())?;
    for result in results {
        let valid_time = result.valid_time.as_deref().unwrap_or("");
        let agreement_fraction = result
            .agreement_fraction
            .map(|v| v.to_string())
            .unwrap_or_default();
        let requested_coverage = result
            .requested_coverage
            .map(|v| v.to_string())
            .unwrap_or_default();
        for member in &result.members {
            let satisfied = member.satisfied.map(py_bool).unwrap_or("");
            let reason = member.reason.as_deref().unwrap_or("");
            let diag = format_py_json_values(&member.values)?;
            writer
                .write_record([
                    result.definition_name.as_str(),
                    result.definition_summary.as_str(),
                    valid_time,
                    member.member.as_str(),
                    py_bool(member.loaded),
                    py_bool(member.usable),
                    satisfied,
                    reason,
                    member.state.as_str(),
                    member.state_label.as_str(),
                    &result.qualifying_count.to_string(),
                    &result.nonqualifying_count.to_string(),
                    &result.loaded_unusable_count.to_string(),
                    &result.not_loaded_count.to_string(),
                    &result.usable_count.to_string(),
                    &result.loaded_count.to_string(),
                    &result.requested_count.to_string(),
                    &result.resolved_count.to_string(),
                    &result.not_evaluated_count.to_string(),
                    agreement_fraction.as_str(),
                    result.agreement.as_str(),
                    requested_coverage.as_str(),
                    result.coverage.as_str(),
                    py_bool(result.counts_reconcile),
                    py_bool(result.cancelled),
                    diag.as_str(),
                    DISCLAIMER,
                ])
                .map_err(|e| e.to_string())?;
        }
    }
    let bytes = writer.into_inner().map_err(|e| e.to_string())?;
    String::from_utf8(bytes).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_matches_contract_fixture_exactly() {
        let expected: serde_json::Value = serde_json::from_str(include_str!(
            "../../../src-tauri/contracts/bridge/soundings/threshold-metrics.result.json"
        ))
        .unwrap();
        let actual = serde_json::to_value(metrics_catalog()).unwrap();
        assert_eq!(actual, expected);
    }

    #[test]
    fn condition_summary_and_fraction_match_contract() {
        assert_eq!(
            condition_summary("mlcape", ">=", 2350.0, None, "J/kg"),
            "MLCAPE ≥ 2350 J/kg"
        );
        assert_eq!(
            condition_summary("shear_6km", "between", 20.0, Some(60.0), "kt"),
            "0-6 km shear between 20 and 60 kt"
        );
        assert_eq!(fraction_text(1, 3, 0), "1/3 (33%)");
        assert_eq!(fraction_text(0, 0, 0), "—");
    }
}
