use super::*;
use pyo3::types::{PyCFunction, PyTuple};
use std::fmt::Write;

#[test]
fn animation_axes_reject_invalid_ranges_before_accessing_widgets() {
    Python::initialize();
    Python::attach(|py| {
        for radius in [0.0, 39.9, 500.1, f64::NAN, f64::INFINITY] {
            assert!(
                apply_animation_axes(py.None().bind(py), radius, &PyDict::new(py), "severe")
                    .is_err()
            );
        }
    });
}

#[test]
#[ignore = "requires the pinned offscreen Qt rendering environment"]
fn animation_axes_keep_severe_and_winter_centered_and_targets_aligned() {
    Python::initialize();
    let dir = std::env::temp_dir().join(format!("sharpmod-animation-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("animation.spc");
    let mut source = "%TITLE%\n ANIM 261114/1200\n\n%RAW%\n".to_owned();
    for i in 0..35 {
        let t = 8.0 - i as f64 * 2.1;
        writeln!(
            source,
            "{}, {}, {t}, {}, {}, {}",
            1000 - i * 25,
            i * 330,
            t - 2.,
            180 + i * 4,
            8 + i * 3
        )
        .unwrap();
    }
    source.push_str("%END%\n");
    std::fs::write(&input, source).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let extension = PyModule::import(py, "sharpmod_rs")?;
        let verify = PyCFunction::new_closure(
            py,
            Some(c"verify_animation_axes"),
            None,
            |args: &Bound<'_, PyTuple>, _: Option<&Bound<'_, PyDict>>| -> PyResult<()> {
                let win = args.get_item(0)?;
                let radius: f64 = args.get_item(1)?.extract()?;
                let metadata = args.get_item(2)?.cast_into::<PyDict>()?;
                let mode: String = args.get_item(3)?.extract()?;
                apply_animation_axes(&win, radius, &metadata, &mode)?;
                let hodo = win.getattr("spc_widget")?.getattr("hodo")?;
                let w: f64 = hodo.call_method0("width")?.extract()?;
                let h: f64 = hodo.call_method0("height")?.extract()?;
                let center: (f64, f64) = hodo
                    .call_method1("pix_to_uv", (w / 2., h / 2.))?
                    .extract()?;
                assert!(center.0.abs() < 1e-8 && center.1.abs() < 1e-8, "{center:?}");
                let scale: f64 = hodo.getattr("scale")?.extract()?;
                assert!((scale - w.min(h) / (2. * radius)).abs() < 0.02, "{scale}");
                let interactive = PyModule::import(args.py(), "sharpmod.rendering.interactive")?;
                let expected =
                    interactive.call_method1("inspection", (win.getattr("spc_widget")?,))?;
                assert!(metadata.get_item("inspection")?.unwrap().eq(expected)?);
                Ok(())
            },
        )?;
        extension.setattr("apply_animation_axes", verify)?;
        let cli = PyModule::import(py, "sharpmod.rendering.cli")?;
        for mode in ["severe", "winter"] {
            for radius in [80.0, 140.0] {
                let view = PyDict::new(py);
                view.set_item("edits", PyList::empty(py))?;
                view.set_item("chart", "streamwiseness")?;
                view.set_item("panel", "STP STATS")?;
                view.set_item("mode", mode)?;
                view.set_item("animationRange", radius)?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("view", &view)?;
                kwargs.set_item("view_result", PyDict::new(py))?;
                kwargs.set_item("image_mode", "hd")?;
                kwargs.set_item("show_locator", false)?;
                cli.call_method(
                    "render",
                    (
                        input.to_str().unwrap(),
                        dir.join(format!("{mode}-{radius}.png")).to_str().unwrap(),
                    ),
                    Some(&kwargs),
                )?;
            }
        }
        Ok(())
    })
    .unwrap();
}
