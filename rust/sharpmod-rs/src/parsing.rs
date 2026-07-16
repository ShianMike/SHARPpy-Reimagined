//! Parser for a deliberately small six-column sounding-row representation.

// PyO3 emits cumulative interpreter cfgs such as `Py_3_12` from its build
// script, but Cargo does not automatically register those names for check-cfg.
#![allow(unexpected_cfgs)]

use std::borrow::Cow;

const COLUMN_COUNT: usize = 6;

// Zero code points for every Unicode 14.0 General_Category=Nd block. Python
// normalizes these decimal digits before parsing a float, including in the
// exponent and on either side of an underscore. Rust's `f64::from_str` only
// accepts ASCII digits, so the native parser performs that normalization
// explicitly. Do not replace this table with `char::is_numeric`: that broader
// predicate also accepts Nl/No characters (for example Roman numerals and
// superscripts) that Python's `float()` rejects.
const UNICODE_DECIMAL_ZEROS: &[u32] = &[
    0x0030, 0x0660, 0x06f0, 0x07c0, 0x0966, 0x09e6, 0x0a66, 0x0ae6, 0x0b66, 0x0be6, 0x0c66, 0x0ce6,
    0x0d66, 0x0de6, 0x0e50, 0x0ed0, 0x0f20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80,
    0x1a90, 0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0,
    0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0,
    0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x16a60, 0x16ac0,
    0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e950, 0x1fbf0,
];

// CPython 3.12 moved to Unicode 15.0, which added these two Nd blocks. PyO3
// emits cumulative interpreter-version cfgs, so each native wheel mirrors the
// Unicode decimal table used by the Python minor version it targets.
#[cfg(Py_3_12)]
const UNICODE_15_ADDITIONAL_DECIMAL_ZEROS: &[u32] = &[0x11f50, 0x1e4f0];

fn python_decimal_value(character: char) -> Option<u32> {
    let codepoint = character as u32;
    let insertion = UNICODE_DECIMAL_ZEROS.partition_point(|zero| *zero <= codepoint);
    if insertion > 0 {
        let zero = UNICODE_DECIMAL_ZEROS[insertion - 1];
        let value = codepoint - zero;
        if value < 10 {
            return Some(value);
        }
    }

    #[cfg(Py_3_12)]
    for zero in UNICODE_15_ADDITIONAL_DECIMAL_ZEROS {
        let value = codepoint.wrapping_sub(*zero);
        if value < 10 {
            return Some(value);
        }
    }
    None
}

fn is_python_whitespace(character: char) -> bool {
    // Python's str.isspace()/strip()/split() retain four legacy ASCII
    // information separators in addition to Unicode White_Space. Rust follows
    // Unicode White_Space and therefore needs these C0 controls explicitly.
    character.is_whitespace() || matches!(character, '\u{001c}'..='\u{001f}')
}

fn python_trim(value: &str) -> &str {
    value.trim_matches(is_python_whitespace)
}

fn split_columns(line: &str) -> Vec<&str> {
    if line.contains(',') {
        line.split(',').map(python_trim).collect()
    } else {
        line.split(is_python_whitespace)
            .filter(|value| !value.is_empty())
            .collect()
    }
}

fn is_python_line_boundary(character: char) -> bool {
    matches!(
        character,
        '\n' | '\r'
            | '\u{000b}'
            | '\u{000c}'
            | '\u{001c}'
            | '\u{001d}'
            | '\u{001e}'
            | '\u{0085}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// Split text like Python's `str.splitlines()`, including its Unicode line
/// boundaries and its treatment of CRLF as one separator.
fn python_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut characters = text.char_indices().peekable();

    while let Some((index, character)) = characters.next() {
        if !is_python_line_boundary(character) {
            continue;
        }

        lines.push(&text[start..index]);
        let mut next_start = index + character.len_utf8();
        if character == '\r' {
            if let Some(&(next_index, '\n')) = characters.peek() {
                characters.next();
                next_start = next_index + '\n'.len_utf8();
            }
        }
        start = next_start;
    }

    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

fn is_canonical_header(columns: &[&str]) -> bool {
    if columns.len() != COLUMN_COUNT {
        return false;
    }

    let normalized: Vec<String> = columns
        .iter()
        .map(|value| python_trim(value).to_ascii_lowercase())
        .collect();

    normalized == ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]
}

fn explicit_nonfinite(token: &str) -> bool {
    matches!(
        python_trim(token).to_ascii_lowercase().as_str(),
        "nan" | "+nan" | "-nan" | "inf" | "+inf" | "-inf" | "infinity" | "+infinity" | "-infinity"
    )
}

fn python_float_literal(token: &str) -> Result<Cow<'_, str>, ()> {
    let characters: Vec<char> = token.chars().collect();
    let has_non_ascii_decimal = characters
        .iter()
        .any(|character| !character.is_ascii() && python_decimal_value(*character).is_some());
    if !token.contains('_') && !has_non_ascii_decimal {
        return Ok(Cow::Borrowed(token));
    }

    for (index, character) in characters.iter().enumerate() {
        if *character != '_' {
            continue;
        }
        let between_decimal_digits = index > 0
            && index + 1 < characters.len()
            && python_decimal_value(characters[index - 1]).is_some()
            && python_decimal_value(characters[index + 1]).is_some();
        if !between_decimal_digits {
            return Err(());
        }
    }

    let mut normalized = String::with_capacity(token.len());
    for character in characters {
        if character == '_' {
            continue;
        }
        if let Some(value) = python_decimal_value(character) {
            normalized.push(char::from_digit(value, 10).expect("decimal digit is in 0..=9"));
        } else {
            normalized.push(character);
        }
    }
    Ok(Cow::Owned(normalized))
}

fn parse_value(token: &str, missing: f64, line: usize) -> Result<f64, String> {
    let token = python_trim(token);
    if token.is_empty() || explicit_nonfinite(token) {
        return Ok(missing);
    }

    let literal = python_float_literal(token)
        .map_err(|()| format!("line {line}: nonnumeric value '{token}'"))?;
    let value = literal
        .parse::<f64>()
        .map_err(|_| format!("line {line}: nonnumeric value '{token}'"))?;
    if !value.is_finite() || value == missing {
        Ok(missing)
    } else {
        Ok(value)
    }
}

/// Parse comma- or whitespace-delimited six-column sounding rows.
///
/// Columns are pressure, height, temperature, dewpoint, wind direction, and
/// wind speed.  Blank lines and whole-line comments are ignored.  One canonical
/// header may precede the data.  Missing comma fields, non-finite values, and
/// values exactly equal to `missing` are normalized to `missing`.  Input order
/// and duplicate pressures are preserved.
pub fn parse_sounding_rows(text: &str, missing: f64) -> Result<Vec<[f64; COLUMN_COUNT]>, String> {
    let mut rows = Vec::new();
    let mut header_seen = false;

    for (zero_based_line, raw_line) in python_splitlines(text).into_iter().enumerate() {
        let line_number = zero_based_line + 1;
        let line = python_trim(raw_line);
        if line.is_empty()
            || line.starts_with('#')
            || line.starts_with(';')
            || line.starts_with("//")
        {
            continue;
        }

        let columns = split_columns(line);
        if rows.is_empty() && !header_seen && is_canonical_header(&columns) {
            header_seen = true;
            continue;
        }
        if columns.len() != COLUMN_COUNT {
            return Err(format!(
                "line {line_number}: expected {COLUMN_COUNT} columns, got {}",
                columns.len()
            ));
        }

        let mut row = [missing; COLUMN_COUNT];
        for (index, token) in columns.iter().enumerate() {
            row[index] = parse_value(token, missing, line_number)?;
        }
        rows.push(row);
    }

    if rows.is_empty() {
        Err("no sounding rows were found".to_owned())
    } else {
        Ok(rows)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const MISSING: f64 = -9999.0;

    #[test]
    fn parses_comma_rows_and_preserves_order_and_duplicates() {
        let rows = parse_sounding_rows(
            "1000, 100, 20, 15, 180, 10\n900,1000,10,5,220,30\n900,1005,9,4,225,31",
            MISSING,
        )
        .unwrap();

        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0], [1000.0, 100.0, 20.0, 15.0, 180.0, 10.0]);
        assert_eq!(rows[1][0], 900.0);
        assert_eq!(rows[2][0], 900.0);
    }

    #[test]
    fn parses_whitespace_rows() {
        let rows =
            parse_sounding_rows("1000 100 20 15 180 10\n900 1000 10 5 220 30", MISSING).unwrap();

        assert_eq!(
            rows,
            vec![
                [1000.0, 100.0, 20.0, 15.0, 180.0, 10.0],
                [900.0, 1000.0, 10.0, 5.0, 220.0, 30.0],
            ]
        );
    }

    #[test]
    fn accepts_every_python_intraline_whitespace_character() {
        // Python's splitlines() consumes the remaining whitespace characters
        // that are line boundaries; these are every isspace() character that
        // can still reach line.split() as a column delimiter.
        for separator in [
            '\t', '\u{001f}', ' ', '\u{00a0}', '\u{1680}', '\u{2000}', '\u{2001}', '\u{2002}',
            '\u{2003}', '\u{2004}', '\u{2005}', '\u{2006}', '\u{2007}', '\u{2008}', '\u{2009}',
            '\u{200a}', '\u{202f}', '\u{205f}', '\u{3000}',
        ] {
            let text = ["1000", "100", "20", "15", "180", "10"].join(&separator.to_string());
            let rows = parse_sounding_rows(&text, MISSING).unwrap();
            assert_eq!(
                rows[0],
                [1000.0, 100.0, 20.0, 15.0, 180.0, 10.0],
                "separator U+{:04X}",
                separator as u32
            );
        }
    }

    #[test]
    fn strips_python_unit_separator_around_comma_fields_and_comments() {
        let rows = parse_sounding_rows(
            "\u{001f}# ignored\u{001f}\n\u{001f}1000\u{001f},\u{001f}100\u{001f},20,15,180,\u{001f}10\u{001f}",
            MISSING,
        )
        .unwrap();

        assert_eq!(rows[0], [1000.0, 100.0, 20.0, 15.0, 180.0, 10.0]);
    }

    #[test]
    fn accepts_the_canonical_header_case_insensitively() {
        let core = parse_sounding_rows(
            "pres,hght,tmpc,dwpc,wdir,wspd\n1000,100,20,15,180,10",
            MISSING,
        )
        .unwrap();
        let upper = parse_sounding_rows(
            "PRES HGHT TMPC DWPC WDIR WSPD\n1000 100 20 15 180 10",
            MISSING,
        )
        .unwrap();

        assert_eq!(core, upper);
    }

    #[test]
    fn skips_blank_and_comment_lines() {
        let rows = parse_sounding_rows(
            "\n# source sounding\n; another comment\n// generated row\n1000 100 20 15 180 10\n",
            MISSING,
        )
        .unwrap();

        assert_eq!(rows.len(), 1);
    }

    #[test]
    fn normalizes_blank_nonfinite_and_exact_sentinel_values() {
        let rows = parse_sounding_rows("1000,,NaN,Infinity,-inf,-9999", MISSING).unwrap();

        assert_eq!(
            rows[0],
            [1000.0, MISSING, MISSING, MISSING, MISSING, MISSING]
        );
    }

    #[test]
    fn reports_wrong_column_count_with_line_number() {
        let error = parse_sounding_rows("# comment\n1000,100,20,15,180", MISSING).unwrap_err();

        assert_eq!(error, "line 2: expected 6 columns, got 5");
    }

    #[test]
    fn reports_nonnumeric_tokens_like_the_python_backend() {
        let error = parse_sounding_rows("1000,100,twenty,15,180,10", MISSING).unwrap_err();

        assert_eq!(error, "line 1: nonnumeric value 'twenty'");
    }

    #[test]
    fn accepts_valid_python_float_underscore_literals() {
        let rows = parse_sounding_rows("1_000,1_0,2_0.5,.1_5,1_8_0,1e1_0", MISSING).unwrap();

        assert_eq!(rows[0], [1000.0, 10.0, 20.5, 0.15, 180.0, 1.0e10]);
    }

    #[test]
    fn accepts_python_unicode_decimal_digits_in_all_float_positions() {
        let rows = parse_sounding_rows("١٢٣.٤٥,१_२,１e２,𝟙𝟚.𝟛,১২,༡༢", MISSING).unwrap();

        assert_eq!(rows[0], [123.45, 12.0, 100.0, 12.3, 12.0, 12.0]);
    }

    #[test]
    fn accepts_every_runtime_unicode_decimal_digit_block() {
        assert_eq!(UNICODE_DECIMAL_ZEROS.len(), 66);
        for zero in UNICODE_DECIMAL_ZEROS {
            let one = char::from_u32(zero + 1).unwrap();
            let two = char::from_u32(zero + 2).unwrap();
            let token = format!("{one}_{two}");
            let text = format!("{token},100,20,15,180,10");
            let rows = parse_sounding_rows(&text, MISSING).unwrap();
            assert_eq!(rows[0][0], 12.0, "decimal block starts at U+{zero:04X}");
        }

        #[cfg(Py_3_12)]
        for zero in UNICODE_15_ADDITIONAL_DECIMAL_ZEROS {
            let one = char::from_u32(zero + 1).unwrap();
            let two = char::from_u32(zero + 2).unwrap();
            let token = format!("{one}_{two}");
            let text = format!("{token},100,20,15,180,10");
            let rows = parse_sounding_rows(&text, MISSING).unwrap();
            assert_eq!(rows[0][0], 12.0, "decimal block starts at U+{zero:04X}");
        }
    }

    #[cfg(Py_3_12)]
    #[test]
    fn accepts_unicode_15_kawi_and_nag_mundari_digits() {
        let rows = parse_sounding_rows("𑽑𑽒,𞓱𞓲,20,15,180,10", MISSING).unwrap();
        assert_eq!(rows[0][..2], [12.0, 12.0]);
    }

    #[cfg(not(Py_3_12))]
    #[test]
    fn rejects_unicode_15_digits_before_python_3_12() {
        for token in ["𑽑", "𞓱"] {
            let text = format!("{token},100,20,15,180,10");
            assert_eq!(
                parse_sounding_rows(&text, MISSING).unwrap_err(),
                format!("line 1: nonnumeric value '{token}'")
            );
        }
    }

    #[test]
    fn rejects_unicode_numeric_characters_that_are_not_decimal_digits() {
        for token in ["Ⅰ", "²", "四", "൰"] {
            let text = format!("{token},100,20,15,180,10");
            assert_eq!(
                parse_sounding_rows(&text, MISSING).unwrap_err(),
                format!("line 1: nonnumeric value '{token}'")
            );
        }
    }

    #[test]
    fn rejects_invalid_python_float_underscore_placement() {
        for token in ["_1000", "1000_", "1__000", "1_.0", "1._0", "1e_2", "1_e2"] {
            let text = format!("{token},100,20,15,180,10");
            assert_eq!(
                parse_sounding_rows(&text, MISSING).unwrap_err(),
                format!("line 1: nonnumeric value '{token}'")
            );
        }
    }

    #[test]
    fn uses_python_splitlines_boundaries() {
        for separator in [
            "\n", "\r", "\r\n", "\u{000b}", "\u{000c}", "\u{001c}", "\u{001d}", "\u{001e}",
            "\u{0085}", "\u{2028}", "\u{2029}",
        ] {
            let text = format!("1000,100,20,15,180,10{separator}900,1000,12,8,200,20");
            let rows = parse_sounding_rows(&text, MISSING).unwrap();
            assert_eq!(rows.len(), 2, "separator {separator:?}");
        }
    }

    #[test]
    fn header_after_data_is_not_silently_accepted() {
        let error = parse_sounding_rows(
            "1000,100,20,15,180,10\npres,hght,tmpc,dwpc,wdir,wspd",
            MISSING,
        )
        .unwrap_err();

        assert_eq!(error, "line 2: nonnumeric value 'pres'");
    }

    #[test]
    fn rejects_input_without_data_rows() {
        assert_eq!(
            parse_sounding_rows("\n# only comments\n", MISSING).unwrap_err(),
            "no sounding rows were found"
        );
        assert_eq!(
            parse_sounding_rows("pres,hght,tmpc,dwpc,wdir,wspd", MISSING).unwrap_err(),
            "no sounding rows were found"
        );
    }
}
