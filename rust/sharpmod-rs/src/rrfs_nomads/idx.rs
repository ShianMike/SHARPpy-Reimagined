use crate::model_planning::{
    build_noaa_search_str, choose_noaa_fields_from_names, NOAA_SURFACE_SEARCH,
};
use crate::rrfs_nomads::rrfs_unavailable;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyTuple};
use std::collections::{BTreeMap, BTreeSet};

const UNUSABLE_PRESSURE_FIELDS: [&str; 1] = ["DZDT"];

const NOAA_SURFACE_PAIRS: [(&str, &str); 8] = [
    ("PRES", "surface"),
    ("HGT", "surface"),
    ("TMP", "2 m above ground"),
    ("DPT", "2 m above ground"),
    ("RH", "2 m above ground"),
    ("SPFH", "2 m above ground"),
    ("UGRD", "10 m above ground"),
    ("VGRD", "10 m above ground"),
];

const CFS_SURFACE_SEARCH: &str = concat!(
    ":(?:PRES|HGT):surface:",
    "|:(?:TMP|SPFH):2 m above ground:",
    "|:(?:UGRD|VGRD):10 m above ground:",
);

const CFS_SURFACE_PAIRS: [(&str, &str); 6] = [
    ("PRES", "surface"),
    ("HGT", "surface"),
    ("TMP", "2 m above ground"),
    ("SPFH", "2 m above ground"),
    ("UGRD", "10 m above ground"),
    ("VGRD", "10 m above ground"),
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct IdxRecordRow {
    pub(crate) number: i64,
    pub(crate) start: u64,
    pub(crate) end: u64,
    pub(crate) variable: String,
    pub(crate) level: String,
    pub(crate) forecast: String,
    pub(crate) line: String,
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum IdxParseError {
    NoRecords,
    UnusableLength,
    UnusableRecordRange(i64),
}

impl IdxParseError {
    fn message(&self) -> String {
        match self {
            Self::NoRecords => "RRFS inventory published no records".to_string(),
            Self::UnusableLength => "RRFS object published no usable length".to_string(),
            Self::UnusableRecordRange(number) => {
                format!("RRFS inventory record {number} has an unusable byte range")
            }
        }
    }
}

pub(crate) fn parse_idx_rows(
    text: &str,
    total_size: i64,
) -> Result<Vec<IdxRecordRow>, IdxParseError> {
    let mut rows: Vec<(u64, i64, String, String, String, String)> = Vec::new();
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        let Some((num_str, rest_after_num)) = line.split_once(':') else {
            continue;
        };
        if num_str.is_empty() || !num_str.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let Some((start_str, _)) = rest_after_num.split_once(':') else {
            continue;
        };
        if start_str.is_empty() || !start_str.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let Ok(number) = num_str.parse::<i64>() else {
            continue;
        };
        let Ok(start) = start_str.parse::<u64>() else {
            continue;
        };
        let mut parts = line.split(':');
        let variable = parts.nth(3).unwrap_or("").to_string();
        let level = parts.next().unwrap_or("").to_string();
        let forecast = parts.next().unwrap_or("").to_string();
        rows.push((start, number, variable, level, forecast, line.to_string()));
    }

    if rows.is_empty() {
        return Err(IdxParseError::NoRecords);
    }
    if total_size <= 0 {
        return Err(IdxParseError::UnusableLength);
    }
    let total = total_size as u64;

    rows.sort();
    let starts: Vec<u64> = rows
        .iter()
        .map(|row| row.0)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let mut following: BTreeMap<u64, u64> = BTreeMap::new();
    for (index, &start) in starts.iter().enumerate() {
        let next = starts.get(index + 1).copied().unwrap_or(total);
        following.insert(start, next);
    }

    let mut records = Vec::with_capacity(rows.len());
    for (start, number, variable, level, forecast, line) in rows {
        let next_start = following[&start];
        if next_start == 0 {
            return Err(IdxParseError::UnusableRecordRange(number));
        }
        let end = next_start - 1;
        if end < start || end >= total {
            return Err(IdxParseError::UnusableRecordRange(number));
        }
        records.push(IdxRecordRow {
            number,
            start,
            end,
            variable,
            level,
            forecast,
            line,
        });
    }
    Ok(records)
}

enum CompiledSearch {
    NoaaPressureAndSurface {
        vars: Vec<String>,
        include_surface: bool,
    },
    SurfacePairs(&'static [(&'static str, &'static str)]),
    ExactToken(String),
}

fn unescape_simple_name(token: &str) -> Option<String> {
    let mut out = String::with_capacity(token.len());
    let mut chars = token.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            let next = chars.next()?;
            out.push(next);
        } else if matches!(
            ch,
            '(' | ')' | '[' | ']' | '{' | '}' | '?' | '*' | '+' | '|' | '^' | '$' | '.'
        ) {
            return None;
        } else {
            out.push(ch);
        }
    }
    (!out.is_empty()).then_some(out)
}

fn compile_known_search(pattern: &str) -> Option<CompiledSearch> {
    if pattern == NOAA_SURFACE_SEARCH {
        return Some(CompiledSearch::SurfacePairs(&NOAA_SURFACE_PAIRS));
    }
    if pattern == CFS_SURFACE_SEARCH {
        return Some(CompiledSearch::SurfacePairs(&CFS_SURFACE_PAIRS));
    }
    if pattern == ":HGT:surface:" || pattern == ":z:sfc:" {
        return Some(CompiledSearch::ExactToken(pattern.to_string()));
    }
    let suffix = format!(r"):\d+(?:\.\d+)? mb:)|(?:{NOAA_SURFACE_SEARCH})");
    if let Some(middle) = pattern
        .strip_prefix("(?:")
        .and_then(|rest| rest.strip_suffix(&suffix))
        .and_then(|inner| inner.strip_prefix(":(?:"))
    {
        let mut vars = Vec::new();
        for raw in middle.split('|') {
            vars.push(unescape_simple_name(raw)?);
        }
        return Some(CompiledSearch::NoaaPressureAndSurface {
            vars,
            include_surface: true,
        });
    }
    None
}

fn line_matches_mb_vars(line: &str, vars: &[String]) -> bool {
    for (idx, ch) in line.char_indices() {
        if ch != ':' {
            continue;
        }
        let after_colon = &line[idx + 1..];
        for var in vars {
            let Some(after_var) = after_colon.strip_prefix(var.as_str()) else {
                continue;
            };
            let Some(after_var_colon) = after_var.strip_prefix(':') else {
                continue;
            };
            let bytes = after_var_colon.as_bytes();
            let mut pos = 0;
            while pos < bytes.len() && bytes[pos].is_ascii_digit() {
                pos += 1;
            }
            if pos == 0 {
                continue;
            }
            if pos < bytes.len() && bytes[pos] == b'.' {
                pos += 1;
                let frac_start = pos;
                while pos < bytes.len() && bytes[pos].is_ascii_digit() {
                    pos += 1;
                }
                if pos == frac_start {
                    continue;
                }
            }
            if after_var_colon[pos..].starts_with(" mb:") {
                return true;
            }
        }
    }
    false
}

fn line_matches_surface_pairs(line: &str, pairs: &[(&str, &str)]) -> bool {
    pairs
        .iter()
        .any(|(var, level)| line.contains(&format!(":{var}:{level}:")))
}

pub(crate) fn match_known_search(pattern: &str, lines: &[&str]) -> Option<Vec<usize>> {
    let compiled = compile_known_search(pattern)?;
    let mut matched = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        let is_match = match &compiled {
            CompiledSearch::NoaaPressureAndSurface {
                vars,
                include_surface,
            } => {
                line_matches_mb_vars(line, vars)
                    || (*include_surface && line_matches_surface_pairs(line, &NOAA_SURFACE_PAIRS))
            }
            CompiledSearch::SurfacePairs(pairs) => line_matches_surface_pairs(line, pairs),
            CompiledSearch::ExactToken(token) => line.contains(token.as_str()),
        };
        if is_match {
            matched.push(idx);
        }
    }
    Some(matched)
}

type PyIdxRecordRow = (i64, u64, u64, String, String, String, String);

#[pyfunction]
pub(super) fn rrfs_parse_idx(
    py: Python<'_>,
    text: Option<&str>,
    total_size: Option<&Bound<'_, PyAny>>,
) -> PyResult<Vec<PyIdxRecordRow>> {
    let total = match total_size {
        None => 0i64,
        Some(obj) => {
            if obj.is_none() {
                0i64
            } else if obj.is_instance_of::<PyBool>() {
                if obj.extract::<bool>()? {
                    1
                } else {
                    0
                }
            } else {
                obj.extract::<i64>()?
            }
        }
    };
    let records = parse_idx_rows(text.unwrap_or(""), total)
        .map_err(|err| rrfs_unavailable(py, err.message()))?;
    Ok(records
        .into_iter()
        .map(|r| {
            (
                r.number, r.start, r.end, r.variable, r.level, r.forecast, r.line,
            )
        })
        .collect())
}

#[pyfunction]
pub(super) fn rrfs_select_records(
    py: Python<'_>,
    records: &Bound<'_, PyAny>,
    pattern: &str,
) -> PyResult<Vec<usize>> {
    let mut lines = Vec::new();
    for item in records.try_iter()? {
        let item = item?;
        let line: String = item.getattr("line")?.extract()?;
        lines.push(line);
    }
    let line_refs: Vec<&str> = lines.iter().map(String::as_str).collect();
    if let Some(indices) = match_known_search(pattern, &line_refs) {
        return Ok(indices);
    }

    let re_mod = py.import("re")?;
    let compiled = re_mod.call_method1("compile", (pattern,))?;
    let mut matched = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        let hit = compiled.call_method1("search", (line.as_str(),))?;
        if !hit.is_none() {
            matched.push(idx);
        }
    }
    Ok(matched)
}

#[pyfunction]
pub(super) fn rrfs_pressure_field_plan<'py>(
    py: Python<'py>,
    records: &Bound<'py, PyAny>,
) -> PyResult<(String, Bound<'py, PyTuple>)> {
    let mut mb_vars = Vec::new();
    for item in records.try_iter()? {
        let item = item?;
        let level: String = item.getattr("level")?.extract()?;
        if level.ends_with(" mb") {
            let variable: String = item.getattr("variable")?.extract()?;
            mb_vars.push(variable);
        }
    }
    let chosen = choose_noaa_fields_from_names(mb_vars).map_err(PyValueError::new_err)?;
    let filtered: Vec<String> = chosen
        .into_iter()
        .filter(|name| !UNUSABLE_PRESSURE_FIELDS.contains(&name.to_uppercase().as_str()))
        .collect();
    let search = build_noaa_search_str(&filtered);
    let tuple_fields = PyTuple::new(py, filtered)?;
    Ok((search, tuple_fields))
}
