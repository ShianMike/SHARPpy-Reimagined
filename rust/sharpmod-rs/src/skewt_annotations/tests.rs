use pyo3::{
    prelude::*,
    types::{PyDict, PyList},
};

#[test]
#[ignore = "requires the pinned offscreen Qt rendering environment"]
fn only_active_temperature_and_dewpoint_trace_pens_get_translucent() {
    let _lock = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let widget = PyModule::import(py, "types")?
            .getattr("SimpleNamespace")?
            .call0()?;
        let temperature = PyList::new(py, [24., 20.])?;
        let dewpoint = PyList::new(py, [19., 15.])?;
        let wetbulb = PyList::new(py, [21., 17.])?;
        let pressure = PyList::new(py, [1000., 900.])?;
        let none = py.None().into_bound(py);
        widget.setattr("tmpc", &temperature)?;
        widget.setattr("dwpc", &dewpoint)?;
        let gui = PyModule::import(py, "qtpy.QtGui")?;
        let image_type = gui.getattr("QImage")?;
        let image = image_type.call1((4, 4, image_type.getattr("Format_ARGB32")?))?;
        let painter = gui.getattr("QPainter")?.call1((&image,))?;
        let color = gui.getattr("QColor")?.call1((255, 32, 8, 200))?;
        let pen = gui.getattr("QPen")?.call1((&color,))?;
        pen.call_method1("setWidthF", (3.0,))?;
        for (trace, pressure, expected_alpha) in [
            (temperature.as_any(), &none, 140),
            (dewpoint.as_any(), &none, 140),
            (wetbulb.as_any(), &none, 200),
            (temperature.as_any(), pressure.as_any(), 200),
        ] {
            painter.call_method1("setPen", (&pen,))?;
            super::set_skewt_trace_opacity(&widget, trace, pressure, &painter)?;
            let actual = painter.call_method0("pen")?;
            assert_eq!(actual.call_method0("widthF")?.extract::<f64>()?, 3.0);
            let tint = actual.call_method0("color")?;
            assert_eq!(tint.call_method0("alpha")?.extract::<u8>()?, expected_alpha);
            assert_eq!(tint.call_method0("name")?.extract::<String>()?, "#ff2008");
        }
        assert_eq!(color.call_method0("alpha")?.extract::<u8>()?, 200);
        painter.call_method0("end")?;
        Ok(())
    })
    .unwrap();
}

#[test]
fn effective_inflow_tags_center_as_a_group_and_stay_inside_plot_edges() {
    let columns =
        |bracket_x| super::effective_inflow_tag_columns(bracket_x, 32., 54., 6., 20., 240.);
    assert_eq!(columns(100.), (54., 92.));
    assert_eq!(columns(40.), (20., 58.));
    assert_eq!(columns(210.), (148., 186.));
}

#[test]
#[ignore = "requires the pinned offscreen Qt rendering environment"]
fn fixed_layer_columns_follow_axis_across_sizes_omega_and_inflow_layers() {
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
        let core = PyModule::import(py, "qtpy.QtCore")?;
        for (width, height) in [(900, 700), (1200, 850), (700, 560), (560, 460)] {
            tests.call_method1("_sized", (&widget, &app, width, height))?;
            let scale: f64 = widget.getattr("scale")?.extract()?;
            let origin: f64 = widget.getattr("originx")?.extract()?;
            let reference = widget.getattr("pmax")?;
            let expected = origin
                + widget
                    .call_method1("tmpc_to_pix", (-25.0, &reference))?
                    .extract::<f64>()?
                    / scale;
            let sfc_column: f64 = widget
                .call_method1("tmpc_to_pix", (-15.0, 1000.0))?
                .extract()?;
            let inflow = tests.call_method1("_inflow_layout", (&widget,))?;
            let inflow_x: f64 = inflow.get_item("x1")?.extract()?;
            assert!((inflow_x - sfc_column).abs() < 1e-6);
            if width >= 900 {
                let height_left: f64 = inflow
                    .get_item("rect_top")?
                    .call_method0("left")?
                    .extract()?;
                let helicity_right: f64 = inflow
                    .get_item("rect_esrh")?
                    .call_method0("right")?
                    .extract()?;
                assert!(((height_left + helicity_right) / 2.0 - inflow_x).abs() < 1e-6);
            }
            let hgz_column = origin
                + widget
                    .call_method1("tmpc_to_pix", (-35.0, &reference))?
                    .extract::<f64>()?
                    / scale;
            let plot = core.getattr("QRect")?.call1((0, 0, width, height))?;
            for omega in [false, true] {
                widget.setattr("plot_omega", omega)?;
                let hgz = tests.getattr("_Recorder")?.call0()?;
                let (top, bottom) = (125., 230.);
                assert!(super::draw_fixed_hgz_layer(
                    &widget,
                    &hgz,
                    &plot,
                    Some((top, bottom)),
                    None
                )?);
                let lines: Vec<(f64, f64, f64, f64)> = hgz.getattr("lines")?.extract()?;
                assert_eq!(
                    lines,
                    [
                        (hgz_column - 10., bottom, hgz_column + 10., bottom),
                        (hgz_column - 10., top, hgz_column + 10., top),
                        (hgz_column, bottom, hgz_column, top),
                    ]
                );
                let label = hgz.getattr("texts")?.get_item(0)?;
                assert_eq!(label.get_item(1)?.extract::<String>()?, "HGZ");
                let rect = label.get_item(0)?;
                let center: f64 = rect.call_method0("center")?.call_method0("x")?.extract()?;
                assert!((center - hgz_column).abs() < 1e-6);
                assert!((rect.call_method0("bottom")?.extract::<f64>()? - top).abs() < 1e-6);
                let color = hgz.getattr("colors")?.get_item(0)?;
                assert_eq!(color.call_method0("alpha")?.extract::<u8>()?, 255);
                assert_eq!(hgz.getattr("rects")?.len()?, 0);
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
        assert_readable_lapse_rate_labels(&widget, &tests)?;
        fixture.call_method0("close")?;
        app.call_method0("processEvents")?;
        Ok(())
    })
    .unwrap();
}

fn assert_readable_lapse_rate_labels(
    widget: &Bound<'_, PyAny>,
    tests: &Bound<'_, PyModule>,
) -> PyResult<()> {
    let py = widget.py();
    let gui = PyModule::import(py, "qtpy.QtGui")?;
    let color = gui.getattr("QColor")?;
    let font = gui.getattr("QFont")?;
    let base_font = font.call1((widget.getattr("esrh_font")?,))?;
    base_font.call_method1("setBold", (false,))?;
    widget.setattr("esrh_font", &base_font)?;
    let bold_font = font.call1((&base_font,))?;
    bold_font.call_method1("setBold", (true,))?;

    // Use distinctive widget colours to verify the two severe tiers still use them.
    let alerts = widget.getattr("alert_colors")?;
    alerts.set_item(4, color.call1(("#e53448",))?)?;
    alerts.set_item(5, color.call1(("#b62ad9",))?)?;
    let profile = widget.getattr("prof")?;
    let layer = profile.getattr("max_lapse_rate_2_6")?;
    let (bottom, top) = (layer.get_item(1)?, layer.get_item(2)?);
    let mock = PyModule::import(py, "unittest.mock")?.getattr("Mock")?;
    let recorder_type = tests.getattr("_Recorder")?;

    for (background, low, moderate) in [
        ("#000000", "#d2a261", "#ffc46b"),
        ("#ffffff", "#775000", "#996600"),
    ] {
        let plot_color = color.call1((background,))?;
        widget.setattr("bg_color", &plot_color)?;
        for (rate, expected) in [
            (5.0, low),
            (6.5, moderate),
            (7.5, "#e53448"),
            (8.5, "#b62ad9"),
        ] {
            let annotation_color = color.call1((expected,))?;
            profile.setattr("max_lapse_rate_2_6", (rate, &bottom, &top))?;
            let recorder = recorder_type.call0()?;
            let kwargs = PyDict::new(py);
            kwargs.set_item("wraps", &recorder)?;
            let painter = mock.call((), Some(&kwargs))?;

            super::draw_fixed_lapse_layer(widget, &painter, 4.5)?;

            let mut pen = None;
            let mut draws = Vec::new();
            for call in painter.getattr("method_calls")?.try_iter()? {
                let call = call?;
                let name: String = call.get_item(0)?.extract()?;
                let args = call.get_item(1)?;
                match name.as_str() {
                    "setPen" => pen = Some(args.get_item(0)?),
                    "drawLine" => {
                        let pen = pen.as_ref().expect("annotation pen should be set");
                        assert!(
                            pen.call_method0("color")?.eq(&annotation_color)?,
                            "{name} at {rate:.1} °C/km on {background} should use {expected}"
                        );
                        assert_eq!(pen.call_method0("widthF")?.extract::<f64>()?, 2.0);
                        draws.push("bracket");
                    }
                    "drawText" => {
                        let pen = pen.as_ref().expect("label pen should be set");
                        let color = pen.call_method0("color")?;
                        if color.eq(&plot_color)? {
                            draws.push("halo");
                        } else {
                            assert!(color.eq(&annotation_color)?);
                            draws.push("text");
                        }
                    }
                    "drawRect" | "fillRect" => panic!("annotation should have no backing plate"),
                    _ => {}
                }
            }
            let mut expected_order = vec!["bracket"; 3];
            expected_order.extend(["halo"; 8]);
            expected_order.push("text");
            assert_eq!(draws, expected_order);
            let label = recorder.getattr("texts")?.get_item(8)?;
            assert!(label.get_item(2)?.eq(&bold_font)?);
            assert!(!widget
                .getattr("esrh_font")?
                .call_method0("bold")?
                .is_truthy()?);
        }
    }
    Ok(())
}
