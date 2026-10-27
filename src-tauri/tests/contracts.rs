//! Every shared fixture in `contracts/` against the Rust types (DESIGN-contracts C7).

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::Value;

use sharppy_reimagined_lib::backend::convert;
use sharppy_reimagined_lib::dto::health::{BackendHealth, ScientificBackendStatus};
use sharppy_reimagined_lib::dto::stations::StationCatalog;
use sharppy_reimagined_lib::error::{BackendError, ErrorCode};
use sharppy_reimagined_lib::python::protocol::{
    BridgeErrorCode, CatalogResult, ErrorPayload, HealthResult, HelloResult,
};

fn contracts() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("contracts")
}

#[test]
fn weather_alerts_reject_unsafe_or_misleading_snapshots() {
    use sharppy_reimagined_lib::dto::weather_alerts::WeatherAlerts;
    for field in ["kind", "colour", "source_url", "expires", "polygons"] {
        let mut data = value("bridge/map/alerts.result.json");
        data["alerts"][0][field] = match field {
            "kind" => serde_json::json!("advisory"),
            "colour" => serde_json::json!("red"),
            "source_url" => serde_json::json!("https://api.weather.gov.evil.example"),
            "expires" => data["fetched_at"].clone(),
            _ => serde_json::json!([[[[181.0, 0.0], [0.0, 1.0], [1.0, 1.0], [181.0, 0.0]]]]),
        };
        assert!(
            serde_json::from_value::<WeatherAlerts>(data)
                .unwrap()
                .validate()
                .is_err(),
            "{field}"
        );
    }
}

#[test]
fn only_explicit_non_retryable_internal_cancellation_is_expected() {
    let mut error = BackendError::internal("get_weather_field");
    for flag in [
        Value::Null,
        serde_json::json!(false),
        serde_json::json!("true"),
        serde_json::json!(true),
    ] {
        error.details =
            Some(serde_json::from_value(serde_json::json!({"cancelled": flag})).unwrap());
        assert_eq!(error.is_cancelled(), flag == serde_json::json!(true));
    }
    error.retryable = true;
    assert!(!error.is_cancelled());
    error.retryable = false;
    error.code = ErrorCode::FieldFetchFailed;
    assert!(!error.is_cancelled());
}

fn text(relative: &str) -> String {
    fs::read_to_string(contracts().join(relative)).unwrap_or_else(|e| panic!("{relative}: {e}"))
}

fn value(relative: &str) -> Value {
    serde_json::from_str(&text(relative)).unwrap_or_else(|e| panic!("{relative}: {e}"))
}

fn typed<T: DeserializeOwned>(relative: &str) -> T {
    serde_json::from_str(&text(relative)).unwrap_or_else(|e| panic!("{relative}: {e}"))
}

/// Deserializes, re-serializes, and compares as JSON values.
fn round_trip<T: DeserializeOwned + Serialize>(relative: &str) -> T {
    let original = value(relative);
    let parsed: T =
        serde_json::from_value(original.clone()).unwrap_or_else(|e| panic!("{relative}: {e}"));
    let again = serde_json::to_value(&parsed).unwrap();
    assert_eq!(again, original, "{relative} does not round-trip");
    parsed
}

/// File names (not paths) of the files directly inside `dir`.
fn files(dir: &str) -> Vec<String> {
    let mut names: Vec<String> = fs::read_dir(contracts().join(dir))
        .unwrap()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

#[test]
fn every_bridge_fixture_round_trips_into_its_type() {
    let mut checked = 0;
    for name in files("bridge") {
        let path = format!("bridge/{name}");
        if name == "hello.result.json" {
            round_trip::<HelloResult>(&path);
        } else if name.starts_with("health.result.") {
            round_trip::<HealthResult>(&path);
        } else if name.starts_with("stations.catalog.result.") {
            round_trip::<CatalogResult>(&path);
        } else if name.starts_with("error.") {
            round_trip::<ErrorPayload>(&path);
        } else {
            panic!("unexpected bridge fixture {path}");
        }
        checked += 1;
    }
    assert_eq!(checked, 21);
}

/// A bridge payload (snake_case) read into its DTO serializes to the public fixture (camelCase).
fn bridge_to_public<T: DeserializeOwned + Serialize>(bridge: &str, public: &str) -> T {
    let parsed: T = typed(bridge);
    assert_eq!(
        serde_json::to_value(&parsed).unwrap(),
        value(public),
        "{bridge} does not convert to {public}"
    );
    parsed
}

#[test]
fn storage_fixtures_convert_and_reject_impossible_counts() {
    use sharppy_reimagined_lib::dto::storage::{CacheClearResult, StorageUsage};
    let usage: StorageUsage =
        bridge_to_public("bridge/storage/usage.json", "public/storage/usage.json");
    usage.validate().unwrap();
    let cleared: CacheClearResult =
        bridge_to_public("bridge/storage/clear.json", "public/storage/clear.json");
    cleared.validate().unwrap();
    let mut invalid = usage.clone();
    invalid.caches[0].clearable_bytes = invalid.caches[0].bytes + 1;
    assert!(invalid.validate().is_err());
    let mut duplicate = usage;
    duplicate.caches[1].key = "models".into();
    assert!(duplicate.validate().is_err());
}

#[test]
fn sounding_fixtures_convert_from_bridge_to_public_form() {
    use sharppy_reimagined_lib::dto::analysis::SoundingAnalysis;
    use sharppy_reimagined_lib::dto::models::ModelCatalog;
    use sharppy_reimagined_lib::dto::soundings::SoundingSummary;
    let catalog: ModelCatalog = bridge_to_public(
        "bridge/soundings/models.catalog.result.json",
        "public/soundings/models.catalog.json",
    );
    catalog.validate().unwrap();
    let maps_only: Vec<_> = catalog
        .models
        .iter()
        .filter(|model| !model.soundings_available)
        .collect();
    assert_eq!(maps_only.len(), 1);
    assert_eq!(maps_only[0].key, "aigfs");
    assert_eq!(maps_only[0].domain, "Global");
    let ecmwf = catalog
        .models
        .iter()
        .find(|m| m.key == "openmeteo-ecmwf-ifs025")
        .unwrap();
    assert_eq!(ecmwf.max_forecast_hours[0], 360);
    assert_eq!(ecmwf.max_forecast_hours[6], 144);
    let mut invalid = catalog.clone();
    invalid.models[0].max_forecast_hours.pop();
    assert!(invalid.validate().is_err());
    assert!(catalog
        .models
        .iter()
        .any(|m| m.key == "hrrr" && m.outline.len() > 100));
    let summary: SoundingSummary = bridge_to_public(
        "bridge/soundings/open.result.example.json",
        "public/soundings/summary.example.json",
    );
    summary.validate().unwrap();
    let analysis: SoundingAnalysis = bridge_to_public(
        "bridge/soundings/analysis.result.example.json",
        "public/soundings/analysis.example.json",
    );
    analysis.validate().unwrap();
    // The audited deterministic values of the example sounding (AUDIT.md §5.6).
    let table = &analysis.parcel_table;
    let column = table.columns.iter().position(|c| c.key == "cape").unwrap();
    let ml = table.rows.iter().find(|r| r.key == "ml").unwrap();
    assert!((ml.values[column].unwrap() - 2465.3).abs() < 0.5);
    let png = fs::read(contracts().join("public/soundings/render.example.png")).unwrap();
    assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
    let multi: SoundingSummary = bridge_to_public(
        "bridge/soundings/open.result.multi-time.json",
        "public/soundings/summary.multi-time.json",
    );
    multi.validate().unwrap();
    let trends: sharppy_reimagined_lib::dto::analysis::SoundingTrends = bridge_to_public(
        "bridge/soundings/trends.result.example.json",
        "public/soundings/trends.example.json",
    );
    trends.validate().unwrap();
    assert_eq!(trends.points.len(), multi.time_count as usize);
    assert!(trends.points.iter().all(|p| p.time.is_some()));
}

#[test]
fn sounding_progress_rejects_invalid_counters() {
    use sharppy_reimagined_lib::dto::sounding_progress::{DownloadStage, SoundingProgress};
    let progress: SoundingProgress = bridge_to_public(
        "bridge/soundings/progress.result.json",
        "public/soundings/progress.json",
    );
    progress.validate().unwrap();
    for stage in [DownloadStage::Decoding, DownloadStage::Calculating] {
        let mut phase = progress.clone();
        phase.stage = stage;
        phase.bytes_per_second = 0.0;
        phase.validate().unwrap();
        let public = serde_json::to_value(&phase).unwrap();
        assert!(matches!(
            public["stage"].as_str(),
            Some("decoding" | "calculating")
        ));
    }
    for speed in [-1.0, f64::NAN, f64::INFINITY] {
        let mut bad = progress.clone();
        bad.bytes_per_second = speed;
        assert!(bad.validate().is_err());
    }
    let mut bad = progress.clone();
    bad.stage = DownloadStage::Processing;
    assert!(bad.validate().is_err());
    let mut bad = progress;
    bad.downloaded_bytes = 1 << 53;
    assert!(bad.validate().is_err());
    assert!(
        serde_json::from_value::<SoundingProgress>(serde_json::json!({
            "stage": "unknown", "downloaded_bytes": 0, "bytes_per_second": 0,
        }))
        .is_err()
    );
}

#[test]
fn model_status_fixture_preserves_provider_state_and_cycle() {
    use sharppy_reimagined_lib::dto::models::ModelStatus;
    let mut status: ModelStatus = bridge_to_public(
        "bridge/soundings/models.status.result.json",
        "public/soundings/models.status.json",
    );
    status.validate().unwrap();
    status.forecast_hour = 1001;
    assert!(status.validate().is_err());
    let mut raw = value("bridge/soundings/models.status.result.json");
    raw["state"] = serde_json::json!("healthy");
    assert!(serde_json::from_value::<ModelStatus>(raw).is_err());
}

#[test]
fn threshold_fixtures_convert_from_bridge_to_public_form() {
    use sharppy_reimagined_lib::dto::threshold_catalog::{ThresholdCsv, ThresholdMetrics};
    use sharppy_reimagined_lib::dto::thresholds::ThresholdEvaluation;
    let evaluation: ThresholdEvaluation = bridge_to_public(
        "bridge/soundings/threshold.result.example.json",
        "public/soundings/threshold.example.json",
    );
    evaluation.validate().unwrap();
    // The rule the evaluator tested, and the sentence naming it.
    assert_eq!(evaluation.definition.name, "Warm-core shear");
    assert_eq!(evaluation.definition.conditions.len(), 2);
    assert_eq!(evaluation.definition.conditions[0].units, "J/kg");
    assert_eq!(evaluation.definition.conditions[1].units, "kt");
    assert!(evaluation.definition_summary.contains("2350"));
    // The counts partition the request and name their denominators.
    assert_eq!(evaluation.requested_member_count, 4);
    assert_eq!(evaluation.qualifying_member_count, 1);
    assert_eq!(evaluation.nonqualifying_member_count, 2);
    assert_eq!(evaluation.not_loaded_member_count, 1);
    assert!(evaluation.reconciles && !evaluation.cancelled);
    assert_eq!(evaluation.agreement, "1/3 (33%)");
    assert_eq!(evaluation.coverage, "3/4 (75%)");
    assert!((evaluation.agreement_fraction.unwrap() - 1.0 / 3.0).abs() < 1e-12);
    // One row per requested member, each in exactly one state with its phrase.
    assert_eq!(evaluation.members.len(), 4);
    let winner = &evaluation.members[0];
    assert_eq!(winner.state, "qualifying");
    assert_eq!(winner.state_label, "qualifies");
    assert_eq!(winner.satisfied, Some(true));
    assert_eq!(winner.values.len(), 2);
    let missing = &evaluation.members[3];
    assert_eq!(missing.state, "not_loaded");
    assert_eq!(missing.state_label, "not loaded");
    assert_eq!(missing.satisfied, None);
    assert!(missing.values.values().all(Option::is_none));

    let metrics: ThresholdMetrics = bridge_to_public(
        "bridge/soundings/threshold-metrics.result.json",
        "public/soundings/threshold-metrics.json",
    );
    metrics.validate().unwrap();
    // The catalog: every registry diagnostic with its own control bounds.
    assert!(metrics.metrics.len() > 50);
    let shear = metrics
        .metrics
        .iter()
        .find(|m| m.key == "shear_6km")
        .unwrap();
    assert_eq!(shear.units, "kt");
    assert!(shear.minimum < shear.suggested && shear.suggested < shear.maximum);
    assert_eq!(metrics.default_metrics.len(), 3);
    assert!(metrics
        .default_metrics
        .iter()
        .all(|k| metrics.metrics.iter().any(|m| &m.key == k)));

    let csv: ThresholdCsv = bridge_to_public(
        "bridge/soundings/threshold-csv.result.example.json",
        "public/soundings/threshold-csv.example.json",
    );
    csv.validate().unwrap();
    assert_eq!(csv.file_name, "ensemble-threshold-results.csv");
    assert_eq!(csv.csv.lines().count(), 5);

    let mut refused = evaluation.clone();
    refused.reconciles = false;
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.nonqualifying_member_count += 1;
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.members[0].satisfied = None;
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.members[0].state = "half".to_owned();
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.members[3].loaded = true;
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.agreement_fraction = Some(1.5);
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.definition.conditions[0].upper = Some(1.0);
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.members[0]
        .values
        .insert("extra".to_owned(), Some(1.0));
    assert!(refused.validate().is_err());
    let mut refused = evaluation.clone();
    refused.valid_time = Some("yesterday".to_owned());
    assert!(refused.validate().is_err());

    let mut refused = metrics.clone();
    refused.metrics[0].suggested = refused.metrics[0].maximum + 1.0;
    assert!(refused.validate().is_err());
    let mut refused = metrics.clone();
    refused.operators = vec![">".to_owned()];
    assert!(refused.validate().is_err());
    let mut refused = metrics.clone();
    refused.default_metrics = vec!["not-a-diagnostic".to_owned()];
    assert!(refused.validate().is_err());

    // The request side: the same rule shape the form submits.
    let input = evaluation.definition.to_input();
    input.validate().unwrap();
    let mut refused = input.clone();
    refused.conditions[0].operator = "~".to_owned();
    assert!(refused.validate().is_err());
    let mut refused = input.clone();
    refused.conditions[0].upper = Some(1.0);
    assert!(refused.validate().is_err());
    let mut refused = input.clone();
    refused.conditions[0].value = f64::NAN;
    assert!(refused.validate().is_err());
    let mut refused = input.clone();
    refused.conditions.push(refused.conditions[0].clone());
    refused.conditions[2].metric = refused.conditions[1].metric.clone();
    assert!(refused.validate().is_err());
}

/// Splits an SHRF frame into its JSON header and its body.
fn split_frame(frame: &[u8]) -> (Value, &[u8]) {
    assert_eq!(&frame[..8], b"SHRF\x01\x00\x00\x00");
    let length = u32::from_le_bytes([frame[8], frame[9], frame[10], frame[11]]) as usize;
    assert!(
        (12 + length).is_multiple_of(8),
        "the body is 8-byte aligned"
    );
    let header = serde_json::from_slice(&frame[12..12 + length]).unwrap();
    (header, &frame[12 + length..])
}

#[test]
fn map_fixtures_convert_from_bridge_to_public_form() {
    use sharppy_reimagined_lib::backend::map_ops::shrf_frame;
    use sharppy_reimagined_lib::dto::map::{
        AvailabilityResult, FieldGridResult, MapCatalog, PlaceLabels, RadarResult,
        RadarSiteCatalog, RadarSiteResult, SpcOutlook,
    };
    let catalog: MapCatalog =
        bridge_to_public("bridge/map/catalog.result.json", "public/map/catalog.json");
    catalog.validate().unwrap();
    assert_eq!(catalog.max_forecast_hours.len(), 24);
    for (model, data) in value("bridge/map/catalog.models.json").as_object().unwrap() {
        let catalog: MapCatalog = serde_json::from_value(data.clone()).unwrap();
        catalog.validate().unwrap();
        assert_eq!(&catalog.field_model, model);
        assert_eq!(
            catalog.products.len(),
            if model.starts_with("refs-") {
                22
            } else if model == "nam-3km-conus" {
                36
            } else if model == "nam" {
                34
            } else if model.starts_with("hrw-") {
                28
            } else if model == "gfs"
                || model == "ecmwf-ifs-9km"
                || model == "openmeteo-ecmwf-ifs025"
            {
                20
            } else if model == "aigfs" {
                9
            } else {
                38
            }
        );
        assert_eq!(
            serde_json::to_value(catalog).unwrap(),
            value("public/map/catalog.models.json")[model]
        );
    }
    for product in ["cat", "torn", "wind"] {
        let outlook: SpcOutlook = bridge_to_public(
            &format!("bridge/map/outlook.result.{product}.json"),
            &format!("public/map/outlook.{product}.json"),
        );
        outlook.validate().unwrap();
        assert_eq!(outlook.outlook.is_some(), product != "wind");
    }
    let places: PlaceLabels =
        bridge_to_public("bridge/map/places.result.json", "public/map/places.json");
    let alerts: sharppy_reimagined_lib::dto::weather_alerts::WeatherAlerts =
        bridge_to_public("bridge/map/alerts.result.json", "public/map/alerts.json");
    alerts.validate().unwrap();
    places.validate().unwrap();
    let report = typed::<AvailabilityResult>("bridge/map/availability.result.json").into_report();
    assert_eq!(
        serde_json::to_value(&report).unwrap(),
        value("public/map/availability.json")
    );

    let grid: FieldGridResult = typed("bridge/map/field.result.json");
    grid.validate().unwrap();
    let (built, _) = shrf_frame(&grid.header(), &grid.data_base64).unwrap();
    let committed = fs::read(contracts().join("public/map/field.shrf")).unwrap();
    let (built_header, built_body) = split_frame(&built);
    let (header, body) = split_frame(&committed);
    assert_eq!(built_header, header);
    assert_eq!(built_body, body);
    assert_eq!(body.len(), grid.width as usize * grid.height as usize * 2);

    let radar: RadarResult = typed("bridge/map/radar.result.json");
    radar.validate().unwrap();
    let (built, _) = shrf_frame(&radar.header(), &radar.png_base64).unwrap();
    let committed = fs::read(contracts().join("public/map/radar.shrf")).unwrap();
    let (built_header, built_body) = split_frame(&built);
    let (header, body) = split_frame(&committed);
    assert_eq!(built_header, header);
    assert_eq!(built_body, body);
    assert!(body.starts_with(b"\x89PNG\r\n\x1a\n"));

    let site: RadarSiteResult = typed("bridge/map/radar-site.result.json");
    site.validate().unwrap();
    assert_eq!(site.site_id, "KTLX");
    let (built, _) = shrf_frame(&site.header(), &site.png_base64).unwrap();
    let committed = fs::read(contracts().join("public/map/radar-site.shrf")).unwrap();
    let (built_header, built_body) = split_frame(&built);
    let (header, body) = split_frame(&committed);
    assert_eq!(built_header, header);
    assert_eq!(built_body, body);
    assert_eq!(built_header["kind"], "radar-site");
    assert_eq!(built_header["siteId"], "KTLX");
    assert!(body.starts_with(b"\x89PNG\r\n\x1a\n"));

    let sites: RadarSiteCatalog = typed("bridge/map/radar-sites.result.json");
    sites.validate().unwrap();
    let public = sites.into_public();
    assert_eq!(
        serde_json::to_value(&public).unwrap(),
        value("public/map/radar-sites.json")
    );

    use sharppy_reimagined_lib::dto::contours::ContourLines;
    let lines: ContourLines = bridge_to_public(
        "bridge/map/contours.result.json",
        "public/map/contours.json",
    );
    lines.validate().unwrap();
    assert_eq!(lines.product, "hgt-wind-500");
    assert_eq!(lines.interval, 6.0);
    assert_eq!(lines.unit, "dam");
    assert!(!lines.segments.is_empty());
    // The fixture height plane crosses two 6 dam levels as straight lines.
    assert!(lines.segments.len() > 10);
    let wind = lines.wind.as_ref().unwrap();
    assert_eq!((wind.width, wind.height), (128, 64));
    let mut invalid_wind = wind.clone();
    invalid_wind.width = 0;
    assert!(invalid_wind.validate().is_err());
    invalid_wind = wind.clone();
    invalid_wind.data_base64 = "AAAA".into();
    assert!(invalid_wind.validate().is_err());

    use sharppy_reimagined_lib::dto::reports::{ReportOptions, StormReports};
    let storms: StormReports =
        bridge_to_public("bridge/map/reports.result.json", "public/map/reports.json");
    storms.validate().unwrap();
    assert_eq!(storms.shown_count, 4);
    assert_eq!(storms.total_count, 4);
    assert_eq!(storms.reports.len(), 4);
    // Rank order: wind gust and damage, the significant hail, then the tornado.
    assert_eq!(storms.reports[2].symbol, "diamond");
    assert!(storms.reports[2].significant);
    assert_eq!(storms.reports[2].radius_px, 3.5);
    let options: ReportOptions = bridge_to_public(
        "bridge/map/reports-options.result.json",
        "public/map/reports-options.json",
    );
    options.validate().unwrap();
    assert_eq!(options.windows.len(), 4);
    assert_eq!(options.filters.len(), 5);

    use sharppy_reimagined_lib::dto::surface::SurfacePlots;
    let plots: SurfacePlots =
        bridge_to_public("bridge/map/surface.result.json", "public/map/surface.json");
    plots.validate().unwrap();
    assert_eq!(plots.plots.len(), 6);
    assert_eq!(plots.counts.matched, 4);
    assert_eq!(plots.counts.stale, 1);
    assert_eq!(plots.counts.no_observation, 1);
    assert_eq!(plots.counts.failed, 1);
    assert_eq!(plots.counts.nearby, 6);
    assert_eq!(plots.counts.queried, 6);
    assert_eq!(plots.counts.unqueried, 0);
    // The 20 kt staff from the west: two barbs, no pennants, pointing back
    // toward where the wind comes from.
    let koun = &plots.plots[0];
    assert_eq!(koun.id, "KOUN");
    assert_eq!(koun.wind_state, "available");
    let staff = koun.staff.as_ref().unwrap();
    assert_eq!(staff.angle_deg, 180.0);
    assert_eq!(staff.barbs, 2);
    assert_eq!(staff.pennants, 0);
    assert_eq!(staff.halves, 0);
    assert_eq!(koun.upper_text, "22");
    assert_eq!(koun.lower_text, "12");
    assert_eq!(koun.temperature_c, Some(21.5));
    assert_eq!(koun.dewpoint_c, Some(12.2));
    let stale = plots.plots.iter().find(|plot| plot.stale).unwrap();
    assert_eq!(stale.id, "KSWA");
    assert_eq!(stale.offset_min, Some(-45.0));
    for plot in &plots.plots {
        if plot.availability != "available" {
            assert!(plot.staff.is_none());
            assert_eq!(plot.valid, None);
            assert_eq!(plot.offset_min, None);
        }
    }

    use sharppy_reimagined_lib::dto::surface::{StaffSpec, SurfacePlot};
    let mut refused = plots.clone();
    refused.plots[0].temperature_c = Some(f64::NAN);
    assert!(refused.validate().is_err());
    let mut refused = plots.clone();
    refused.plots[0].staff = None;
    assert!(refused.validate().is_err());
    let mut refused = plots.clone();
    refused.plots[0].staff = Some(StaffSpec {
        angle_deg: 360.0,
        pennants: 0,
        barbs: 2,
        halves: 0,
    });
    assert!(refused.validate().is_err());
    let mut refused = plots.clone();
    refused.counts.stale += 1;
    assert!(refused.validate().is_err());
    let mut refused = plots.clone();
    refused.plots.push(SurfacePlot {
        id: "KNEW".to_owned(),
        ..refused.plots[1].clone()
    });
    assert!(refused.validate().is_err());

    use sharppy_reimagined_lib::dto::satellite::SatelliteResult;
    for (bridge, public, channel) in [
        (
            "bridge/map/satellite.result.json",
            "public/map/satellite.shrf",
            "infrared",
        ),
        (
            "bridge/map/satellite.result.visible.json",
            "public/map/satellite.visible.shrf",
            "visible",
        ),
    ] {
        let goes: SatelliteResult = typed(bridge);
        goes.validate().unwrap();
        assert_eq!(goes.satellite, "G19");
        assert_eq!(goes.product, channel);
        let (built, _) = shrf_frame(&goes.header(), &goes.png_base64).unwrap();
        let committed = fs::read(contracts().join(public)).unwrap();
        let (built_header, built_body) = split_frame(&built);
        let (header, body) = split_frame(&committed);
        assert_eq!(built_header, header, "{public}");
        assert_eq!(built_body, body, "{public}");
        assert_eq!(built_header["kind"], "satellite");
        assert_eq!(built_header["satellite"], "G19");
        assert_eq!(built_header["coverageFraction"], 0.87);
        assert!(body.starts_with(b"\x89PNG\r\n\x1a\n"));
    }
}

#[test]
fn every_public_fixture_round_trips_into_its_type() {
    let mut checked = 0;
    for name in files("public") {
        let path = format!("public/{name}");
        if name.starts_with("health.") {
            round_trip::<BackendHealth>(&path);
        } else if name.starts_with("stations.catalog.") {
            let catalog = round_trip::<StationCatalog>(&path);
            catalog.validate().unwrap_or_else(|e| panic!("{path}: {e}"));
        } else if name == "stations.search-cases.json" {
            continue; // search parity is a TypeScript and pytest concern
        } else {
            panic!("unexpected public fixture {path}");
        }
        checked += 1;
    }
    assert_eq!(checked, 9);
    for name in files("public/errors") {
        let error = round_trip::<BackendError>(&format!("public/errors/{name}"));
        assert_eq!(format!("{}.json", error.code.as_str()), name);
    }
}

fn rejects<T: DeserializeOwned>(raw: &str) -> bool {
    serde_json::from_str::<T>(raw).is_err()
}

#[test]
fn every_invalid_fixture_is_rejected() {
    let bridge = files("bridge/invalid");
    assert_eq!(bridge.len(), 5);
    for name in bridge {
        let raw = text(&format!("bridge/invalid/{name}"));
        let rejected = match name.split('.').next().unwrap() {
            "stations" => rejects::<CatalogResult>(&raw),
            "health" => rejects::<HealthResult>(&raw),
            "error" => rejects::<ErrorPayload>(&raw),
            other => panic!("unknown target {other} in bridge/invalid/{name}"),
        };
        assert!(rejected, "bridge/invalid/{name} was accepted");
    }
    let public = files("public/invalid");
    assert_eq!(public.len(), 7);
    for name in public {
        let raw = text(&format!("public/invalid/{name}"));
        let rejected = match name.split('.').next().unwrap() {
            "stations" => {
                serde_json::from_str::<StationCatalog>(&raw).map_or(true, |c| c.validate().is_err())
            }
            "health" => rejects::<BackendHealth>(&raw),
            "error" => rejects::<BackendError>(&raw),
            other => panic!("unknown target {other} in public/invalid/{name}"),
        };
        assert!(rejected, "public/invalid/{name} was accepted");
    }
}

#[test]
fn bridge_catalogues_convert_to_the_public_fixtures() {
    for (bridge, public) in [
        (
            "bridge/stations.catalog.result.sample.json",
            "public/stations.catalog.sample.json",
        ),
        (
            "bridge/stations.catalog.result.seed-fallback.json",
            "public/stations.catalog.seed-fallback.json",
        ),
    ] {
        let converted = convert::catalog(typed(bridge));
        assert_eq!(
            serde_json::to_value(&converted).unwrap(),
            value(public),
            "{bridge}"
        );
    }
}

#[test]
fn bridge_health_converts_to_the_public_fixtures_with_fixed_app_facts() {
    let hello: HelloResult = typed("bridge/hello.result.json");
    for case in ["ok", "python-fallback"] {
        let expected: BackendHealth = typed(&format!("public/health.{case}.json"));
        let bridge: HealthResult = typed(&format!("bridge/health.result.{case}.json"));
        let backend: ScientificBackendStatus =
            convert::scientific_backend(bridge.scientific_backend);
        let converted = convert::health(
            expected.app.clone(),
            expected.bridge.clone(),
            Some(&hello),
            Some(backend),
            expected.checked_at.clone(),
            true,
        );
        assert_eq!(
            serde_json::to_value(&converted).unwrap(),
            value(&format!("public/health.{case}.json")),
            "health.{case}"
        );
    }
}

fn code_names<T: Serialize>(codes: &[T]) -> BTreeSet<String> {
    codes
        .iter()
        .map(|c| {
            serde_json::to_value(c)
                .unwrap()
                .as_str()
                .unwrap()
                .to_owned()
        })
        .collect()
}

#[test]
fn error_code_sets_equal_their_fixture_file_sets() {
    let public: BTreeSet<String> = files("public/errors")
        .iter()
        .map(|n| n.trim_end_matches(".json").to_owned())
        .collect();
    assert_eq!(code_names(&ErrorCode::ALL), public);
    assert!(ErrorCode::ALL
        .iter()
        .all(|c| code_names(&[*c]).contains(c.as_str())));
    let bridge: BTreeSet<String> = files("bridge")
        .iter()
        .filter_map(|n| {
            n.strip_prefix("error.")
                .and_then(|n| n.strip_suffix(".json"))
                .map(str::to_owned)
        })
        .collect();
    assert_eq!(code_names(&BridgeErrorCode::ALL), bridge);
    for code in BridgeErrorCode::ALL {
        assert_eq!(code_names(&[code]), code_names(&[code.to_error_code()]));
    }
}

#[test]
fn golden_catalogue_holds_the_whole_bundled_file() {
    let catalog: StationCatalog = typed("public/stations.catalog.full.json");
    assert_eq!(catalog.stations.len(), 933);
    catalog.validate().unwrap();
}

#[test]
fn the_frame_body_starts_on_an_eight_byte_boundary() {
    use sharppy_reimagined_lib::backend::map_ops::shrf_frame;
    for header in ["a", "ab", "abcdefgh", "abcdefghijk"] {
        let (frame, body_offset) = shrf_frame(&header, "AQID").unwrap();
        let length = u32::from_le_bytes([frame[8], frame[9], frame[10], frame[11]]) as usize;
        assert_eq!(&frame[..4], b"SHRF");
        assert!((12 + length).is_multiple_of(8));
        assert_eq!(body_offset, 12 + length);
        assert_eq!(&frame[12 + length..], &[1, 2, 3]);
        let json: String = serde_json::from_slice(&frame[12..12 + length]).unwrap();
        assert_eq!(json, header);
    }
}

#[test]
fn direct_decode_preserves_every_padding_length_and_rejects_noncanonical_base64() {
    use base64::Engine as _;
    use sharppy_reimagined_lib::backend::map_ops::shrf_frame;
    for body in [
        &[][..],
        &[1][..],
        &[1, 2][..],
        &[1, 2, 3][..],
        &[1, 2, 3, 4][..],
    ] {
        let encoded = base64::engine::general_purpose::STANDARD.encode(body);
        let (frame, body_offset) =
            shrf_frame(&serde_json::json!({"label": "雷"}), &encoded).unwrap();
        assert_eq!(&frame[body_offset..], body);
        assert!(body_offset.is_multiple_of(8));
    }
    for encoded in [
        "!", "A", "AQ", "AQI", "AQ===", "AQ==AA==", "AA=A", "AB==", "AAB=", "AQID\n",
    ] {
        assert!(
            shrf_frame(&(), encoded).unwrap_err().starts_with("base64:"),
            "{encoded:?}"
        );
    }
}
