use super::*;
use std::{fs, path::PathBuf};

fn request<'py>(py: Python<'py>, json: &str) -> Bound<'py, PyAny> {
    PyModule::import(py, "json")
        .unwrap()
        .call_method1("loads", (json,))
        .unwrap()
}

fn rejected(py: Python<'_>, value: &Bound<'_, PyAny>) {
    let error = validate_sounding_view(py, value).unwrap_err();
    assert_eq!(
        error
            .value(py)
            .getattr("code")
            .unwrap()
            .extract::<String>()
            .unwrap(),
        "INVALID_ARGUMENT"
    );
    assert!(!error
        .value(py)
        .getattr("retryable")
        .unwrap()
        .is_truthy()
        .unwrap());
}

#[test]
fn rejects_invalid_objects_options_and_level_edits() {
    Python::initialize();
    Python::attach(|py| {
        for json in [
            "null",
            "[]",
            "{}",
            r#"{"edits": [], "other": 0}"#,
            r#"{"edits": null}"#,
            r#"{"edits": [0]}"#,
            r#"{"edits": [{"index": 0, "values": {}}]}"#,
            r#"{"edits": [{"index": 0, "values": {"other": 1}}]}"#,
            r#"{"edits": [{"index": 0, "values": {"u": 1}, "other": 1}]}"#,
            r#"{"edits": [{"index": 0, "values": {"u": 1}}, {"index": 0, "values": {"v": 1}}]}"#,
            r#"{"edits": [{"index": -1, "values": {"u": 1}}]}"#,
            r#"{"edits": [{"index": -2, "values": {"u": 1, "v": 2, "tmpc": 3}}]}"#,
            r#"{"edits": [{"index": true, "values": {"u": 1}}]}"#,
            r#"{"edits": [{"index": 0.0, "values": {"u": 1}}]}"#,
            r#"{"edits": [{"index": -3, "values": {"u": 1}}]}"#,
            r#"{"edits": [{"index": 10001, "values": {"u": 1}}]}"#,
        ] {
            rejected(py, &request(py, json));
        }
        for key in ["chart", "panel", "mode"] {
            for value in ["null", "false", "0", r#""unknown""#] {
                rejected(
                    py,
                    &request(py, &format!(r#"{{"edits": [], "{key}": {value}}}"#)),
                );
            }
        }
        for value in [
            "-1",
            "1000001",
            "true",
            "1.0",
            "null",
            "999999999999999999999999999",
        ] {
            rejected(
                py,
                &request(py, &format!(r#"{{"edits": [], "timeIndex": {value}}}"#)),
            );
        }
        for (field, invalid) in [
            ("tmpc", "65.01"),
            ("dwpc", "-150.01"),
            ("u", "350.01"),
            ("v", "-350.01"),
        ] {
            rejected(
                py,
                &request(
                    py,
                    &format!(
                        r#"{{"edits": [{{"index": 0, "values": {{"{field}": {invalid}}}}}]}}"#
                    ),
                ),
            );
        }
        for value in [
            "true",
            "null",
            r#""20""#,
            "NaN",
            "Infinity",
            "-Infinity",
            "1e400",
        ] {
            rejected(
                py,
                &request(
                    py,
                    &format!(r#"{{"edits": [{{"index": 0, "values": {{"tmpc": {value}}}}}]}}"#),
                ),
            );
        }
    });
}

#[test]
fn accepts_numeric_boundaries_and_all_existing_plot_options() {
    Python::initialize();
    Python::attach(|py| {
        let input = request(
            py,
            r#"{"edits": [
            {"index": 0, "values": {"tmpc": -150, "dwpc": -150, "u": -350, "v": 350}},
            {"index": 10000, "values": {"tmpc": 65, "dwpc": 65}},
            {"index": -1, "values": {"u": 350, "v": -350}},
            {"index": -2, "values": {"u": 0, "v": 0}}
        ], "timeIndex": 1000000}"#,
        );
        let view = input.cast::<PyDict>().unwrap();
        for mode in ["severe", "winter"] {
            for chart in ["streamwiseness", "srw", "theta", "cape"] {
                for panel in [
                    "STP STATS",
                    "COND STP",
                    "WINTER",
                    "FIRE",
                    "SHIP",
                    "VROT",
                    "HEIGHT CHARTS",
                ] {
                    view.set_item("mode", mode).unwrap();
                    view.set_item("chart", chart).unwrap();
                    view.set_item("panel", panel).unwrap();
                    let normalized = validate_sounding_view(py, view.as_any()).unwrap();
                    assert!(normalized.eq(view).unwrap());
                }
            }
        }
    });
}

#[test]
fn wind_measurement_validates_and_normalizes_the_view() {
    Python::initialize();
    Python::attach(|py| {
        for json in [
            r#"{"edits": [], "measureWind": null}"#,
            r#"{"edits": [], "measureWind": 1}"#,
            r#"{"edits": [], "measureWind": "true"}"#,
            r#"{"edits": [], "measureWind": true, "animationRange": 80}"#,
            r#"{"edits": [], "measureWind": true, "drag": {"target": "wind:0", "x": 0.2, "y": 0.3}}"#,
        ] {
            rejected(py, &request(py, json));
        }
        let measured = validate_sounding_view(
            py,
            &request(py, r#"{"edits": [], "measureWind": true, "drag": null, "mode": "winter", "timeIndex": 2}"#),
        )
        .unwrap();
        assert!(measured
            .get_item("measureWind")
            .unwrap()
            .unwrap()
            .is_truthy()
            .unwrap());
        assert_eq!(
            measured
                .get_item("mode")
                .unwrap()
                .unwrap()
                .extract::<String>()
                .unwrap(),
            "winter"
        );
        assert_eq!(
            measured
                .get_item("timeIndex")
                .unwrap()
                .unwrap()
                .extract::<usize>()
                .unwrap(),
            2
        );
        assert!(!measured.contains("drag").unwrap());

        let normal = request(
            py,
            r#"{"edits": [], "animationRange": 80, "drag": {"target": "wind:0", "x": 0.2, "y": 0.3}}"#,
        );
        let omitted = validate_sounding_view(py, &normal).unwrap();
        normal.set_item("measureWind", false).unwrap();
        let explicit_false = validate_sounding_view(py, &normal).unwrap();
        assert!(explicit_false.eq(omitted).unwrap());
    });
}

#[test]
fn bounds_animation_ranges_before_rendering() {
    Python::initialize();
    Python::attach(|py| {
        for value in [
            "null",
            "true",
            "false",
            "{}",
            "[]",
            r#""100""#,
            "NaN",
            "Infinity",
            "-Infinity",
            "39.99",
            "500.01",
            "1e400",
        ] {
            rejected(
                py,
                &request(
                    py,
                    &format!(r#"{{"edits": [], "animationRange": {value}}}"#),
                ),
            );
        }
        for value in [40.0, 40.5, 200.0, 500.0] {
            let input = request(
                py,
                &format!(r#"{{"edits": [], "animationRange": {value}}}"#),
            );
            let normalized = validate_sounding_view(py, &input).unwrap();
            assert_eq!(
                normalized
                    .get_item("animationRange")
                    .unwrap()
                    .unwrap()
                    .extract::<f64>()
                    .unwrap(),
                value
            );
        }
        let input = request(py, r#"{"edits": []}"#);
        input
            .set_item(
                "animationRange",
                py.get_type::<PyInt>().call1(("9".repeat(400),)).unwrap(),
            )
            .unwrap();
        rejected(py, &input);
    });
}

#[test]
fn normalizes_drag_targets_and_rejects_invalid_positions() {
    Python::initialize();
    Python::attach(|py| {
        for (target, expected) in [
            (" tmpc:+0001 ", "tmpc:1"),
            ("dwpc:0", "dwpc:0"),
            ("wind:10000", "wind:10000"),
            ("storm:-02", "storm:-2"),
        ] {
            let input = request(
                py,
                r#"{"edits": [], "drag": {"target": "", "x": 0, "y": 1}}"#,
            );
            input
                .get_item("drag")
                .unwrap()
                .set_item("target", target)
                .unwrap();
            let normalized = validate_sounding_view(py, &input).unwrap();
            assert_eq!(
                normalized
                    .get_item("drag")
                    .unwrap()
                    .unwrap()
                    .get_item("target")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                expected
            );
            assert_eq!(
                input
                    .get_item("drag")
                    .unwrap()
                    .get_item("target")
                    .unwrap()
                    .extract::<String>()
                    .unwrap(),
                target
            );
        }
        for target in [
            "",
            "wind",
            "other:0",
            "wind:-1",
            "storm:0",
            "storm:-3",
            "tmpc:10001",
            "tmpc:1:2",
            "tmpc:1.0",
            "tmpc:999999999999999999999999999999",
        ] {
            let input = request(
                py,
                r#"{"edits": [], "drag": {"target": "", "x": 0, "y": 1}}"#,
            );
            input
                .get_item("drag")
                .unwrap()
                .set_item("target", target)
                .unwrap();
            rejected(py, &input);
        }
        for key in ["x", "y"] {
            for value in ["null", "true", "NaN", "Infinity", "-0.01", "1.01"] {
                let input = request(
                    py,
                    r#"{"edits": [], "drag": {"target": "wind:0", "x": 0.5, "y": 0.5}}"#,
                );
                input
                    .get_item("drag")
                    .unwrap()
                    .set_item(key, request(py, value))
                    .unwrap();
                rejected(py, &input);
            }
        }
    });
}

#[test]
fn equivalent_defaults_share_cache_keys_without_mutating_the_request() {
    Python::initialize();
    Python::attach(|py| {
        let api = PyModule::import(py, "sharpmod.bridge.sounding_view").unwrap();
        let input = request(py, r#"{"edits": [{"index": 0, "values": {"tmpc": 31}}]}"#);
        let normalized = validate_sounding_view(py, &input).unwrap();
        let key = api
            .call_method1("_render_key", ("MU", "uhd", &normalized, "standard"))
            .unwrap();
        let defaults = request(
            py,
            r#"{"edits": [{"index": 0, "values": {"tmpc": 31}}], "mode": "severe", "timeIndex": 0, "chart": "streamwiseness", "panel": "STP STATS", "drag": null}"#,
        );
        let same = validate_sounding_view(py, &defaults).unwrap();
        assert!(key
            .eq(api
                .call_method1("_render_key", ("MU", "uhd", &same, "standard"))
                .unwrap())
            .unwrap());
        same.set_item("mode", "winter").unwrap();
        assert!(!key
            .eq(api
                .call_method1("_render_key", ("MU", "uhd", same, "standard"))
                .unwrap())
            .unwrap());
        normalized
            .get_item("edits")
            .unwrap()
            .unwrap()
            .get_item(0)
            .unwrap()
            .get_item("values")
            .unwrap()
            .set_item("tmpc", 40)
            .unwrap();
        assert_eq!(
            input
                .get_item("edits")
                .unwrap()
                .get_item(0)
                .unwrap()
                .get_item("values")
                .unwrap()
                .get_item("tmpc")
                .unwrap()
                .extract::<i64>()
                .unwrap(),
            31
        );
        assert_eq!(input.len().unwrap(), 1);
    });
}

#[test]
fn edit_limit_accepts_2000_unique_levels_and_rejects_2001() {
    Python::initialize();
    Python::attach(|py| {
        let edits = PyList::empty(py);
        let input = PyDict::new(py);
        input.set_item("edits", &edits).unwrap();
        for index in 0..2000 {
            edits
                .append(request(
                    py,
                    &format!(r#"{{"index": {index}, "values": {{"u": 0}}}}"#),
                ))
                .unwrap();
        }
        validate_sounding_view(py, input.as_any()).unwrap();
        edits
            .append(request(py, r#"{"index": 2000, "values": {"u": 0}}"#))
            .unwrap();
        rejected(py, input.as_any());
    });
}

#[test]
fn invalid_requests_never_start_a_render_worker() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let directory =
        std::env::temp_dir().join(format!("sharpmod-view-boundary-{}", std::process::id()));
    fs::create_dir(&directory).unwrap();
    let path: PathBuf = directory.join("source.npz");
    fs::write(&path, b"source must remain untouched").unwrap();
    Python::attach(|py| -> PyResult<()> {
        let api = PyModule::import(py, "sharpmod.bridge.sounding_view")?;
        let cache_api = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
        cache_api.call_method1("reset_cache", (&directory,))?;
        let entry = cache_api.call_method1(
            "Entry",
            ("view-boundary", &path, request(py, r#"{"time_count": 1}"#)),
        )?;
        let cache = cache_api.call_method0("cache")?;
        cache.call_method1("add", (&entry,))?;
        let patch = PyModule::import(py, "unittest.mock")?
            .call_method1("patch", ("sharpmod.bridge.render_client.client",))?;
        let worker = patch.call_method0("start")?;
        for view in [
            r#"{"edits": [], "animationRange": 0}"#,
            r#"{"edits": [{"index": -1, "values": {"u": 30}}]}"#,
        ] {
            let params = PyDict::new(py);
            params.set_item("sounding_id", "view-boundary")?;
            params.set_item("view", request(py, view))?;
            let error = api.call_method1("render_view", (params,)).unwrap_err();
            assert_eq!(
                error.value(py).getattr("code")?.extract::<String>()?,
                "INVALID_ARGUMENT"
            );
        }
        worker.call_method0("assert_not_called")?;
        assert_eq!(entry.getattr("view_renders")?.len()?, 0);
        patch.call_method0("stop")?;
        assert_eq!(fs::read(&path).unwrap(), b"source must remain untouched");
        cache_api.call_method1("reset_cache", (py.None(),))?;
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(directory).unwrap();
}
