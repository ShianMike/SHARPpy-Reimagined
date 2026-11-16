use pyo3::{prelude::*, types::PyList};

#[test]
#[ignore = "requires the pinned offscreen Qt rendering environment"]
fn fixed_lapse_column_is_minus_thirty_across_sizes_omega_and_inflow_layers() {
    let _lock = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        super::register(&PyModule::import(py, "sharpmod_rs")?)?;
        let application = PyModule::import(py, "qtpy.QtWidgets")?.getattr("QApplication")?;
        let current = application.call_method0("instance")?;
        let app = if current.is_none() {
            application.call1((vec!["lapse-column-test"],))?
        } else {
            current
        };
        let tests = PyModule::import(py, "sharpmod.tests.test_skewt_annotation_layout")?;
        let fixture = tests
            .getattr("skewt")?
            .getattr("__wrapped__")?
            .call1((&app,))?;
        let widget = PyModule::import(py, "builtins")?
            .getattr("next")?
            .call1((&fixture,))?;
        let profile = widget.getattr("prof")?;
        let original_top = profile.getattr("etop")?;
        for (width, height) in [(900, 700), (1200, 850), (700, 560), (560, 460)] {
            tests.call_method1("_sized", (&widget, &app, width, height))?;
            let scale: f64 = widget.getattr("scale")?.extract()?;
            let origin: f64 = widget.getattr("originx")?.extract()?;
            let reference = widget.getattr("pmax")?;
            let expected = origin
                + widget
                    .call_method1("tmpc_to_pix", (-30.0, &reference))?
                    .extract::<f64>()?
                    / scale;
            let sfc_column = origin
                + widget
                    .call_method1("tmpc_to_pix", (-20.0, &reference))?
                    .extract::<f64>()?
                    / scale;
            for omega in [false, true] {
                widget.setattr("plot_omega", omega)?;
                for inflow_top in [
                    &original_top,
                    &profile.getattr("max_lapse_rate_2_6")?.get_item(2)?,
                ] {
                    profile.setattr("etop", inflow_top)?;
                    let recorded = tests.call_method1("_lapse", (&widget,))?;
                    let lines = recorded
                        .getattr("lines")?
                        .extract::<Vec<(f64, f64, f64, f64)>>()?;
                    assert_eq!(lines.len(), 3);
                    let vertical = lines.iter().find(|line| line.0 == line.2).unwrap();
                    assert!(
                        (vertical.0 - expected).abs() < 1e-6,
                        "bracket shifted: {} instead of {expected}",
                        vertical.0
                    );
                    let texts = recorded.getattr("texts")?.cast_into::<PyList>()?;
                    assert_eq!(texts.len(), 1);
                    let rect = texts.get_item(0)?.get_item(0)?;
                    let center: f64 = rect.call_method0("center")?.call_method0("x")?.extract()?;
                    assert!(
                        (center - expected).abs() < 1e-6,
                        "value shifted: {center} instead of {expected}"
                    );
                    assert!((center - sfc_column).abs() > 10.0);
                }
            }
        }
        profile.setattr("etop", original_top)?;
        widget.setattr("plot_omega", true)?;
        fixture.call_method0("close")?;
        app.call_method0("processEvents")?;
        Ok(())
    })
    .unwrap();
}
