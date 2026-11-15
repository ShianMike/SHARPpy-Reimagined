//! Experimental layer-by-layer Bourgouin/Birk guidance from SounderPy's
//! development winter method (MIT; see NOTICE). This is not Birk's calibrated
//! column probability algorithm. SPC guidance is shown separately.
use super::*;

pub(super) fn phase(
    data: &WinterProfile,
    source: Option<Level>,
    clouds: &[Layer],
    thermal: &[Layer],
) -> &'static str {
    let Some(source) = source else {
        return "No saturated source";
    };
    let Some(section) = data.section(0., source.z) else {
        return "Unavailable";
    };
    if !source.t.is_finite() || section.iter().any(|l| !l.tw.is_finite()) {
        return "Unavailable";
    }
    let initial = clouds
        .iter()
        .find(|l| source.z >= l.bottom.z - 300. && source.z <= l.top.z + 300.)
        .map_or(source.t, |l| l.top.t);
    #[derive(Clone, Copy)]
    enum Phase {
        Snow,
        Pellets,
        Rain,
        Drizzle,
        RainSnow,
        RainPellets,
        FreezingMix,
    }
    use Phase::*;
    let mut phase = if initial > 0. {
        Rain
    } else if initial > -10. {
        Drizzle
    } else {
        Snow
    };
    let ice_origin = matches!(phase, Snow);
    let mut melt = 0.;
    for layer in thermal {
        if layer.energy > 0. && matches!(phase, Snow | Pellets) {
            melt = layer.energy;
            let (partial, full) = if layer.bottom.z <= 1. {
                (5.6, 13.2)
            } else {
                (9.4, 15.)
            };
            if melt > full {
                phase = Rain;
            } else if melt >= partial {
                phase = if matches!(phase, Snow) {
                    RainSnow
                } else {
                    RainPellets
                };
            }
        } else if layer.energy < 0. {
            if matches!(phase, RainSnow | RainPellets) {
                phase = Pellets;
            } else if matches!(phase, Rain) && ice_origin && melt > 0. {
                let refreeze = -layer.energy;
                if refreeze > 208. + 0.08 * melt {
                    phase = Pellets;
                } else if refreeze >= 117. + 0.08 * melt {
                    phase = FreezingMix;
                }
            }
        }
    }
    let frozen_surface = data.levels[0].t <= 0.;
    match phase {
        Snow => "Snow",
        Pellets => "Ice pellets",
        RainSnow => "Rain / snow",
        RainPellets => "Rain / ice pellets",
        FreezingMix => "Freezing rain / ice pellets",
        Rain if frozen_surface => "Freezing rain",
        Rain => "Rain",
        Drizzle if frozen_surface => "Freezing drizzle",
        Drizzle => "Drizzle",
    }
}
