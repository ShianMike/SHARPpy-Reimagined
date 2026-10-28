use super::*;

fn record<const N: usize>(values: [f64; N], outcome: f64) -> Record<N> {
    Record {
        name: "test".into(),
        outcome,
        classification: outcome.to_string(),
        values,
    }
}

#[test]
fn hail_preserves_loose_probability_quality_order_and_display_limit() {
    let input = [15., 2000., -10., 7., 20., 30., 15., 100.];
    let mut records: Vec<_> = (0..20)
        .map(|index| record(input, if index < 10 { 2. } else { 1. }))
        .collect();
    let mut loose_only = input;
    loose_only[2] = -5.;
    records.push(record(loose_only, 2.));
    let result = hail(&records, input);
    assert_eq!(result.quality, (0..15).collect::<Vec<_>>());
    assert_eq!((result.loose, result.significant), (21, 11));
    assert_eq!(result.probability, 11. / 21.);

    let mut missing_srh = input;
    missing_srh[7] = f64::NAN;
    let result = hail(&records, missing_srh);
    assert_eq!(result.loose, 21);
    assert!(result.quality.is_empty());
    let zero = [15., 0., -10., 7., 20., 30., 15., 100.];
    let result = hail(&[record(zero, 2.)], zero);
    assert_eq!(result.loose, 1);
    assert_eq!(result.probability, 0.);
}

#[test]
fn supercell_keeps_signed_srh_and_independent_missing_quality_fields() {
    let input = [2000., 1000., -10., 7., 40., -100., 30., 60., -150.];
    let result = supercell(&[record(input, 2.)], input);
    assert_eq!(result.quality, [0]);
    assert_eq!(
        (result.loose, result.significant, result.probability),
        (0, 0, 0.)
    );

    let mut positive = input;
    positive[5] = 100.;
    let records = [record(positive, 2.), record(positive, 0.)];
    let result = supercell(&records, positive);
    assert_eq!(
        (result.loose, result.significant, result.probability),
        (2, 1, 0.5)
    );
    positive[6..].fill(f64::NAN);
    let result = supercell(&records, positive);
    assert_eq!(result.loose, 2);
    assert!(result.quality.is_empty());
    positive[0] = f64::NAN;
    assert_eq!(supercell(&records, positive).loose, 0);
}

#[test]
fn thresholds_are_inclusive_with_the_original_subtract_then_compare_rounding() {
    let upper = 0.1_f64 + 0.2;
    assert!(within(&[upper], &[0.1], &[0.2]));
    assert!(!within(
        &[f64::from_bits(upper.to_bits() + 1)],
        &[0.1],
        &[0.2]
    ));
    assert!(!within(&[f64::NAN], &[0.6], &[0.4]));
}

#[test]
fn parses_the_published_columns_and_reuses_only_current_source_data() {
    let row = "95052300.DDC 791 6.00 4181 15.3 -9.6 -36.2 7.5 7.1 23.4 19.4 26.8 325 1.9 3.0";
    let text = format!("header\n{row}\n{row}\n");
    let records = parse(&text, [4, 3, 5, 7, 10, 11, 9, 12], 2, "#").unwrap();
    assert_eq!(
        records[0].values,
        [15.3, 4181., -9.6, 7.5, 19.4, 26.8, 23.4, 325.]
    );
    assert_eq!(records[0].outcome, 6.);
    assert!(parse(&text.replace("15.3", "invalid"), [4], 2, "#").is_err());
    assert!(parse("header\nshort row\n", [0], 1, "#").is_err());
    let shorter = row
        .split_whitespace()
        .take(13)
        .collect::<Vec<_>>()
        .join(" ");
    assert!(parse(
        &format!("header\n{shorter}\n{shorter}\n"),
        [4, 3, 5, 7, 10, 11, 9, 12],
        2,
        "#"
    )
    .is_ok());
    assert!(parse(&format!("header\n{shorter}\n{row}\n"), [4], 2, "#").is_err());

    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path =
        std::env::temp_dir().join(format!("sharpmod-sars-{}-{nonce}.txt", std::process::id()));
    std::fs::write(&path, &text).unwrap();
    let first = catalogue::hail(&path).unwrap();
    assert!(std::sync::Arc::ptr_eq(
        &first,
        &catalogue::hail(&path).unwrap()
    ));
    let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
    let changed = text.replace("6.00", "5.00");
    std::fs::write(&path, &changed).unwrap();
    std::fs::File::options()
        .write(true)
        .open(&path)
        .unwrap()
        .set_modified(modified + std::time::Duration::from_secs(1))
        .unwrap();
    assert_eq!(catalogue::hail(&path).unwrap()[0].outcome, 5.);
    std::fs::write(&path, format!("{changed}{row}\n")).unwrap();
    let updated = catalogue::hail(&path).unwrap();
    assert!(!std::sync::Arc::ptr_eq(&first, &updated));
    assert_eq!(updated.len(), 3);
    std::fs::remove_file(&path).unwrap();
    assert!(catalogue::hail(&path).is_err());
}
