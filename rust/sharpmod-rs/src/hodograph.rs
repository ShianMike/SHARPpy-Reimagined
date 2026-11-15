//! Hodograph background ring configuration, shared by both sounding views.

use pyo3::{exceptions::PyValueError, prelude::*};

const RING_LIMIT_KNOTS: u16 = 200;
const RING_LIMIT_METERS_PER_SECOND: u16 = 105;

fn rings(increment: u16, metric: bool) -> Result<Vec<u16>, &'static str> {
    if increment == 0 {
        return Err("hodograph ring increment must be positive");
    }
    // 105 m/s includes the first 5 m/s ring past 200 knots (102.9 m/s).
    let limit = if metric {
        RING_LIMIT_METERS_PER_SECOND
    } else {
        RING_LIMIT_KNOTS
    };
    Ok((1..=limit / increment).map(|i| i * increment).collect())
}

#[pyfunction]
fn configure_hodograph_rings(widget: &Bound<'_, PyAny>) -> PyResult<()> {
    let increment = widget.getattr("ring_increment")?.extract()?;
    let metric = widget.getattr("wind_units")?.extract::<String>()? == "m/s";
    let values = rings(increment, metric).map_err(PyValueError::new_err)?;
    widget.setattr("rings", values)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(configure_hodograph_rings, module)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rings_reach_200_knots_and_keep_the_existing_spacing() {
        assert_eq!(
            rings(10, false).unwrap(),
            vec![
                10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180,
                190, 200,
            ]
        );
        let metric = rings(5, true).unwrap();
        assert_eq!(metric.len(), 21);
        assert_eq!(metric.first(), Some(&5));
        assert_eq!(metric.last(), Some(&105));
        assert!(metric.windows(2).all(|pair| pair[1] - pair[0] == 5));
        assert!(f64::from(*metric.last().unwrap()) >= 200. * 0.514444);
        assert!(rings(0, false).is_err());
    }

    #[test]
    fn configuring_rings_leaves_zoom_and_center_unchanged() {
        Python::initialize();
        Python::attach(|py| -> PyResult<()> {
            let widget = PyModule::import(py, "types")?
                .getattr("SimpleNamespace")?
                .call0()?;
            widget.setattr("ring_increment", 10)?;
            widget.setattr("wind_units", "knots")?;
            widget.setattr("rings", vec![10, 20])?;
            widget.setattr("hodomag", 160.0)?;
            widget.setattr("scale", 4.5)?;
            widget.setattr("centerx", 100.0)?;
            widget.setattr("centery", 200.0)?;
            configure_hodograph_rings(&widget)?;
            assert_eq!(
                widget.getattr("rings")?.extract::<Vec<u16>>()?,
                rings(10, false).unwrap()
            );
            for (name, expected) in [
                ("hodomag", 160.0),
                ("scale", 4.5),
                ("centerx", 100.0),
                ("centery", 200.0),
            ] {
                assert_eq!(widget.getattr(name)?.extract::<f64>()?, expected);
            }
            widget.setattr("wind_units", "m/s")?;
            widget.setattr("ring_increment", 5)?;
            configure_hodograph_rings(&widget)?;
            assert_eq!(
                widget.getattr("rings")?.extract::<Vec<u16>>()?,
                rings(5, true).unwrap()
            );
            Ok(())
        })
        .unwrap();
    }

    #[test]
    #[ignore = "requires the pinned SHARPpy/Qt environment and an offscreen platform"]
    fn qt_background_reapplies_rings_after_initialization_and_centering() {
        std::env::set_var("QT_QPA_PLATFORM", "offscreen");
        Python::initialize();
        Python::attach(|py| -> PyResult<()> {
            let extension = PyModule::import(py, "sharpmod_rs")?;
            register(&extension)?;
            PyModule::import(py, "sharpmod.render_patches.later_skewt")?
                .call_method0("_install_hodo_label_fit")?;

            let application = PyModule::import(py, "qtpy.QtWidgets")?.getattr("QApplication")?;
            let current = application.call_method0("instance")?;
            let _app = if current.is_none() {
                application.call1((vec!["hodograph-test"],))?
            } else {
                current
            };
            let widget = PyModule::import(py, "sharppy.viz.hodo")?
                .getattr("plotHodo")?
                .call0()?;
            assert_eq!(
                widget.getattr("rings")?.extract::<Vec<u16>>()?,
                rings(10, false).unwrap()
            );
            widget.call_method1("center_hodo", ((35.0, -25.0),))?;
            let before = ["hodomag", "scale", "centerx", "centery"]
                .map(|name| widget.getattr(name)?.extract::<f64>())
                .into_iter()
                .collect::<PyResult<Vec<_>>>()?;
            widget.setattr("rings", vec![10, 20])?;
            widget.call_method0("plotBackground")?;
            assert_eq!(
                widget.getattr("rings")?.extract::<Vec<u16>>()?,
                rings(10, false).unwrap()
            );
            for (index, name) in ["hodomag", "scale", "centerx", "centery"]
                .iter()
                .enumerate()
            {
                assert_eq!(widget.getattr(*name)?.extract::<f64>()?, before[index]);
            }
            widget.call_method0("initUI")?;
            assert_eq!(
                widget.getattr("rings")?.extract::<Vec<u16>>()?,
                rings(10, false).unwrap()
            );
            widget.setattr("wind_units", "m/s")?;
            widget.setattr("ring_increment", 5)?;
            widget.call_method0("plotBackground")?;
            assert_eq!(
                widget.getattr("rings")?.extract::<Vec<u16>>()?,
                rings(5, true).unwrap()
            );
            widget.call_method0("close")?;
            Ok(())
        })
        .unwrap();
    }
}
