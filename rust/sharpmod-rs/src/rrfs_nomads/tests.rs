use super::idx::{match_known_search, parse_idx_rows, IdxParseError};
use super::provenance::{provenance_sidecar_path, read_provenance_data, write_provenance_file};
use super::{forget_release_value, lookup_domain, release_candidates_list, remember_release_value};
use crate::model_planning::{build_noaa_search_str, NOAA_SURFACE_SEARCH};
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn domain_normalization_and_release_order() {
    let pr = lookup_domain("Puerto_Rico").expect("puerto rico");
    assert_eq!(pr.tag, "pr");
    assert_eq!(pr.resolution, "2p5km");
    let na = lookup_domain("north-america").expect("north america");
    assert_eq!(na.tag, "na");
    assert_eq!(na.resolution, "13km");
    assert!(lookup_domain("unknown").is_none());

    forget_release_value();
    assert_eq!(release_candidates_list(), vec!["prod", "para", "v1.0"]);
    assert!(remember_release_value("para".to_string()));
    assert!(!remember_release_value("para".to_string()));
    assert_eq!(release_candidates_list(), vec!["para", "prod", "v1.0"]);
    forget_release_value();
}

#[test]
fn idx_parsing_sorts_out_of_order_offsets_and_detects_bad_lengths() {
    let idx = "\
2:200:d=2026101200:TMP:500 mb:18 hour fcst:\n\
1:0:d=2026101200:HGT:500 mb:18 hour fcst:\n\
3:450:d=2026101200:UGRD:500 mb:18 hour fcst:\n\
4:450:d=2026101200:VGRD:500 mb:18 hour fcst:\n";
    let records = parse_idx_rows(idx, 700).expect("parsed records");
    assert_eq!(records.len(), 4);
    assert_eq!(records[0].start, 0);
    assert_eq!(records[0].end, 199);
    assert_eq!(records[1].start, 200);
    assert_eq!(records[1].end, 449);
    assert_eq!(records[2].start, 450);
    assert_eq!(records[2].end, 699);
    assert_eq!(records[3].end, 699);

    assert_eq!(parse_idx_rows("", 700), Err(IdxParseError::NoRecords));
    assert_eq!(parse_idx_rows(idx, 0), Err(IdxParseError::UnusableLength));
    assert_eq!(
        parse_idx_rows(idx, 450),
        Err(IdxParseError::UnusableRecordRange(3))
    );
}

#[test]
fn fast_search_matcher_matches_generated_noaa_and_surface_patterns() {
    let lines = [
        "1:0:d=2026101200:TMP:500 mb:18 hour fcst:",
        "2:100:d=2026101200:DZDT:500 mb:18 hour fcst:",
        "3:200:d=2026101200:TMP:2 m above ground:18 hour fcst:",
        "4:300:d=2026101200:PRES:surface:18 hour fcst:",
    ];
    let search = build_noaa_search_str(&[
        "HGT".into(),
        "TMP".into(),
        "UGRD".into(),
        "VGRD".into(),
        "RH".into(),
    ]);
    let matched = match_known_search(&search, &lines).expect("known pattern");
    assert_eq!(matched, vec![0, 2, 3]);

    let surf_matched = match_known_search(NOAA_SURFACE_SEARCH, &lines).expect("surface");
    assert_eq!(surf_matched, vec![2, 3]);
}

#[test]
fn provenance_round_trip_and_invalid_payloads() {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("sharpmod_rrfs_prov_{nanos}"));
    let combined = dir.join("rrfs-test.grib2");
    let written = write_provenance_file(
        &combined,
        &["HGT".into(), "TMP".into(), "PRES".into()],
        "https://nomads.example/prslev.grib2",
        &["HGT".into(), "TMP".into()],
    )
    .expect("write provenance");
    assert_eq!(written, provenance_sidecar_path(&combined));

    let read_back = read_provenance_data(&combined).expect("valid provenance");
    assert_eq!(read_back.0, vec!["HGT", "TMP", "PRES"]);
    assert_eq!(read_back.1, vec!["HGT", "TMP"]);
    assert_eq!(read_back.2, "https://nomads.example/prslev.grib2");

    std::fs::write(&written, br#"{"version": 2, "fields": ["HGT"]}"#).expect("corrupt");
    assert!(read_provenance_data(&combined).is_none());
    let _ = std::fs::remove_dir_all(&dir);
}
