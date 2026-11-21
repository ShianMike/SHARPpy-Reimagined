//! Validate interactive sounding requests before scheduling a render worker.
use pyo3::{
    prelude::*,
    types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString},
};
use std::collections::HashSet;

fn invalid(py: Python<'_>, message: &str) -> PyErr {
    let message = format!("The request was not valid: {message}.");
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|module| module.getattr("BridgeError"))
        .and_then(|class| class.call1(("INVALID_ARGUMENT", &message, false)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => pyo3::exceptions::PyValueError::new_err(message),
    }
}

fn object<'py>(value: &Bound<'py, PyAny>, name: &str) -> PyResult<Bound<'py, PyDict>> {
    value
        .clone()
        .cast_into::<PyDict>()
        .map_err(|_| invalid(value.py(), &format!("{name} must be an object")))
}

fn keys(params: &Bound<'_, PyDict>, required: &[&str], optional: &[&str]) -> PyResult<()> {
    for key in required {
        if !params.contains(key)? {
            return Err(invalid(params.py(), &format!("missing {key}")));
        }
    }
    for key in params.keys() {
        let key = key
            .extract::<String>()
            .map_err(|_| invalid(params.py(), "object keys must be text"))?;
        if !required.contains(&key.as_str()) && !optional.contains(&key.as_str()) {
            return Err(invalid(params.py(), &format!("unexpected {key}")));
        }
    }
    Ok(())
}

fn integer(value: &Bound<'_, PyAny>, name: &str, low: i64, high: i64) -> PyResult<i64> {
    let error = || {
        invalid(
            value.py(),
            &format!("{name} must be an integer within [{low}, {high}]"),
        )
    };
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(error());
    }
    let number = value.extract::<i64>().map_err(|_| error())?;
    if !(low..=high).contains(&number) {
        return Err(error());
    }
    Ok(number)
}

fn number(value: &Bound<'_, PyAny>, name: &str, low: f64, high: f64) -> PyResult<()> {
    let error = || {
        invalid(
            value.py(),
            &format!("{name} must be a finite number within [{low}, {high}]"),
        )
    };
    if value.is_instance_of::<PyBool>()
        || !(value.is_instance_of::<PyInt>() || value.is_instance_of::<PyFloat>())
    {
        return Err(error());
    }
    let number = value.extract::<f64>().map_err(|_| error())?;
    if !number.is_finite() || !(low..=high).contains(&number) {
        return Err(error());
    }
    Ok(())
}

fn choice(params: &Bound<'_, PyDict>, key: &str, choices: &[&str]) -> PyResult<String> {
    let Some(value) = params.get_item(key)? else {
        return Ok(choices[0].to_string());
    };
    let value = value.extract::<String>().map_err(|_| {
        invalid(
            params.py(),
            &format!("{key} must be one of {}", choices.join(", ")),
        )
    })?;
    if !choices.contains(&value.as_str()) {
        return Err(invalid(
            params.py(),
            &format!("{key} must be one of {}", choices.join(", ")),
        ));
    }
    Ok(value)
}

#[pyfunction]
pub(crate) fn validate_sounding_view<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let input = object(value, "view")?;
    keys(
        &input,
        &["edits"],
        &[
            "chart",
            "panel",
            "drag",
            "timeIndex",
            "mode",
            "animationRange",
            "measureWind",
        ],
    )?;
    let view = PyDict::new(py);
    view.set_item("mode", choice(&input, "mode", &["severe", "winter"])?)?;
    view.set_item(
        "chart",
        choice(&input, "chart", &["streamwiseness", "srw", "theta", "cape"])?,
    )?;
    view.set_item(
        "panel",
        choice(
            &input,
            "panel",
            &[
                "STP STATS",
                "COND STP",
                "WINTER",
                "FIRE",
                "SHIP",
                "VROT",
                "HEIGHT CHARTS",
            ],
        )?,
    )?;
    view.set_item(
        "timeIndex",
        match input.get_item("timeIndex")? {
            Some(value) => integer(&value, "timeIndex", 0, 1_000_000)?,
            None => 0,
        },
    )?;
    let measure_wind = if let Some(value) = input.get_item("measureWind")? {
        value
            .cast::<PyBool>()
            .map_err(|_| invalid(py, "measureWind must be a boolean"))?
            .is_true()
    } else {
        false
    };
    if measure_wind {
        if input.contains("animationRange")?
            || input.get_item("drag")?.is_some_and(|drag| !drag.is_none())
        {
            return Err(invalid(
                py,
                "measureWind cannot be combined with drag or animationRange",
            ));
        }
        view.set_item("measureWind", true)?;
    }
    if let Some(value) = input.get_item("animationRange")? {
        number(&value, "animationRange", 40.0, 500.0)?;
        view.set_item("animationRange", value)?;
    }

    // The schema above guarantees required fields; normalize into new containers
    // so neither rendering nor the caller can change the other's request.
    let edits = input.get_item("edits")?.unwrap();
    let edits = edits
        .cast::<PyList>()
        .map_err(|_| invalid(py, "edits must contain at most 2000 levels"))?;
    if edits.len() > 2000 {
        return Err(invalid(py, "edits must contain at most 2000 levels"));
    }
    let result = PyList::empty(py);
    let mut seen = HashSet::with_capacity(edits.len());
    for edit in edits {
        let edit = object(&edit, "each edit")?;
        keys(&edit, &["index", "values"], &[])?;
        let index = integer(&edit.get_item("index")?.unwrap(), "index", -2, 10000)?;
        if !seen.insert(index) {
            return Err(invalid(py, "each edited level must occur once"));
        }
        let values = object(&edit.get_item("values")?.unwrap(), "edit values")?;
        if values.is_empty() {
            return Err(invalid(py, "edit values must be a nonempty object"));
        }
        if index < 0 {
            keys(&values, &["u", "v"], &[])?;
        } else {
            keys(&values, &[], &["tmpc", "dwpc", "u", "v"])?;
        }
        for (key, value) in &values {
            let field: String = key.extract()?;
            let (low, high) = if matches!(field.as_str(), "tmpc" | "dwpc") {
                (-150.0, 65.0)
            } else {
                (-350.0, 350.0)
            };
            number(&value, &field, low, high)?;
        }
        let normalized = PyDict::new(py);
        normalized.set_item("index", index)?;
        normalized.set_item("values", values.copy()?)?;
        result.append(normalized)?;
    }
    view.set_item("edits", result)?;

    if let Some(drag) = input.get_item("drag")?.filter(|value| !value.is_none()) {
        let drag = object(&drag, "drag")?;
        keys(&drag, &["target", "x", "y"], &[])?;
        let target = drag.get_item("target")?.unwrap();
        let target = target
            .cast::<PyString>()
            .map_err(|_| invalid(py, "target must be text"))?;
        let target = target
            .to_str()
            .map_err(|_| invalid(py, "target must be valid Unicode text"))?
            .trim();
        if !(1..=30).contains(&target.chars().count()) {
            return Err(invalid(py, "target must be text of 1 to 30 characters"));
        }
        let (kind, level) = target
            .split_once(':')
            .ok_or_else(|| invalid(py, "unknown edit target"))?;
        if !matches!(kind, "tmpc" | "dwpc" | "wind" | "storm") {
            return Err(invalid(py, "unknown edit target"));
        }
        let index = level
            .trim()
            .parse::<i64>()
            .map_err(|_| invalid(py, "edit target needs a level"))?;
        let range = if kind == "storm" { -2..=-1 } else { 0..=10000 };
        if !range.contains(&index) {
            return Err(invalid(py, "edit target level is invalid"));
        }
        let x = drag.get_item("x")?.unwrap();
        let y = drag.get_item("y")?.unwrap();
        number(&x, "x", 0.0, 1.0)?;
        number(&y, "y", 0.0, 1.0)?;
        let normalized = PyDict::new(py);
        normalized.set_item("target", format!("{kind}:{index}"))?;
        normalized.set_item("x", x)?;
        normalized.set_item("y", y)?;
        view.set_item("drag", normalized)?;
    }
    Ok(view)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(validate_sounding_view, module)?)
}

#[cfg(test)]
mod tests;
