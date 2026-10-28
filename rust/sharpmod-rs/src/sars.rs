//! Matching thresholds from the pinned SHARPpy 1.4.0a5 SARS routines.

pub(crate) mod catalogue;

pub(crate) struct Record<const N: usize> {
    pub name: String,
    pub outcome: f64,
    pub classification: String,
    pub values: [f64; N],
}

pub(crate) struct Matches {
    pub quality: Vec<usize>,
    pub loose: usize,
    pub significant: usize,
    pub probability: f64,
}

pub(crate) fn parse<const N: usize>(
    text: &str,
    columns: [usize; N],
    outcome_column: usize,
    comment: &str,
) -> Result<Vec<Record<N>>, String> {
    let mut records = Vec::new();
    let required = columns
        .iter()
        .copied()
        .chain([outcome_column])
        .max()
        .unwrap()
        + 1;
    let mut width = 0;
    for (index, line) in text.lines().enumerate().skip(1) {
        let line = line.split_once(comment).map_or(line, |(data, _)| data);
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.is_empty() {
            continue;
        }
        if width == 0 {
            width = fields.len();
        }
        if fields.len() != width || fields.len() < required {
            return Err(format!(
                "SARS database row {} has inconsistent or missing columns",
                index + 1
            ));
        }
        let number = |column: usize| {
            fields[column]
                .parse::<f64>()
                .map_err(|_| format!("SARS database row {} has an invalid number", index + 1))
        };
        let mut values = [0.; N];
        for (value, column) in values.iter_mut().zip(columns) {
            *value = number(column)?;
        }
        records.push(Record {
            name: fields[0].into(),
            outcome: number(outcome_column)?,
            classification: fields[outcome_column].into(),
            values,
        });
    }
    if records.len() < 2 {
        return Err("SARS database has fewer than two records".into());
    }
    Ok(records)
}

fn within(input: &[f64], record: &[f64], range: &[f64]) -> bool {
    input
        .iter()
        .zip(record)
        .zip(range)
        .all(|((&input, &record), &range)| input >= record - range && input <= record + range)
}

fn select<const N: usize>(
    records: &[Record<N>],
    input: [f64; N],
    loose: &[f64],
    quality: [f64; N],
    significant: fn(f64) -> bool,
    max_quality: usize,
    cape: f64,
) -> Matches {
    let mut result = Matches {
        quality: Vec::new(),
        loose: 0,
        significant: 0,
        probability: 0.,
    };
    for (index, record) in records.iter().enumerate() {
        if within(&input, &record.values, loose) {
            result.loose += 1;
            result.significant += usize::from(significant(record.outcome));
        }
        if result.quality.len() < max_quality && within(&input, &record.values, &quality) {
            result.quality.push(index);
        }
    }
    if result.loose > 0 && cape > 0. {
        result.probability = result.significant as f64 / result.loose as f64;
    }
    result
}

/// Input: MU mixing ratio/CAPE, 500 hPa temperature, lapse rate, 6/9/3 km shear, SRH.
pub(crate) fn hail(records: &[Record<8>], input: [f64; 8]) -> Matches {
    let cape = input[1];
    let cape_quality = cape
        * if cape < 500. {
            0.5
        } else if cape < 2000. {
            0.25
        } else {
            0.2
        };
    let srh_quality = if input[7] < 50. { 25. } else { input[7] * 0.5 };
    select(
        records,
        input,
        &[2., cape * 0.3, 9., 2., 12., 22., 10.],
        [2., cape_quality, 1.5, 0.4, 6., 15., 8., srh_quality],
        |size| size >= 2.,
        15,
        cape,
    )
}

/// Input: ML CAPE/LCL, 500 hPa temperature, lapse rate, 6 km shear, 1 km SRH,
/// 3/9 km shear and 3 km SRH. Shears retain the supercell database's knots.
pub(crate) fn supercell(records: &[Record<9>], input: [f64; 9]) -> Matches {
    let cape = input[0];
    // The reference deliberately uses the signed SRH for this loose range.
    let srh_loose = if input[5].abs() < 50. { 100. } else { input[5] };
    let srh_quality = if input[5].abs() < 100. {
        50.
    } else {
        input[5].abs() * 0.3
    };
    let srh3_quality = if input[8].abs() < 100. {
        50.
    } else {
        input[8].abs() * 0.5
    };
    select(
        records,
        input,
        &[
            if cape == 0. { 0. } else { 1300. },
            500.,
            7.,
            1.,
            14.,
            srh_loose,
        ],
        [
            cape * 0.25,
            200.,
            5.,
            0.8,
            10.,
            srh_quality,
            15.,
            25.,
            srh3_quality,
        ],
        |category| category > 0.,
        usize::MAX,
        cape,
    )
}

#[cfg(test)]
mod tests;
