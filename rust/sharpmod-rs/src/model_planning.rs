use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyTuple};
use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

const NOAA_REQUIRED_FIELDS: [&str; 4] = ["HGT", "TMP", "UGRD", "VGRD"];
const IFS_REQUIRED_FIELDS: [&str; 4] = ["gh", "t", "u", "v"];
const IFS_SURFACE_FIELDS: [&str; 6] = ["z", "sp", "2t", "2d", "10u", "10v"];

pub(crate) const NOAA_SURFACE_SEARCH: &str = concat!(
    ":(?:PRES|HGT):surface:",
    "|:(?:TMP|DPT|RH|SPFH):2 m above ground:",
    "|:(?:UGRD|VGRD):10 m above ground:",
);

pub(crate) const IFS_SURFACE_SEARCH: &str = ":(?:z|sp|2t|2d|10u|10v):sfc:";

const MAX_RANGE_WORKERS: usize = 8;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub(crate) struct ByteSpan {
    pub(crate) start: u64,
    pub(crate) end: u64,
}

impl ByteSpan {
    pub(crate) fn size(self) -> u64 {
        self.end - self.start + 1
    }
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum TransportPlanError {
    MissingEnd,
    InvalidRange,
    EmptyRanges,
}

impl TransportPlanError {
    fn message(&self) -> &'static str {
        match self {
            Self::MissingEnd => "inventory has no ending byte for a selected GRIB message",
            Self::InvalidRange => "inventory contains an invalid byte range",
            Self::EmptyRanges => "inventory returned no byte ranges for the selected fields",
        }
    }
}

fn transport_error(py: Python<'_>, err: TransportPlanError) -> PyErr {
    let message = err.message();
    py.import("sharpmod.models.model_transport")
        .and_then(|module| module.getattr("OptimizedTransportUnavailable"))
        .and_then(|cls| cls.call1((message,)))
        .map(PyErr::from_value)
        .unwrap_or_else(|_| PyRuntimeError::new_err(message))
}

fn collect_strings(values: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let mut out = Vec::new();
    for item in values.try_iter()? {
        let item = item?;
        if let Ok(s) = item.extract::<String>() {
            out.push(s);
        } else {
            out.push(item.str()?.to_str()?.to_owned());
        }
    }
    Ok(out)
}

fn collect_optional_strings(values: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    if values.is_none() {
        return Ok(Vec::new());
    }
    collect_strings(values)
}

fn escape_regex_literal(input: &str) -> String {
    let mut escaped = String::with_capacity(input.len());
    for ch in input.chars() {
        if matches!(
            ch,
            '(' | ')'
                | '['
                | ']'
                | '{'
                | '}'
                | '?'
                | '*'
                | '+'
                | '-'
                | '|'
                | '^'
                | '$'
                | '\\'
                | '.'
                | '&'
                | '~'
                | '#'
                | ' '
                | '\t'
                | '\n'
                | '\r'
                | '\x0b'
                | '\x0c'
        ) {
            escaped.push('\\');
        }
        escaped.push(ch);
    }
    escaped
}

pub(crate) fn choose_noaa_fields_from_names<I, S>(names: I) -> Result<Vec<String>, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let available: HashSet<String> = names
        .into_iter()
        .map(|s| s.as_ref().to_uppercase())
        .collect();
    let missing: Vec<&str> = NOAA_REQUIRED_FIELDS
        .iter()
        .copied()
        .filter(|name| !available.contains(*name))
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "missing required pressure fields: {}",
            missing.join(", ")
        ));
    }
    let mut fields: Vec<String> = NOAA_REQUIRED_FIELDS
        .iter()
        .map(|s| (*s).to_string())
        .collect();
    if available.contains("RH") {
        fields.push("RH".to_string());
    } else if available.contains("SPFH") {
        fields.push("SPFH".to_string());
    } else {
        return Err("missing required pressure fields: RH or SPFH".to_string());
    }

    if available.contains("VVEL") {
        fields.push("VVEL".to_string());
    } else if available.contains("DZDT") {
        fields.push("DZDT".to_string());
    }
    if available.contains("ABSV") {
        fields.push("ABSV".to_string());
    }
    Ok(fields)
}

pub(crate) fn choose_ifs_fields_from_names<I, S>(names: I) -> Result<Vec<String>, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let available: HashSet<String> = names
        .into_iter()
        .map(|s| s.as_ref().to_lowercase())
        .collect();
    let missing: Vec<&str> = IFS_REQUIRED_FIELDS
        .iter()
        .copied()
        .filter(|name| !available.contains(*name))
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "missing required pressure fields: {}",
            missing.join(", ")
        ));
    }
    let mut fields: Vec<String> = IFS_REQUIRED_FIELDS
        .iter()
        .map(|s| (*s).to_string())
        .collect();
    if available.contains("r") {
        fields.push("r".to_string());
    } else if available.contains("q") {
        fields.push("q".to_string());
    } else {
        return Err("missing required pressure fields: r or q".to_string());
    }
    for optional in ["w", "vo"] {
        if available.contains(optional) {
            fields.push(optional.to_string());
        }
    }
    Ok(fields)
}

pub(crate) fn build_noaa_search_str(fields: &[String]) -> String {
    let names = fields
        .iter()
        .map(|field| escape_regex_literal(&field.to_uppercase()))
        .collect::<Vec<_>>()
        .join("|");
    let pressure = format!(r":(?:{names}):\d+(?:\.\d+)? mb:");
    format!("(?:{pressure})|(?:{NOAA_SURFACE_SEARCH})")
}

pub(crate) fn build_ifs_search_str(fields: &[String]) -> String {
    let names = fields
        .iter()
        .map(|field| escape_regex_literal(&field.to_lowercase()))
        .collect::<Vec<_>>()
        .join("|");
    let pressure = format!(r":(?:{names}):\d+:pl:");
    format!("(?:{pressure})|(?:{IFS_SURFACE_SEARCH})")
}

pub(crate) fn supports_noaa_surface_merge_names<I, S>(names: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let available: HashSet<String> = names
        .into_iter()
        .map(|s| s.as_ref().to_uppercase())
        .collect();
    let has_required = NOAA_REQUIRED_FIELDS
        .iter()
        .all(|name| available.contains(*name))
        && available.contains("PRES");
    let has_moisture =
        available.contains("DPT") || available.contains("RH") || available.contains("SPFH");
    has_required && has_moisture
}

pub(crate) fn supports_ifs_surface_merge_names<I, S>(names: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let available: HashSet<String> = names
        .into_iter()
        .map(|s| s.as_ref().to_lowercase())
        .collect();
    IFS_REQUIRED_FIELDS
        .iter()
        .chain(IFS_SURFACE_FIELDS.iter())
        .all(|name| available.contains(*name))
}

fn parse_py_i64(value: &Bound<'_, PyAny>, is_end: bool) -> Result<i64, TransportPlanError> {
    if value.is_none() {
        return Err(if is_end {
            TransportPlanError::MissingEnd
        } else {
            TransportPlanError::InvalidRange
        });
    }
    if value.is_instance_of::<PyBool>() {
        return Err(TransportPlanError::InvalidRange);
    }
    if let Ok(int_val) = value.extract::<i64>() {
        return Ok(int_val);
    }
    if let Ok(float_val) = value.extract::<f64>() {
        if float_val.is_nan() {
            return Err(if is_end {
                TransportPlanError::MissingEnd
            } else {
                TransportPlanError::InvalidRange
            });
        }
        if !float_val.is_finite() || float_val < (i64::MIN as f64) || float_val > (i64::MAX as f64)
        {
            return Err(TransportPlanError::InvalidRange);
        }
        return Ok(float_val as i64);
    }
    if let Ok(text) = value.extract::<String>() {
        return text
            .trim()
            .parse::<i64>()
            .map_err(|_| TransportPlanError::InvalidRange);
    }
    Err(TransportPlanError::InvalidRange)
}

fn collapse_and_plan(
    rows: &[(i64, i64)],
    max_gap: i64,
    max_overhead_ratio: f64,
) -> Result<Vec<ByteSpan>, TransportPlanError> {
    let mut grouped: BTreeMap<i64, i64> = BTreeMap::new();
    for &(start, end) in rows {
        if start < 0 {
            return Err(TransportPlanError::InvalidRange);
        }
        grouped
            .entry(start)
            .and_modify(|existing| {
                if end > *existing {
                    *existing = end;
                }
            })
            .or_insert(end);
    }

    let mut exact: Vec<ByteSpan> = Vec::with_capacity(grouped.len());
    for (start, end) in grouped {
        if end < start {
            return Err(TransportPlanError::InvalidRange);
        }
        let item = ByteSpan {
            start: start as u64,
            end: end as u64,
        };
        if let Some(last) = exact.last_mut() {
            if item.start <= last.end.saturating_add(1) {
                last.end = last.end.max(item.end);
                continue;
            }
        }
        exact.push(item);
    }

    if exact.is_empty() {
        return Err(TransportPlanError::EmptyRanges);
    }

    let exact_bytes: u128 = exact.iter().map(|item| u128::from(item.size())).sum();
    let ratio = if max_overhead_ratio.is_finite() {
        max_overhead_ratio
    } else {
        0.0
    };
    let overhead_limit_f = (exact_bytes as f64) * ratio;
    let overhead_limit = if overhead_limit_f <= 0.0 || overhead_limit_f.is_nan() {
        0u128
    } else {
        overhead_limit_f as u128
    };
    let max_gap_u128 = if max_gap < 0 {
        None
    } else {
        Some(max_gap as u128)
    };

    let mut overhead: u128 = 0;
    let mut planned: Vec<ByteSpan> = Vec::with_capacity(exact.len());
    for item in exact {
        if let Some(last) = planned.last_mut() {
            let gap = u128::from(item.start - last.end - 1);
            if max_gap_u128.is_some_and(|limit| gap <= limit) && overhead + gap <= overhead_limit {
                overhead += gap;
                last.end = item.end;
                continue;
            }
        }
        planned.push(item);
    }
    Ok(planned)
}

pub(crate) fn parallelize_spans(
    ranges: Vec<ByteSpan>,
    workers: usize,
    min_part_bytes: u64,
) -> Vec<ByteSpan> {
    if ranges.is_empty() {
        return Vec::new();
    }
    let mut plan = ranges;
    plan.sort();
    let target = workers.clamp(1, MAX_RANGE_WORKERS);
    let minimum = min_part_bytes.max(1);
    let split_threshold = minimum.saturating_mul(2);

    while plan.len() < target {
        let best = plan
            .iter()
            .enumerate()
            .filter(|(_, item)| item.size() >= split_threshold)
            .map(|(index, item)| (item.size(), index))
            .max();
        let Some((_, index)) = best else {
            break;
        };
        let item = plan[index];
        let left_size = item.size() / 2;
        let split_at = item.start + left_size - 1;
        plan[index] = ByteSpan {
            start: item.start,
            end: split_at,
        };
        plan.insert(
            index + 1,
            ByteSpan {
                start: split_at + 1,
                end: item.end,
            },
        );
    }
    plan
}

pub(crate) fn parse_content_range(value: &str) -> Option<(u64, u64, u64)> {
    let (unit, remainder) = value.split_once(' ')?;
    let (span, total_raw) = remainder.split_once('/')?;
    let (start_raw, end_raw) = span.split_once('-')?;
    if !unit.eq_ignore_ascii_case("bytes") || total_raw == "*" {
        return None;
    }
    let start = start_raw.trim().parse::<i64>().ok()?;
    let end = end_raw.trim().parse::<i64>().ok()?;
    let total = total_raw.trim().parse::<i64>().ok()?;
    if start < 0 || end < start || total <= end {
        return None;
    }
    Some((start as u64, end as u64, total as u64))
}

pub(crate) fn valid_grib_path(path: &Path) -> bool {
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let Ok(meta) = file.metadata() else {
        return false;
    };
    if meta.len() < 8 {
        return false;
    }
    let mut head = [0u8; 4];
    if file.read_exact(&mut head).is_err() || &head != b"GRIB" {
        return false;
    }
    if file.seek(SeekFrom::End(-4)).is_err() {
        return false;
    }
    let mut tail = [0u8; 4];
    file.read_exact(&mut tail).is_ok() && &tail == b"7777"
}

#[pyfunction]
fn choose_noaa_fields<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    let names = collect_strings(values)?;
    let fields = choose_noaa_fields_from_names(names).map_err(PyValueError::new_err)?;
    PyTuple::new(py, fields)
}

#[pyfunction]
fn choose_ifs_fields<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    let names = collect_strings(values)?;
    let fields = choose_ifs_fields_from_names(names).map_err(PyValueError::new_err)?;
    PyTuple::new(py, fields)
}

#[pyfunction]
fn build_noaa_search(fields: &Bound<'_, PyAny>) -> PyResult<String> {
    let names = collect_strings(fields)?;
    Ok(build_noaa_search_str(&names))
}

#[pyfunction]
fn build_ifs_search(fields: &Bound<'_, PyAny>) -> PyResult<String> {
    let names = collect_strings(fields)?;
    Ok(build_ifs_search_str(&names))
}

#[pyfunction]
fn supports_noaa_surface_merge(fields: &Bound<'_, PyAny>) -> PyResult<bool> {
    let names = collect_optional_strings(fields)?;
    Ok(supports_noaa_surface_merge_names(names))
}

#[pyfunction]
fn supports_ifs_surface_merge(fields: &Bound<'_, PyAny>) -> PyResult<bool> {
    let names = collect_optional_strings(fields)?;
    Ok(supports_ifs_surface_merge_names(names))
}

#[pyfunction]
#[pyo3(signature = (rows, max_gap = None, max_overhead_ratio = 0.25))]
fn plan_model_ranges(
    py: Python<'_>,
    rows: &Bound<'_, PyAny>,
    max_gap: Option<&Bound<'_, PyAny>>,
    max_overhead_ratio: f64,
) -> PyResult<Vec<(u64, u64)>> {
    let max_gap_val = match max_gap {
        None => 2 * 1024 * 1024,
        Some(obj) if obj.is_none() => 2 * 1024 * 1024,
        Some(obj) => {
            if obj.is_instance_of::<PyBool>() {
                return Err(PyValueError::new_err("max_gap must be an integer"));
            }
            obj.extract::<i64>()?
        }
    };
    let mut parsed_rows = Vec::new();
    for entry in rows.try_iter()? {
        let entry = entry?;
        let mut iter = entry
            .try_iter()
            .map_err(|_| transport_error(py, TransportPlanError::InvalidRange))?;
        let first = iter
            .next()
            .transpose()
            .map_err(|_| transport_error(py, TransportPlanError::InvalidRange))?
            .ok_or_else(|| transport_error(py, TransportPlanError::InvalidRange))?;
        let second = iter
            .next()
            .transpose()
            .map_err(|_| transport_error(py, TransportPlanError::InvalidRange))?
            .ok_or_else(|| transport_error(py, TransportPlanError::InvalidRange))?;
        if iter.next().is_some() {
            return Err(transport_error(py, TransportPlanError::InvalidRange));
        }
        let end = parse_py_i64(&second, true).map_err(|err| transport_error(py, err))?;
        let start = parse_py_i64(&first, false).map_err(|err| transport_error(py, err))?;
        parsed_rows.push((start, end));
    }

    let planned = collapse_and_plan(&parsed_rows, max_gap_val, max_overhead_ratio)
        .map_err(|err| transport_error(py, err))?;
    Ok(planned.into_iter().map(|s| (s.start, s.end)).collect())
}

#[pyfunction]
#[pyo3(signature = (ranges, workers = None, min_part_bytes = None))]
fn parallelize_model_range_plan(
    py: Python<'_>,
    ranges: &Bound<'_, PyAny>,
    workers: Option<&Bound<'_, PyAny>>,
    min_part_bytes: Option<&Bound<'_, PyAny>>,
) -> PyResult<Vec<(u64, u64)>> {
    let worker_count = match workers {
        None => 1usize,
        Some(obj) if obj.is_none() => 1usize,
        Some(obj) => {
            if obj.is_instance_of::<PyBool>() {
                return Err(PyValueError::new_err(
                    "workers and min_part_bytes must be integers",
                ));
            }
            obj.extract::<isize>()?.max(1) as usize
        }
    };
    let minimum = match min_part_bytes {
        None => (2 * 1024 * 1024) as u64,
        Some(obj) if obj.is_none() => (2 * 1024 * 1024) as u64,
        Some(obj) => {
            if obj.is_instance_of::<PyBool>() {
                return Err(PyValueError::new_err(
                    "workers and min_part_bytes must be integers",
                ));
            }
            obj.extract::<i64>()?.max(1) as u64
        }
    };

    let mut spans = Vec::new();
    for entry in ranges.try_iter()? {
        let entry = entry?;
        let (first, second) = if let (Ok(start_attr), Ok(end_attr)) =
            (entry.getattr("start"), entry.getattr("end"))
        {
            (start_attr, end_attr)
        } else {
            let mut iter = entry
                .try_iter()
                .map_err(|_| transport_error(py, TransportPlanError::InvalidRange))?;
            let first = iter
                .next()
                .transpose()
                .map_err(|_| transport_error(py, TransportPlanError::InvalidRange))?
                .ok_or_else(|| transport_error(py, TransportPlanError::InvalidRange))?;
            let second = iter
                .next()
                .transpose()
                .map_err(|_| transport_error(py, TransportPlanError::InvalidRange))?
                .ok_or_else(|| transport_error(py, TransportPlanError::InvalidRange))?;
            if iter.next().is_some() {
                return Err(transport_error(py, TransportPlanError::InvalidRange));
            }
            (first, second)
        };
        let end = parse_py_i64(&second, true).map_err(|err| transport_error(py, err))?;
        let start = parse_py_i64(&first, false).map_err(|err| transport_error(py, err))?;
        if start < 0 || end < start {
            return Err(transport_error(py, TransportPlanError::InvalidRange));
        }
        spans.push(ByteSpan {
            start: start as u64,
            end: end as u64,
        });
    }

    Ok(parallelize_spans(spans, worker_count, minimum)
        .into_iter()
        .map(|s| (s.start, s.end))
        .collect())
}

#[pyfunction]
fn parse_content_range_details(value: &Bound<'_, PyAny>) -> Option<(u64, u64, u64)> {
    let text = value.extract::<String>().ok()?;
    parse_content_range(&text)
}

#[pyfunction]
fn valid_grib_file(path: PathBuf) -> bool {
    valid_grib_path(&path)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(choose_noaa_fields, module)?)?;
    module.add_function(wrap_pyfunction!(choose_ifs_fields, module)?)?;
    module.add_function(wrap_pyfunction!(build_noaa_search, module)?)?;
    module.add_function(wrap_pyfunction!(build_ifs_search, module)?)?;
    module.add_function(wrap_pyfunction!(supports_noaa_surface_merge, module)?)?;
    module.add_function(wrap_pyfunction!(supports_ifs_surface_merge, module)?)?;
    module.add_function(wrap_pyfunction!(plan_model_ranges, module)?)?;
    module.add_function(wrap_pyfunction!(parallelize_model_range_plan, module)?)?;
    module.add_function(wrap_pyfunction!(parse_content_range_details, module)?)?;
    module.add_function(wrap_pyfunction!(valid_grib_file, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn noaa_and_ifs_field_selection() {
        let noaa =
            choose_noaa_fields_from_names(["hgt", "tmp", "ugrd", "vgrd", "spfh", "dzdt", "absv"])
                .expect("valid noaa fields");
        assert_eq!(
            noaa,
            vec!["HGT", "TMP", "UGRD", "VGRD", "SPFH", "DZDT", "ABSV"]
        );
        let ifs = choose_ifs_fields_from_names(["GH", "T", "U", "V", "R", "W"]).expect("valid ifs");
        assert_eq!(ifs, vec!["gh", "t", "u", "v", "r", "w"]);
        assert!(choose_noaa_fields_from_names(["HGT", "TMP", "UGRD"]).is_err());
    }

    #[test]
    fn range_collapsing_handles_duplicate_starts_and_budgets() {
        let merged = collapse_and_plan(&[(100, 99), (100, 199), (200, 299), (400, 499)], 128, 0.5)
            .expect("valid collapsed plan");
        assert_eq!(
            merged,
            vec![ByteSpan {
                start: 100,
                end: 499
            }]
        );

        let split = parallelize_spans(vec![ByteSpan { start: 0, end: 99 }], 4, 25);
        assert_eq!(
            split,
            vec![
                ByteSpan { start: 0, end: 24 },
                ByteSpan { start: 25, end: 49 },
                ByteSpan { start: 50, end: 74 },
                ByteSpan { start: 75, end: 99 },
            ]
        );
    }

    #[test]
    fn content_range_and_valid_grib_checks() {
        assert_eq!(parse_content_range("BYTES 0-0/4096"), Some((0, 0, 4096)));
        assert_eq!(parse_content_range("bytes 0-99/*"), None);
        assert_eq!(parse_content_range("bytes 5-2/100"), None);
        assert_eq!(parse_content_range("bytes 0-100/100"), None);

        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("sharpmod_grib_check_{nanos}.grib2"));
        {
            let mut file = File::create(&path).expect("create temp");
            file.write_all(b"GRIB\x00\x007777").expect("write");
        }
        assert!(valid_grib_path(&path));
        std::fs::write(&path, b"BAD0\x00\x007777").expect("overwrite");
        assert!(!valid_grib_path(&path));
        let _ = std::fs::remove_file(&path);
    }
}
