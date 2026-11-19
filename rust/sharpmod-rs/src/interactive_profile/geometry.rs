use pyo3::{
    prelude::*,
    types::{PyDict, PyList},
};

struct Plot<'py> {
    widget: Bound<'py, PyAny>,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    root_width: f64,
    root_height: f64,
}
impl<'py> Plot<'py> {
    fn new(
        widget: Bound<'py, PyAny>,
        root: &Bound<'py, PyAny>,
        api: &Bound<'py, PyModule>,
        dimensions: bool,
    ) -> PyResult<Self> {
        let (x, y) = api.call_method1("_origin", (&widget, root))?.extract()?;
        Ok(Self {
            width: if dimensions {
                widget.call_method0("width")?.extract()?
            } else {
                0.
            },
            height: if dimensions {
                widget.call_method0("height")?.extract()?
            } else {
                0.
            },
            root_width: root.call_method0("width")?.extract()?,
            root_height: root.call_method0("height")?.extract()?,
            widget,
            x,
            y,
        })
    }
    fn position(&self, x: f64, y: f64) -> (f64, f64) {
        (
            (self.x + x) / self.root_width,
            (self.y + y) / self.root_height,
        )
    }
    fn contains(&self, x: f64, y: f64) -> bool {
        (0. ..=self.width).contains(&x) && (0. ..=self.height).contains(&y)
    }
}

#[pyfunction]
fn sounding_interactive_geometry<'py>(
    py: Python<'py>,
    sw: &Bound<'py, PyAny>,
    inspection: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let api = PyModule::import(py, "sharpmod.rendering.interactive")?;
    let number = PyModule::import(py, "sharpmod.bridge.params")?.getattr("num")?;
    let profile = sw.getattr("default_prof")?;
    let sound = Plot::new(sw.getattr("sound")?, sw, &api, !inspection)?;
    let hodo = Plot::new(sw.getattr("hodo")?, sw, &api, true)?;
    let pmin: f64 = sound.widget.getattr("pmin")?.extract()?;
    let pmax: f64 = sound.widget.getattr("pmax")?.extract()?;
    let scale: f64 = sound.widget.getattr("scale")?.extract()?;
    let originx: f64 = if inspection {
        0.
    } else {
        sound.widget.getattr("originx")?.extract()?
    };
    let originy: f64 = sound.widget.getattr("originy")?.extract()?;
    let values = PyList::empty(py);
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let pressure = crate::profile_input::column(&prepare, &profile.getattr("pres")?, "pres")?;
    let fields = ["hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v"]
        .map(|name| crate::profile_input::column(&prepare, &profile.getattr(name)?, name))
        .into_iter()
        .collect::<PyResult<Vec<_>>>()?;
    if fields.iter().any(|values| values.len() != pressure.len()) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "interactive sounding column lengths disagree",
        ));
    }
    let surface: f64 = if inspection {
        0.
    } else {
        *fields[0]
            .get(profile.getattr("sfc")?.extract::<usize>()?)
            .ok_or_else(|| {
                pyo3::exceptions::PyIndexError::new_err(
                    "sounding surface index is outside the profile",
                )
            })?
    };
    let (left, top) = sound.position(
        sound.widget.getattr("lpad")?.extract()?,
        sound.widget.getattr("tpad")?.extract()?,
    );
    let (right, bottom) = sound.position(
        sound.widget.getattr("brx")?.extract()?,
        sound.widget.getattr("bry")?.extract()?,
    );
    let add = |id: String,
               label: String,
               plot: &Plot<'py>,
               x: f64,
               y: f64,
               data: Bound<'py, PyList>|
     -> PyResult<()> {
        if !plot.contains(x, y) {
            return Ok(());
        }
        let (x, y) = plot.position(x, y);
        if !(0. ..=1.).contains(&x) || !(0. ..=1.).contains(&y) {
            return Ok(());
        }
        let item = PyDict::new(py);
        item.set_item("id", id)?;
        item.set_item("label", label)?;
        item.set_item("x", x)?;
        item.set_item("y", y)?;
        item.set_item("values", data)?;
        values.append(item)
    };
    for (index, &p) in pressure.iter().enumerate() {
        if !p.is_finite() {
            continue;
        }
        let data = fields
            .iter()
            .map(|column| column[index].is_finite().then_some(column[index]))
            .collect::<Vec<_>>();
        let in_pressure = (pmin..=pmax).contains(&p);
        let y = if in_pressure {
            originy
                + sound
                    .widget
                    .call_method1("pres_to_pix", (p,))?
                    .extract::<f64>()?
                    / scale
        } else {
            f64::NAN
        };
        if inspection {
            if !in_pressure {
                continue;
            }
            let y = sound.position(0., y).1;
            if !(top..=bottom).contains(&y) {
                continue;
            }
            let level = PyDict::new(py);
            level.set_item("index", index)?;
            level.set_item("pressure", p)?;
            level.set_item("y", y)?;
            for (key, value) in ["height", "tmpc", "dwpc", "windDirection", "windSpeed"]
                .into_iter()
                .zip(&data)
            {
                level.set_item(key, value)?;
            }
            level.set_item("hodograph", py.None())?;
            if data[5].is_some() && data[6].is_some() {
                let (x, wy) = hodo
                    .widget
                    .call_method1("uv_to_pix", (&data[5], &data[6]))?
                    .extract::<(f64, f64)>()?;
                if hodo.contains(x, wy) {
                    let (x, y) = hodo.position(x, wy);
                    let wind = PyDict::new(py);
                    wind.set_item("x", x)?;
                    wind.set_item("y", y)?;
                    level.set_item("hodograph", wind)?;
                }
            }
            values.append(level)?;
        } else {
            for (offset, key, label) in [(1, "tmpc", "Temperature"), (2, "dwpc", "Dewpoint")] {
                if data[offset].is_none() || !in_pressure {
                    continue;
                }
                let x = originx
                    + sound
                        .widget
                        .call_method1("tmpc_to_pix", (&data[offset], p))?
                        .extract::<f64>()?
                        / scale;
                let entry = PyList::new(
                    py,
                    [api.call_method1("_field", (key, label, &data[offset], "°C"))?],
                )?;
                // Python's format implements its existing tie-to-even label spelling.
                let stamp = py
                    .get_type::<pyo3::types::PyFloat>()
                    .call1((p,))?
                    .call_method1("__format__", (".0f",))?;
                add(
                    format!("{key}:{index}"),
                    format!("{label} · {stamp} hPa"),
                    &sound,
                    x,
                    y,
                    entry,
                )?;
            }
            if data[0].is_some()
                && data[5].is_some()
                && data[6].is_some()
                && data[0].unwrap() - surface <= 12000.
            {
                let (x, y) = hodo
                    .widget
                    .call_method1("uv_to_pix", (&data[5], &data[6]))?
                    .extract()?;
                let entry = PyList::new(
                    py,
                    [
                        api.call_method1("_field", ("u", "U wind", &data[5], "kt"))?,
                        api.call_method1("_field", ("v", "V wind", &data[6], "kt"))?,
                    ],
                )?;
                let stamp = py
                    .get_type::<pyo3::types::PyFloat>()
                    .call1((p,))?
                    .call_method1("__format__", (".0f",))?;
                add(
                    format!("wind:{index}"),
                    format!("Wind · {stamp} hPa"),
                    &hodo,
                    x,
                    y,
                    entry,
                )?;
            }
        }
    }
    if !inspection {
        let storm = hodo.widget.getattr("srwind")?;
        for (index, label, offset) in [(-1, "Right mover", 0), (-2, "Left mover", 2)] {
            let u = number.call1((storm.get_item(offset)?,))?;
            let v = number.call1((storm.get_item(offset + 1)?,))?;
            if u.is_none() || v.is_none() {
                continue;
            }
            let (x, y) = hodo.widget.call_method1("uv_to_pix", (&u, &v))?.extract()?;
            let entry = PyList::new(
                py,
                [
                    api.call_method1("_field", ("u", "U wind", u, "kt"))?,
                    api.call_method1("_field", ("v", "V wind", v, "kt"))?,
                ],
            )?;
            add(format!("storm:{index}"), label.into(), &hodo, x, y, entry)?;
        }
        return Ok(values.into_any());
    }
    let mut ordered = values
        .iter()
        .map(|value| Ok((value.get_item("y")?.extract::<f64>()?, value)))
        .collect::<PyResult<Vec<_>>>()?;
    ordered.sort_by(|a, b| b.0.total_cmp(&a.0));
    let bounds = PyDict::new(py);
    for (key, value) in [
        ("left", left),
        ("top", top),
        ("right", right),
        ("bottom", bottom),
    ] {
        bounds.set_item(key, value)?;
    }
    let result = PyDict::new(py);
    result.set_item("bounds", bounds)?;
    result.set_item(
        "levels",
        PyList::new(py, ordered.into_iter().map(|(_, value)| value))?,
    )?;
    Ok(result.into_any())
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sounding_interactive_geometry, module)?)
}
