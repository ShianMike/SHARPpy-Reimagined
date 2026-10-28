use super::*;

const HEADER: &str = "VALID,LAT,LON,MAG,WFO,TYPECODE,CITY,COUNTY,UGCNAME,STATE,SOURCE,REMARK\n";

#[test]
fn parses_valid_hazards_and_preserves_units_order_quotes_and_multiline_metadata() {
    let rows = concat!(
        "202609070450,47.71,-108.72,3.25,GGW,H,Landusky,old,Phillips,MT,Public,Baseball hail\n",
        "202609070519,44.05,-104.54,54,UNR,G,Upton,Weston,,WY,Public,\n",
        "202609070530,35.22,-97.44,,OUN,t,Norman,Cleveland,,OK,Spotter,\"quoted, text\nwith \"\"hail\"\"\"\n",
        "202609070600,35.22,-97.44,inf,OUN,D,Norman,Cleveland,,OK,Public,Damage\n",
        "202609070600,35.22,-97.44,60,OUN,M,Norman,Cleveland,,OK,Public,Marine\n"
    );
    let parsed = parse(&format!("{HEADER}{rows}"));
    assert_eq!(parsed.len(), 4);
    assert_eq!(
        parsed
            .iter()
            .map(|row| row.code.as_str())
            .collect::<Vec<_>>(),
        ["H", "G", "T", "D"]
    );
    assert_eq!(parsed[0].magnitude, Some(3.25));
    assert_eq!(parsed[0].county, "Phillips");
    assert_eq!(parsed[1].county, "Weston");
    assert_eq!(parsed[2].magnitude, None);
    assert_eq!(parsed[3].magnitude, None);
    assert_eq!(parsed[2].remark, "quoted, text\nwith \"hail\"");
    assert_eq!(
        crate::format_rfc3339_utc(parsed[2].valid),
        "2026-09-07T05:30:00Z"
    );
}

#[test]
fn discards_only_rows_with_bad_position_time_or_hazard() {
    let data = format!(
        "{HEADER}{}",
        concat!(
            "bad,35,-97,,OUN,H\n",
            "202602300500,35,-97,,OUN,H\n",
            "000009070500,35,-97,,OUN,H\n",
            "-000109070500,35,-97,,OUN,H\n",
            "202609070500,NaN,-97,,OUN,H\n",
            "202609070500,91,-97,,OUN,H\n",
            "202609070500,35,-181,,OUN,H\n",
            "202609070500,35,-97,,OUN,H\n"
        )
    );
    assert_eq!(parse(&data).len(), 1);
    assert!(parse("").is_empty());
    assert!(parse("TYPECODE,LAT,LON\nH,35,-97\n").is_empty());
}

#[test]
fn uses_last_duplicate_header_and_allows_extra_or_missing_optional_columns() {
    let rows =
        parse("VALID,LAT,LON,TYPECODE,MAG,MAG\n202609070500,35,-97,H,1,2\n202609070600,35,-97,T\n");
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].magnitude, Some(2.));
    assert_eq!(rows[1].magnitude, None);
    assert!(rows[1].remark.is_empty());
}

#[tokio::test]
async fn rejects_untrusted_targets_and_transfer_limits_before_network() {
    let client = ReportsClient::default();
    for url in [
        "http://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py",
        "https://mesonet.agron.iastate.edu.evil.example/cgi-bin/request/gis/lsr.py",
        "https://user@mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py",
        "https://mesonet.agron.iastate.edu:444/cgi-bin/request/gis/lsr.py",
        "https://mesonet.agron.iastate.edu/other",
    ] {
        assert!(client
            .fetch(url, 20., MAX_BYTES)
            .await
            .unwrap_err()
            .contains("URL"));
    }
    let url = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py?recent=3600&justcsv=1";
    assert!(client.fetch(url, 20., MAX_BYTES + 1).await.is_err());
    assert!(client.fetch(url, f64::NAN, MAX_BYTES).await.is_err());
}

#[tokio::test]
#[ignore = "live IEM CSV schema and transport; run explicitly"]
async fn fetches_live_iem_report_schema() {
    let url = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py?recent=86400&justcsv=1";
    let bytes = ReportsClient::default()
        .fetch(url, 20., MAX_BYTES)
        .await
        .unwrap();
    let text = String::from_utf8(bytes).unwrap();
    assert!(text
        .lines()
        .next()
        .is_some_and(|header| header.contains("VALID") && header.contains("TYPECODE")));
    let reports = parse(&text);
    println!(
        "Native IEM fetch: {} bytes, {} convective reports",
        text.len(),
        reports.len()
    );
}
