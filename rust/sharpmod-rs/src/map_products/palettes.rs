//! Reference color tables for the forecast map catalogue.

pub(super) const ISOTACH_MIN_KT: u8 = 20;
pub(super) const LOW_LEVEL_WIND_MAX_KT: u8 = 80;
pub(super) const WIND_500_MAX_KT: u8 = 140;
pub(super) const DEEP_SHEAR_MAX_KT: u8 = 140;
pub(super) const EHI_FLOOR: f64 = 0.5;
pub(super) const EHI_TICKS: [f64; 16] = [
    0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 14.0, 16.0, 16.4,
];
pub(super) const CAPE03_TICKS: [f64; 15] = [
    0.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0, 300.0, 425.0,
    525.0,
];
pub(super) const LCL_TICKS: [f64; 16] = [
    0.0, 250.0, 500.0, 750.0, 1000.0, 1250.0, 1500.0, 1750.0, 2000.0, 2500.0, 3000.0, 3500.0,
    4000.0, 6500.0, 9000.0, 10000.0,
];

const EHI_COLOURS: [&str; 72] = [
    "#ffffff", "#f0f0f0", "#e1e1e1", "#d2d2d2", "#c3c3c3", "#a5a5a5", "#969696", "#878787",
    "#787878", "#696969", "#37536a", "#436075", "#506d80", "#5c7a8b", "#698796", "#7594a2",
    "#82a1ad", "#8eaeb8", "#9bbbc3", "#a7c8ce", "#e9dd96", "#e8d186", "#e7c575", "#e6b865",
    "#e5ac54", "#e5a044", "#e49433", "#e38723", "#e27b12", "#e16f02", "#dc4110", "#d33b17",
    "#ca351e", "#c12e25", "#b8282c", "#af2234", "#a61c3b", "#9d1542", "#940f49", "#8b0950",
    "#73088a", "#7e1894", "#8a289f", "#9538a9", "#a148b3", "#ac59be", "#b869c8", "#c379d2",
    "#cf89dd", "#da99e7", "#e9bec3", "#e3b0b7", "#dda3ac", "#d795a0", "#d18894", "#ca7a89",
    "#c46d7d", "#be5f71", "#b85266", "#b2445a", "#893d48", "#8f4752", "#96525b", "#9c5c65",
    "#a2676f", "#a97178", "#af7c82", "#b6868b", "#bc9195", "#c29b9f", "#c9a6a8",
    "#c9a6a8", // Close the last band, which starts at 16.
];

pub(super) fn ehi_stops() -> impl ExactSizeIterator<Item = (f64, &'static str)> {
    EHI_COLOURS.into_iter().enumerate().map(|(index, colour)| {
        // Five swatches between each printed tick: 0.1, 0.2, then 0.4 per band.
        let tenths = match index {
            0..=19 => index,
            20..=49 => 20 + 2 * (index - 20),
            _ => 80 + 4 * (index - 50),
        };
        (tenths as f64 / 10.0, colour)
    })
}

pub(super) fn cape03_stops() -> impl ExactSizeIterator<Item = (f64, &'static str)> {
    (0..70usize).map(|index| {
        // Five swatches per 25 J/kg through 300, then 25 J/kg per swatch.
        let value = if index < 60 {
            5 * index
        } else {
            300 + 25 * (index - 60)
        };
        (value as f64, EHI_COLOURS[index.min(68)])
    })
}

pub(super) fn lcl_stops() -> impl ExactSizeIterator<Item = (f64, &'static str)> {
    (0..73usize).map(|index| {
        let value = match index {
            0..=39 => (index * 50) as f64,
            40..=59 => (2000 + (index - 40) * 100) as f64,
            _ => (4000 + (index - 60) * 500) as f64,
        };
        let colour = if index <= 70 {
            EHI_COLOURS[index]
        } else {
            "#cfb0b2"
        };
        (value, colour)
    })
}

pub(super) fn isotach_stops(
    source: Vec<(f64, String)>,
    max_kt: u8,
    source_step: f64,
) -> Vec<(f64, String)> {
    // Start at the 50 kt swatch of the original wind table. The 500 mb and
    // deep-shear maps use every colour, and the 700/850 mb maps use every other one.
    let min = f64::from(ISOTACH_MIN_KT);
    let max = f64::from(max_kt);
    source
        .into_iter()
        .filter_map(|(old, colour)| {
            let speed = min + (old - 50.0) / source_step;
            ((min..=max).contains(&speed) && speed.fract() == 0.0).then_some((speed, colour))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shallow_cape_uses_the_reference_bands() {
        let stops: Vec<_> = cape03_stops().collect();
        assert_eq!(stops.len(), 70);
        for (index, expected) in [
            (0, (0.0, "#ffffff")),
            (5, (25.0, "#a5a5a5")),
            (10, (50.0, "#37536a")),
            (20, (100.0, "#e9dd96")),
            (30, (150.0, "#dc4110")),
            (60, (300.0, "#893d48")),
            (65, (425.0, "#a97178")),
            (68, (500.0, "#bc9195")),
            (69, (525.0, "#bc9195")),
        ] {
            assert_eq!(stops[index], expected);
        }
        assert!(stops.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(CAPE03_TICKS
            .iter()
            .all(|tick| stops.iter().any(|(value, _)| value == tick)));
    }

    #[test]
    fn ehi_matches_reference_swatch_boundaries() {
        let stops: Vec<_> = ehi_stops().collect();
        for (index, expected) in [
            (4, (0.4, "#c3c3c3")),
            (5, (0.5, "#a5a5a5")),
            (10, (1.0, "#37536a")),
            (19, (1.9, "#a7c8ce")),
            (20, (2.0, "#e9dd96")),
            (49, (7.8, "#da99e7")),
            (50, (8.0, "#e9bec3")),
            (55, (10.0, "#ca7a89")),
            (60, (12.0, "#893d48")),
            (70, (16.0, "#c9a6a8")),
            (71, (16.4, "#c9a6a8")),
        ] {
            assert_eq!(stops[index], expected);
        }
        assert!(stops.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(EHI_TICKS
            .iter()
            .all(|tick| stops.iter().any(|(value, _)| value == tick)));
    }

    #[test]
    fn lcl_matches_reference_swatch_boundaries() {
        let stops: Vec<_> = lcl_stops().collect();
        assert_eq!(stops.len(), 73);
        for (index, expected) in [
            (0, (0.0, "#ffffff")),
            (5, (250.0, "#a5a5a5")),
            (10, (500.0, "#37536a")),
            (20, (1000.0, "#e9dd96")),
            (30, (1500.0, "#dc4110")),
            (40, (2000.0, "#73088a")),
            (50, (3000.0, "#e9bec3")),
            (60, (4000.0, "#893d48")),
            (65, (6500.0, "#a97178")),
            (70, (9000.0, "#c9a6a8")),
            (71, (9500.0, "#cfb0b2")),
            (72, (10000.0, "#cfb0b2")),
        ] {
            assert_eq!(stops[index], expected);
        }
        assert!(stops.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(LCL_TICKS
            .iter()
            .all(|tick| stops.iter().any(|(value, _)| value == tick)));
    }

    #[test]
    fn wind_850_matches_the_supplied_knot_swatches() {
        let source = [
            (49.0, "#ffffff"),
            (50.0, "#f0f8ff"),
            (51.0, "#e6f4ff"),
            (60.0, "#87cefa"),
            (70.0, "#6a5acd"),
            (110.0, "#c80028"),
            (140.0, "#faf064"),
            (170.0, "#a05a0a"),
            (171.0, "#a05a0a"),
        ];
        let mapped = isotach_stops(
            source
                .into_iter()
                .map(|(speed, colour)| (speed, colour.to_owned()))
                .collect(),
            LOW_LEVEL_WIND_MAX_KT,
            2.0,
        );
        let swatches: Vec<_> = mapped
            .iter()
            .map(|(speed, colour)| (*speed, colour.as_str()))
            .collect();
        assert_eq!(
            swatches,
            [
                (20.0, "#f0f8ff"),
                (25.0, "#87cefa"),
                (30.0, "#6a5acd"),
                (50.0, "#c80028"),
                (65.0, "#faf064"),
                (80.0, "#a05a0a"),
            ]
        );
    }

    #[test]
    fn deep_shear_matches_the_supplied_knot_swatches() {
        let source = [
            (49.0, "#ffffff"),
            (50.0, "#f0f8ff"),
            (51.0, "#e6f4ff"),
            (60.0, "#87cefa"),
            (70.0, "#6a5acd"),
            (80.0, "#e696dc"),
            (110.0, "#c80028"),
            (130.0, "#f05050"),
            (140.0, "#faf064"),
            (160.0, "#be8c28"),
            (170.0, "#a05a0a"),
            (171.0, "#a05a0a"),
        ];
        let mapped = isotach_stops(
            source
                .into_iter()
                .map(|(speed, colour)| (speed, colour.to_owned()))
                .collect(),
            DEEP_SHEAR_MAX_KT,
            1.0,
        );
        assert_eq!(
            mapped
                .iter()
                .map(|(speed, colour)| (*speed, colour.as_str()))
                .collect::<Vec<_>>(),
            [
                (20.0, "#f0f8ff"),
                (21.0, "#e6f4ff"),
                (30.0, "#87cefa"),
                (40.0, "#6a5acd"),
                (50.0, "#e696dc"),
                (80.0, "#c80028"),
                (100.0, "#f05050"),
                (110.0, "#faf064"),
                (130.0, "#be8c28"),
                (140.0, "#a05a0a"),
            ]
        );
    }
}
