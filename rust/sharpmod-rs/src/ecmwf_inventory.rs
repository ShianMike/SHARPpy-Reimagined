//! ECMWF JSON-lines addresses translated to the shared forecast field names.

use crate::parsing::{python_float_literal, python_splitlines};
use serde_json::Value;

pub struct InventoryRow {
    pub index: usize,
    pub offset: u64,
    pub length: u64,
    pub variable: &'static str,
    pub level: String,
    pub forecast: String,
}

fn integer(value: &Value) -> Result<i128, ()> {
    match value {
        Value::Bool(value) => Ok(i128::from(*value)),
        Value::String(value) => python_float_literal(value)?.trim().parse().map_err(|_| ()),
        Value::Number(value) => {
            if let Some(value) = value.as_i64() {
                return Ok(i128::from(value));
            }
            if let Some(value) = value.as_u64() {
                return Ok(i128::from(value));
            }
            let value = value.as_f64().ok_or(())?;
            if value.is_finite() && value >= i128::MIN as f64 && value < i128::MAX as f64 {
                Ok(value.trunc() as i128)
            } else {
                Err(())
            }
        }
        _ => Err(()),
    }
}

pub fn parse_inventory(text: &str) -> Result<Vec<InventoryRow>, ()> {
    let mut rows = Vec::new();
    for (index, line) in python_splitlines(text).into_iter().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let item: Value = serde_json::from_str(line).map_err(|_| ())?;
        let param = item.get("param").ok_or(())?;
        let level_type = item.get("levtype").ok_or(())?;
        if !matches!(level_type.as_str(), Some("sfc" | "pl")) {
            continue;
        }
        if param.is_array() || param.is_object() {
            return Err(());
        }
        let (variable, level) = match (level_type.as_str(), param.as_str()) {
            (Some("sfc"), Some(param)) => {
                let value = match param {
                    "2t" => ("TMP", "2 m above ground"),
                    "2d" => ("DPT", "2 m above ground"),
                    "sp" => ("PRES", "surface"),
                    "10u" => ("UGRD", "10 m above ground"),
                    "10v" => ("VGRD", "10 m above ground"),
                    "10fg" => ("GUST", "surface"),
                    "tp" => ("APCP", "surface"),
                    "tprate" => ("PRATE", "surface"),
                    "tcwv" => ("PWAT", "entire atmosphere (considered as a single layer)"),
                    "mucape" => ("CAPE", "255-0 mb above ground"),
                    "sd" => ("sd", "surface"),
                    "rsn" => ("rsn", "surface"),
                    "ptype" => ("ptype", "surface"),
                    _ => continue,
                };
                (value.0, value.1.to_string())
            }
            (Some("pl"), Some(param)) => {
                let variable = match param {
                    "gh" => "HGT",
                    "t" => "TMP",
                    "u" => "UGRD",
                    "v" => "VGRD",
                    _ => continue,
                };
                (
                    variable,
                    format!("{} mb", integer(item.get("levelist").ok_or(())?)?),
                )
            }
            _ => continue,
        };
        let offset = integer(item.get("_offset").ok_or(())?)?
            .try_into()
            .map_err(|_| ())?;
        let length: u64 = integer(item.get("_length").ok_or(())?)?
            .try_into()
            .map_err(|_| ())?;
        if length == 0 {
            return Err(());
        }
        let forecast = if param.as_str() == Some("tp") {
            format!("0-{} hour acc fcst", integer(item.get("step").ok_or(())?)?)
        } else {
            String::new()
        };
        rows.push(InventoryRow {
            index: index + 1,
            offset,
            length,
            variable,
            level,
            forecast,
        });
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_record_lines_ranges_pressure_and_accumulation() {
        let text = concat!("\n{\"param\":\"tp\",\"levtype\":\"sfc\",\"step\":\"12\",\"_offset\":100,\"_length\":50}\r\n",
            "{\"param\":\"u\",\"levtype\":\"pl\",\"levelist\":\"٥٠٠\",\"_offset\":\"1_50\",\"_length\":80.9}\u{2028}",
            "{\"param\":\"unknown\",\"levtype\":\"sfc\"}");
        let rows = parse_inventory(text).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(
            (rows[0].index, rows[0].offset, rows[0].length),
            (2, 100, 50)
        );
        assert_eq!(
            (
                rows[0].variable,
                rows[0].level.as_str(),
                rows[0].forecast.as_str()
            ),
            ("APCP", "surface", "0-12 hour acc fcst")
        );
        assert_eq!(
            (rows[1].index, rows[1].offset, rows[1].length),
            (3, 150, 80)
        );
        assert_eq!(
            (rows[1].variable, rows[1].level.as_str()),
            ("UGRD", "500 mb")
        );
        let cape = parse_inventory(r#"{"param":"mucape","levtype":"sfc","_offset":0,"_length":1}"#)
            .unwrap();
        assert_eq!(
            (cape[0].variable, cape[0].level.as_str()),
            ("CAPE", "255-0 mb above ground")
        );
    }

    #[test]
    fn rejects_invalid_supported_records_without_requiring_unused_fields() {
        for text in [
            "{",
            "[]",
            "{}",
            r#"{"param":"2t","levtype":"sfc","_offset":-1,"_length":1}"#,
            r#"{"param":"2t","levtype":"sfc","_offset":0,"_length":0}"#,
            r#"{"param":"t","levtype":"pl","levelist":"500.0","_offset":0,"_length":1}"#,
            r#"{"param":"tp","levtype":"sfc","_offset":0,"_length":1}"#,
            r#"{"param":"2t","levtype":"sfc","_offset":"1__2","_length":1}"#,
        ] {
            assert!(parse_inventory(text).is_err(), "{text}");
        }
        assert!(parse_inventory(r#"{"param":"unknown","levtype":"sfc"}"#)
            .unwrap()
            .is_empty());
        assert!(parse_inventory(r#"{"param":[],"levtype":"other"}"#)
            .unwrap()
            .is_empty());
    }
}
