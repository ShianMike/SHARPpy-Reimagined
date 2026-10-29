//! Locator line projection and reuse of immutable Qt paths across paint passes.

use std::collections::VecDeque;

use pyo3::{exceptions::PyValueError, prelude::*, types::PyTuple};

const MAX_ENTRIES: usize = 32;
const MAX_POINTS: usize = 262_144;

#[derive(Clone, Copy, PartialEq)]
struct Projection {
    rect: [f64; 4],
    bounds: [f64; 4],
}

impl Projection {
    fn validate(self) -> Result<Self, &'static str> {
        if !self.rect.into_iter().chain(self.bounds).all(f64::is_finite)
            || self.bounds[0] >= self.bounds[2]
            || self.bounds[1] >= self.bounds[3]
        {
            return Err("locator requires finite geometry and increasing bounds");
        }
        Ok(self)
    }

    fn point(self, lon: f64, lat: f64) -> (f64, f64) {
        let [left, top, width, height] = self.rect;
        let [west, south, east, north] = self.bounds;
        (
            left + (lon - west) / (east - west) * width,
            top + (north - lat) / (north - south) * height,
        )
    }
}

struct Entry {
    projection: Projection,
    lines: Py<PyTuple>,
    constructor: Py<PyAny>,
    paths: Vec<Py<PyAny>>,
    points: usize,
}

#[pyclass]
#[derive(Default)]
struct LocatorPaths {
    entries: VecDeque<Entry>,
    points: usize,
}

#[pyfunction(pass_module)]
#[allow(clippy::too_many_arguments)] // Keep the existing locator painter boundary.
fn draw_locator_lines(
    module: &Bound<'_, PyModule>,
    painter: &Bound<'_, PyAny>,
    lines: &Bound<'_, PyTuple>,
    color: &str,
    width: f64,
    rect: &Bound<'_, PyAny>,
    bounds: [f64; 4],
    qtcore: &Bound<'_, PyAny>,
    qtgui: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let py = module.py();
    let projection = Projection {
        rect: [
            rect.call_method0("left")?.extract()?,
            rect.call_method0("top")?.extract()?,
            rect.call_method0("width")?.extract()?,
            rect.call_method0("height")?.extract()?,
        ],
        bounds,
    }
    .validate()
    .map_err(PyValueError::new_err)?;
    let pen = qtgui
        .getattr("QPen")?
        .call1((qtgui.getattr("QColor")?.call1((color,))?, width))?;
    pen.call_method1("setCosmetic", (true,))?;
    painter.call_method1("setPen", (pen,))?;
    painter.call_method1("setBrush", (qtcore.getattr("Qt")?.getattr("NoBrush")?,))?;
    let constructor = qtgui.getattr("QPainterPath")?;
    let owner = module.getattr("_locator_paths")?;
    let mut cache = owner.cast::<LocatorPaths>()?.borrow_mut();
    let mut hit = None;
    for (index, entry) in cache.entries.iter().enumerate() {
        if entry.projection == projection
            && entry.constructor.bind(py).is(&constructor)
            && entry.lines.bind(py).eq(lines)?
        {
            hit = Some(index);
            break;
        }
    }
    let entry = if let Some(index) = hit {
        let entry = cache.entries.remove(index).unwrap();
        cache.points -= entry.points;
        entry
    } else {
        let mut paths = Vec::with_capacity(lines.len());
        let mut points = 0;
        for line in lines.iter() {
            let path = constructor.call0()?;
            let move_to = path.getattr("moveTo")?;
            let line_to = path.getattr("lineTo")?;
            for (index, point) in line.try_iter()?.enumerate() {
                let (lon, lat): (f64, f64) = point?.extract()?;
                let position = projection.point(lon, lat);
                if index == 0 {
                    move_to.call1(position)?;
                } else {
                    line_to.call1(position)?;
                }
                points += 1;
            }
            paths.push(path.unbind());
        }
        Entry {
            projection,
            lines: lines.clone().unbind(),
            constructor: constructor.unbind(),
            paths,
            points,
        }
    };
    let draw = painter.getattr("drawPath")?;
    for path in &entry.paths {
        draw.call1((path.bind(py),))?;
    }
    if entry.points <= MAX_POINTS {
        while cache.entries.len() >= MAX_ENTRIES || cache.points + entry.points > MAX_POINTS {
            let oldest = cache.entries.pop_front().unwrap();
            cache.points -= oldest.points;
        }
        cache.points += entry.points;
        cache.entries.push_back(entry);
    }
    Ok(())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "_locator_paths",
        Py::new(module.py(), LocatorPaths::default())?,
    )?;
    module.add_function(wrap_pyfunction!(draw_locator_lines, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::Projection;

    #[test]
    fn coordinates_keep_pixel_centers_and_wrapped_longitudes() {
        let projection = Projection {
            rect: [5.5, 7.25, 240., 150.],
            bounds: [170., -20., 190., 40.],
        }
        .validate()
        .unwrap();
        assert_eq!(projection.point(170., 40.), (5.5, 7.25));
        assert_eq!(projection.point(190., -20.), (245.5, 157.25));
        assert_eq!(projection.point(180., 10.), (125.5, 82.25));
        // Paths remain unclipped; the caller's painter owns the clipping region.
        assert_eq!(projection.point(160., 70.), (-114.5, -67.75));
    }

    #[test]
    fn degenerate_or_nonfinite_geometry_is_rejected() {
        let valid = Projection {
            rect: [0., 0., 100., 100.],
            bounds: [-180., -90., 180., 90.],
        };
        for bounds in [[0., 0., 0., 1.], [0., 1., 1., 0.], [f64::NAN, 0., 1., 1.]] {
            assert!(Projection { bounds, ..valid }.validate().is_err());
        }
        assert!(Projection {
            rect: [0., 0., f64::INFINITY, 100.],
            ..valid
        }
        .validate()
        .is_err());
    }
}
