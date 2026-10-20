use super::*;

#[test]
fn open_requests_deserialize_camel_case_and_map_to_bridge_calls() {
    let observed: OpenSoundingRequest = serde_json::from_str(
        r#"{"kind":"observed","stationId":" 72357 ","time":"2026-10-04T12:00:00Z"}"#,
    )
    .unwrap();
    observed.validate().unwrap();
    let (method, params) = observed.bridge_call();
    assert_eq!(method, "soundings.open_observed");
    assert_eq!(params["station_id"], "72357");
    assert_eq!(params["provider"], "auto");
    let model: OpenSoundingRequest = serde_json::from_str(
            r#"{"kind":"model","model":"hrrr","runTime":null,"forecastHour":3,"latitude":35.2,"longitude":-97.4,"location":"OUN"}"#,
        )
        .unwrap();
    model.validate().unwrap();
    assert_eq!(model.bridge_call().1["forecast_hour"], 3);
    let era5: OpenSoundingRequest = serde_json::from_str(
            r#"{"kind":"era5","time":"2018-09-01T05:00:00Z","latitude":35.22,"longitude":-97.44,"location":"OUN"}"#,
        ).unwrap();
    era5.validate().unwrap();
    assert_eq!(era5.bridge_call().0, "soundings.open_era5");
    assert_eq!(era5.bridge_call().1["time"], "2018-09-01T05:00:00Z");
    assert_eq!(era5.bridge_call().1["location"], "OUN");
    assert_eq!(serde_json::to_value(SoundingKind::Era5).unwrap(), "era5");
    for bad in [
        r#"{"kind":"observed","stationId":"","time":"2026-10-04T12:00:00Z"}"#,
        r#"{"kind":"model","model":"hrrr","forecastHour":3,"latitude":95,"longitude":0}"#,
        r#"{"kind":"era5","time":"2018-09-01T05:00:00Z","latitude":95,"longitude":0}"#,
        r#"{"kind":"file","fileName":"a.txt","contentBase64":""}"#,
    ] {
        let parsed: OpenSoundingRequest = serde_json::from_str(bad).unwrap();
        assert!(parsed.validate().is_err(), "{bad}");
    }
    assert!(serde_json::from_str::<OpenSoundingRequest>(
        r#"{"kind":"observed","stationId":"1","time":"t","extra":1}"#
    )
    .is_err());
}

#[test]
fn summaries_read_snake_case_and_write_camel_case() {
    let summary: SoundingSummary = serde_json::from_str(
            r#"{"sounding_id":"a1","kind":"file","title":"T","location":null,"station_id":null,
                "station_name":null,"latitude":35.0,"longitude":-97.0,"valid_time":"2026-06-26T00:00:00Z",
                "run_time":null,"forecast_hour":null,"model":"HRRR","provider":null,"provider_name":null,
                "source_url":null,"file_name":"x.npz","level_count":39,"time_count":1}"#,
        )
        .unwrap();
    summary.validate().unwrap();
    let out = serde_json::to_value(&summary).unwrap();
    assert_eq!(out["soundingId"], "a1");
    assert_eq!(out["levelCount"], 39);
    assert!(out.get("sounding_id").is_none());
    let mut multi = summary;
    multi.time_count = 2;
    multi.times = vec![Some("2026-06-26T00:00:00Z".into()), None];
    multi.validate().unwrap();
    multi.times[0] = Some("invalid date".into());
    assert!(multi.validate().is_err());
    multi.times.clear();
    multi.times.push(None);
    assert!(multi.validate().is_err());
}
