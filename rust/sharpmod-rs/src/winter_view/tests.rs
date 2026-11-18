use super::*;
use std::fmt::Write;

#[test]
#[ignore = "requires the pinned Qt environment and existing theme color resolver"]
fn winter_value_colors_stay_readable_on_dark_and_light_backgrounds() {
    use super::value_colors::{Scale, ValueColors};
    pyo3::Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let contrast = PyModule::import(py, "sharpmod.viz.colors")?.getattr("contrast_ratio")?;
        for (bg, fg) in [("#000000", "#ffffff"), ("#ffffff", "#000000")] {
            let palette = panels::Palette {
                bg: bg.into(),
                fg: fg.into(),
                temp: "#ff0000".into(),
                dew: "#00ff00".into(),
                wet: "#00ffff".into(),
            };
            let colors = ValueColors::from_palette(py, &palette)?;
            for state in [Some(true), Some(false), None] {
                let ratio: f64 = contrast.call1((colors.check_color(state), bg))?.extract()?;
                assert!(ratio >= 4.5, "ingredient contrast {ratio} for {bg}");
            }
            for scale in [
                Scale::Temperature,
                Scale::Energy,
                Scale::Wind,
                Scale::Humidity,
                Scale::Omega,
                Scale::SnowSquall,
            ] {
                for value in [
                    -500., -100., -30., -10., -1., 0., 0.1, 1., 5., 10., 25., 50., 100., 500.,
                ] {
                    let ratio: f64 = contrast
                        .call1((colors.color(scale, value), bg))?
                        .extract()?;
                    assert!(ratio >= 4.5, "color contrast {ratio} for {bg} / {value}");
                }
            }
        }
        Ok(())
    })
    .unwrap();
}

#[test]
#[ignore = "requires the pinned SHARPpy, NumPy and offscreen Qt environment"]
fn winter_render_keeps_edit_targets_and_severe_render_is_unchanged() {
    // End-to-end native composition on Qt's offscreen platform, including a
    // warm nose, snow-growth zone and dry upper levels. No provider required.
    std::env::set_var("QT_QPA_PLATFORM", "offscreen");
    pyo3::Python::initialize();
    let dir = std::env::temp_dir().join(format!("sharpmod-winter-native-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("winter.spc");
    let mut fixture = "%TITLE%\n WINTER 261114/1200\n\n%RAW%\n".to_owned();
    for i in 0..=18 {
        let p = 1000. - i as f64 * 50.;
        let z = 8000. * (1000. / p).ln();
        let t = if i < 3 {
            -4. + i as f64 * 3.5
        } else {
            (3. - (i - 2) as f64 * 5.).max(-65.)
        };
        writeln!(
            fixture,
            "{p:.2}, {z:.2}, {t:.2}, {:.2}, {}, {}",
            t - 1.,
            180 + i * 4,
            8 + i * 3
        )
        .unwrap();
    }
    fixture.push_str("%END%\n");
    std::fs::write(&input, fixture).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let extension = PyModule::import(py, "sharpmod_rs")?;
        register(&extension)?;
        crate::locator_paths::register(&extension)?;
        // Inspect the actual Qt composition at the mode boundary, before the
        // window is captured and destroyed by the existing renderer.
        let verify_winter = PyCFunction::new_closure(
            py,
            Some(c"verify_winter_viewport_and_strokes"),
            None,
            |args: &Bound<'_, PyTuple>, _: Option<&Bound<'_, PyDict>>| -> PyResult<Py<PyAny>> {
                let win = args.get_item(0)?;
                let view = args.get_item(1)?.cast_into::<PyDict>()?;
                let app = args.get_item(2)?;
                let original = args.get_item(3)?;
                let sw = win.getattr("spc_widget")?;
                let hodo = sw.getattr("hodo")?;
                let magnitude: f64 = hodo.getattr("hodomag")?.extract()?;
                let scale: f64 = hodo.getattr("scale")?.extract()?;
                let center_mode: String = hodo.getattr("center_loc")?.extract()?;
                let center = || -> PyResult<(f64, f64)> {
                    hodo.call_method1(
                        "pix_to_uv",
                        (
                            hodo.call_method0("width")?.extract::<f64>()? / 2.,
                            hodo.call_method0("height")?.extract::<f64>()? / 2.,
                        ),
                    )?
                    .extract()
                };
                let before = center()?;
                assert_eq!(center_mode, "meanwind");
                assert!(before.0.abs() + before.1.abs() > 1.);
                let sound = sw.getattr("sound")?;
                let skew_background = sound.getattr("backgroundBitMap")?.call_method0("toImage")?;
                let advection = sw.getattr("inferred_temp_advection")?;
                let advection_image = advection.getattr("plotBitMap")?.call_method0("toImage")?;
                let hgz_pixel = || -> PyResult<u32> {
                    let bitmap = sound.getattr("plotBitMap")?;
                    let density: f64 = bitmap.call_method0("devicePixelRatioF")?.extract()?;
                    let scale: f64 = sound.getattr("scale")?.extract()?;
                    let x: f64 = sound.getattr("originx")?.extract::<f64>()?
                        + sound
                            .call_method1("tmpc_to_pix", (-35., sound.getattr("pmax")?))?
                            .extract::<f64>()?
                            / scale;
                    // The fixture is about −22 °C at 650 hPa, inside its HGZ.
                    let y = sound.getattr("originy")?.extract::<f64>()?
                        + sound
                            .call_method1("pres_to_pix", (650.,))?
                            .extract::<f64>()?
                            / scale;
                    bitmap
                        .call_method0("toImage")?
                        .call_method1("pixel", ((x * density) as i32, (y * density) as i32))?
                        .extract()
                };
                let severe_hgz = hgz_pixel()?;
                assert!(matches!(severe_hgz, 0xff00c8ff | 0xff007599));

                let result = configure_winter_view(args.py(), &win, &view, &app, &original)?;
                assert_eq!(hgz_pixel()?, severe_hgz, "winter lost the HGZ bracket");
                assert_eq!(hodo.getattr("hodomag")?.extract::<f64>()?, magnitude);
                assert_eq!(hodo.getattr("scale")?.extract::<f64>()?, scale);
                assert_eq!(
                    hodo.getattr("center_loc")?.extract::<String>()?,
                    center_mode
                );
                let after = center()?;
                assert!((before.0 - after.0).abs() < 1e-9);
                assert!((before.1 - after.1).abs() < 1e-9);
                assert!(sound
                    .getattr("backgroundBitMap")?
                    .call_method0("toImage")?
                    .eq(&skew_background)?);
                assert!(advection
                    .getattr("plotBitMap")?
                    .call_method0("toImage")?
                    .eq(&advection_image)?);

                // The vertical axis near the top is clear of this fixture's
                // wind trace and labels. Compare its antialiased edge pixels
                // with the single-pass native cache at the capture density.
                let bitmap = hodo.getattr("plotBitMap")?;
                let density: f64 = bitmap.call_method0("devicePixelRatioF")?.extract()?;
                let image = bitmap.call_method0("toImage")?;
                let background = hodo.getattr("backgroundBitMap")?.call_method0("toImage")?;
                let x = (hodo.getattr("centerx")?.extract::<f64>()? * density).round() as i32;
                let y = (12. * density).round() as i32;
                for offset in -6..=6 {
                    assert_eq!(
                        image
                            .call_method1("pixel", (x + offset, y))?
                            .extract::<u32>()?,
                        background
                            .call_method1("pixel", (x + offset, y))?
                            .extract::<u32>()?,
                        "winter repainted the cached hodograph axis at offset {offset}"
                    );
                }
                Ok(result.unbind())
            },
        )?;
        extension.setattr("configure_winter_view", verify_winter)?;
        let cli = PyModule::import(py, "sharpmod.rendering.cli")?;
        let mut severe = None;
        let mut severe_size = None;
        for mode in ["severe", "winter", "severe"] {
            let out = dir.join(format!("{mode}.png"));
            let view = PyDict::new(py);
            view.set_item("edits", pyo3::types::PyList::empty(py))?;
            view.set_item("chart", "streamwiseness")?;
            view.set_item("panel", "STP STATS")?;
            view.set_item("mode", mode)?;
            let metadata = PyDict::new(py);
            let kwargs = PyDict::new(py);
            kwargs.set_item("image_mode", "hd")?;
            kwargs.set_item("show_locator", false)?;
            kwargs.set_item("view", view)?;
            kwargs.set_item("view_result", &metadata)?;
            cli.call_method(
                "render",
                (input.to_str().unwrap(), out.to_str().unwrap()),
                Some(&kwargs),
            )?;
            let bytes = std::fs::read(out).unwrap();
            assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n");
            let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
            let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
            if mode == "winter" {
                assert_eq!(Some((width, height)), severe_size);
            } else if let Some(previous) = &severe {
                assert_eq!(&bytes, previous);
            } else {
                severe_size = Some((width, height));
                severe = Some(bytes.clone());
            }
            assert!(metadata.get_item("points")?.unwrap().len()? > 25);
            let bounds = metadata
                .get_item("inspection")?
                .unwrap()
                .get_item("bounds")?;
            let right: f64 = bounds.get_item("right")?.extract()?;
            assert!((0.1..0.7).contains(&right));
            let locator = metadata.get_item("locatorBounds")?.unwrap();
            let left: f64 = locator.get_item("left")?.extract()?;
            let top: f64 = locator.get_item("top")?.extract()?;
            let right: f64 = locator.get_item("right")?.extract()?;
            let bottom: f64 = locator.get_item("bottom")?.extract()?;
            assert!(0.1 < left && left < right && right <= 1.);
            assert!(0. <= top && top < bottom && bottom <= 1.);
        }
        Ok(())
    })
    .unwrap();
}
