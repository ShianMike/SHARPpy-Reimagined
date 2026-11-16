//! Locator line projection and reuse of immutable Qt paths across paint passes.

use std::collections::VecDeque;

use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyTuple},
};

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

/// Normalized hodograph inset bounds for composing a captured map into a PNG.
/// Keep this tied to the native widget geometry instead of a fixed image offset.
#[pyfunction]
fn sounding_locator_bounds<'py>(
    py: Python<'py>,
    sw: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let hodo = sw.getattr("hodo")?;
    let point = PyModule::import(py, "qtpy.QtCore")?
        .getattr("QPoint")?
        .call1((0, 0))?;
    let origin = hodo.call_method1("mapTo", (sw, point))?;
    let root_width: f64 = sw.call_method0("width")?.extract()?;
    let root_height: f64 = sw.call_method0("height")?.extract()?;
    let left: f64 = origin.call_method0("x")?.extract()?;
    let top: f64 = origin.call_method0("y")?.extract()?;
    let width: f64 = hodo.call_method0("width")?.extract()?;
    let height: f64 = hodo.call_method0("height")?.extract()?;
    let frame_left: f64 = hodo.getattr("tlx")?.extract()?;
    let frame_top: f64 = hodo.getattr("tly")?.extract()?;
    let Some(inset) = inset_bounds(
        [left, top, width, height],
        [frame_left, frame_top],
        [root_width, root_height],
    ) else {
        return Ok(None);
    };
    let result = PyDict::new(py);
    for (key, value) in ["left", "top", "right", "bottom"].into_iter().zip(inset) {
        result.set_item(key, value)?;
    }
    // The renderer has already resolved the collection's place name.
    let metadata = hodo
        .getattr("prof_collections")?
        .get_item(hodo.getattr("pc_idx")?)?
        .getattr("_meta")?
        .cast_into::<PyDict>()?;
    if let Some(location) = metadata.get_item("loc")? {
        let model = metadata
            .get_item("model")?
            .unwrap_or_else(|| py.None().into_bound(py));
        let needs_town = PyModule::import(py, "sharpmod.maps.place_names")?
            .call_method1("needs_town_name", (&location, model))?
            .is_truthy()?;
        if !needs_town {
            let name = location
                .str()?
                .to_str()?
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            result.set_item("location", name.chars().take(140).collect::<String>())?;
        }
    }
    Ok(Some(result))
}

fn inset_bounds(hodo: [f64; 4], frame: [f64; 2], root: [f64; 2]) -> Option<[f64; 4]> {
    let [left, top, width, height] = hodo;
    let [frame_left, frame_top] = frame.map(|offset| offset + 1.);
    let [root_width, root_height] = root;
    if !hodo
        .into_iter()
        .chain(frame)
        .chain(root)
        .all(f64::is_finite)
        || left < 0.
        || top < 0.
        || frame_left < 0.
        || frame_top < 0.
        || root_width <= 0.
        || root_height <= 0.
        || left + width > root_width
        || top + height > root_height
    {
        return None;
    }
    // Anchor the captured map in the hodograph's framed corner at logical density.
    let inset_width = (width * 0.29)
        .clamp(150., 250.)
        .min(width - frame_left - 8.);
    let inset_height = ((inset_width * 0.64).max(96.) * 1.2).min(height - frame_top - 8.);
    if inset_width < 110. || inset_height < 72. {
        return None;
    }
    Some([
        (left + frame_left) / root_width,
        (top + frame_top) / root_height,
        (left + frame_left + inset_width) / root_width,
        (top + frame_top + inset_height) / root_height,
    ])
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "_locator_paths",
        Py::new(module.py(), LocatorPaths::default())?,
    )?;
    module.add_function(wrap_pyfunction!(draw_locator_lines, module)?)?;
    module.add_function(wrap_pyfunction!(sounding_locator_bounds, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{inset_bounds, Projection};

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

    #[test]
    fn inset_tracks_the_hodograph_instead_of_output_density() {
        let bounds = inset_bounds([1100., 20., 800., 600.], [0., 0.], [2000., 1400.]).unwrap();
        for (actual, expected) in
            bounds
                .into_iter()
                .zip([1101. / 2000., 21. / 1400., 1333. / 2000., 199.176 / 1400.])
        {
            assert!((actual - expected).abs() < 1e-12);
        }
        let resized = inset_bounds([2200., 40., 1600., 1200.], [0., 0.], [4000., 2800.]).unwrap();
        assert!((resized[0] - bounds[0]).abs() < 0.001);
        assert!(resized[2] < 1. && resized[3] < 1.);
    }

    #[test]
    fn inset_rejects_missing_or_out_of_frame_hodographs() {
        assert!(inset_bounds([0., 0., 100., 50.], [0., 0.], [200., 200.]).is_none());
        assert!(inset_bounds([150., 0., 200., 100.], [0., 0.], [200., 200.]).is_none());
        assert!(inset_bounds([f64::NAN, 0., 200., 100.], [0., 0.], [400., 400.]).is_none());
    }
}
