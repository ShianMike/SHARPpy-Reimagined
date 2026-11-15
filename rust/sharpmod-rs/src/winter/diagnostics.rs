//! Profile-derived winter ingredients. Missing coverage stays unavailable.
use super::*;

pub(crate) struct WinterDiagnostics {
    pub source: Option<Level>,
    pub thermal: Vec<Layer>,
    pub ptype: &'static str,
    pub clouds: Vec<Layer>,
    pub pbl: Option<Level>,
    pub wind10: Option<Level>,
    pub pbl_wind: [f64; 2],
    pub dgz_wind: [f64; 2],
    pub gust: f64,
    pub max_wind: f64,
    pub dgz_pw: f64,
    pub oprh: f64,
    pub squall: f64,
    pub inversion: Option<(f64, f64)>,
    pub rh_above: f64,
    pub cloud_omega: f64,
    pub fog: [Option<bool>; 4],
    pub drizzle: [Option<bool>; 5],
}

impl WinterProfile {
    pub fn at_height(&self, z: f64) -> Option<Level> {
        if !z.is_finite() {
            return None;
        }
        self.levels
            .iter()
            .find(|l| (l.z - z).abs() < 0.001)
            .copied()
            .or_else(|| {
                self.levels.windows(2).find_map(|pair| {
                    let (a, b) = (pair[0], pair[1]);
                    (valid_pair(a, b) && a.z < z && z < b.z)
                        .then(|| a.between(b, (z - a.z) / (b.z - a.z)))
                })
            })
    }

    pub fn at_pressure(&self, p: f64) -> Option<Level> {
        self.levels
            .iter()
            .find(|l| (l.p - p).abs() < 0.001)
            .copied()
            .or_else(|| {
                self.levels.windows(2).find_map(|pair| {
                    let (a, b) = (pair[0], pair[1]);
                    (valid_pair(a, b) && a.p > p && p > b.p)
                        .then(|| a.between(b, (p / a.p).ln() / (b.p / a.p).ln()))
                })
            })
    }

    // Include exact endpoints and reject missing/nonmonotonic coordinates;
    // never silently bridge a gap or extrapolate a short profile.
    pub fn section(&self, bottom: f64, top: f64) -> Option<Vec<Level>> {
        if top <= bottom
            || self
                .levels
                .iter()
                .any(|l| !l.z.is_finite() || !l.p.is_finite())
        {
            return None;
        }
        let mut result = vec![self.at_height(bottom)?];
        for pair in self.levels.windows(2) {
            if pair[0].z < top && pair[1].z > bottom && !valid_pair(pair[0], pair[1]) {
                return None;
            }
        }
        result.extend(
            self.levels
                .iter()
                .filter(|l| l.z > bottom && l.z < top)
                .copied(),
        );
        result.push(self.at_height(top)?);
        result
            .windows(2)
            .all(|w| valid_pair(w[0], w[1]))
            .then_some(result)
    }

    pub fn mean(&self, bottom: f64, top: f64, value: fn(Level) -> f64) -> f64 {
        self.section(bottom, top).map_or(f64::NAN, |levels| {
            levels
                .windows(2)
                .map(|w| (value(w[0]) + value(w[1])) * 0.5 * (w[1].z - w[0].z))
                .sum::<f64>()
                / (top - bottom)
        })
    }

    fn max_wind(&self, top: f64) -> f64 {
        self.section(0., top).map_or(f64::NAN, |levels| {
            if levels.iter().any(|l| !l.speed().is_finite()) {
                return f64::NAN;
            }
            levels.iter().map(|l| l.speed()).fold(0., f64::max)
        })
    }

    fn layer_water(&self, layer: &Layer) -> f64 {
        self.section(layer.bottom.z, layer.top.z)
            .map_or(f64::NAN, |levels| {
                // q dp / g gives kg/m² = mm; convert to inches for SPC's OPRH.
                levels
                    .windows(2)
                    .map(|w| {
                        (w[0].specific_humidity() + w[1].specific_humidity())
                            * 0.5
                            * (w[0].p - w[1].p)
                            * 100.
                            / 9.80665
                            / 25.4
                    })
                    .sum()
            })
    }

    pub fn diagnostics(&self, source_p: f64) -> WinterDiagnostics {
        let surface = self.levels[0];
        let source = self.at_pressure(source_p);
        let thermal = source
            .and_then(|s| self.section(0., s.z))
            .map_or_else(Vec::new, |levels| {
                let mut result = layers(&levels, |l| l.tw, -150., 0., false);
                result.extend(layers(&levels, |l| l.tw, 0., 65., false));
                result
                    .retain(|l| l.energy.abs() > 0. && (l.depth() >= 50. || l.energy.abs() >= 1.));
                result.sort_by(|a, b| b.bottom.z.total_cmp(&a.bottom.z));
                result
            });
        let clouds: Vec<_> = layers(&self.levels, |l| l.rhi, 90., f64::MAX, false)
            .into_iter()
            .filter(|l| l.depth() >= 200. && l.bottom.z < 8000.)
            .collect();
        let ptype = super::precipitation::phase(self, source, &clouds, &thermal);
        // SPC/SHARPpy's surface theta-v + 0.5 K definition, interpolated at the crossing.
        let threshold = surface.theta_v() + 0.5;
        let mut pbl = None;
        for pair in self.levels.windows(2) {
            let (a, b) = (pair[0], pair[1]);
            if !valid_pair(a, b) || !a.theta_v().is_finite() || !b.theta_v().is_finite() {
                break;
            }
            if a.theta_v() <= threshold && b.theta_v() > threshold {
                pbl = Some(a.between(b, (threshold - a.theta_v()) / (b.theta_v() - a.theta_v())));
                break;
            }
        }
        let wind10 = self.at_height(10.).filter(|l| l.speed().is_finite());
        let pbl_wind = pbl.map_or([f64::NAN; 2], |l| {
            [self.mean(0., l.z, |l| l.u), self.mean(0., l.z, |l| l.v)]
        });
        let dgz_depth: f64 = self.dgz.iter().map(Layer::depth).sum();
        let dgz_wind = [|l: Level| l.u, |l: Level| l.v].map(|value: fn(Level) -> f64| {
            if dgz_depth == 0. {
                return f64::NAN;
            }
            self.dgz
                .iter()
                .map(|l| self.mean(l.bottom.z, l.top.z, value) * l.depth())
                .sum::<f64>()
                / dgz_depth
        });
        let dgz_pw = if self.dgz.is_empty() {
            f64::NAN
        } else {
            self.dgz.iter().map(|l| self.layer_water(l)).sum()
        };
        let [rh, _, omega] = self.dgz_means();
        let mean_rh = self.mean(0., 2000., |l| l.rh);
        let mean_speed = self.mean(0., 2000., Level::speed);
        let theta_change = self
            .at_height(2000.)
            .map_or(f64::NAN, |l| l.theta_e() - surface.theta_e());
        let squall = if [mean_rh, mean_speed, theta_change, surface.tw]
            .iter()
            .all(|v| v.is_finite())
        {
            if surface.tw > 1. {
                0.
            } else {
                ((mean_rh - 60.) / 15.).max(0.)
                    * ((4. - theta_change) / 4.).max(0.)
                    * (mean_speed * 0.514444 / 9.)
            }
        } else {
            f64::NAN
        };
        let inversion = self.section(0., 1500.).and_then(|levels| {
            if levels.iter().any(|l| !l.t.is_finite()) {
                return None;
            }
            let max = levels.iter().max_by(|a, b| a.t.total_cmp(&b.t)).unwrap();
            Some((max.t - surface.t, max.z))
        });
        let cloud = clouds.iter().find(|l| l.bottom.z <= 2000.);
        let rh_above = cloud.map_or(f64::NAN, |l| self.mean(l.top.z, l.top.z + 1000., |l| l.rhi));
        let cloud_omega = cloud.map_or(f64::NAN, |l| self.mean(l.bottom.z, l.top.z, |l| l.omega));
        let finite = |v: f64, test: bool| v.is_finite().then_some(test);
        let moisture_complete = self
            .levels
            .iter()
            .take_while(|l| l.z <= 8000.)
            .all(|l| l.rhi.is_finite());
        let cloud_test =
            |test: fn(&Layer) -> bool| cloud.map(test).or(moisture_complete.then_some(false));
        WinterDiagnostics {
            source,
            thermal,
            ptype,
            pbl,
            wind10,
            pbl_wind,
            dgz_wind,
            gust: pbl.map_or(f64::NAN, |l| self.max_wind(l.z)),
            max_wind: self.max_wind(2000.),
            dgz_pw,
            oprh: omega * dgz_pw * rh / 100.,
            squall,
            inversion,
            rh_above,
            cloud_omega,
            fog: [
                finite(surface.t, surface.t < 0.),
                finite(surface.rh, surface.rh >= 95.),
                wind10.map(|l| l.speed() <= 6.),
                inversion.map(|(t, _)| t > 0.5),
            ],
            drizzle: [
                finite(surface.tw, surface.tw <= 0.),
                cloud
                    .filter(|l| l.top.z < self.levels.last().unwrap().z)
                    .map(|l| l.top.t > -10.)
                    .or_else(|| (cloud.is_none() && moisture_complete).then_some(false)),
                finite(rh_above, rh_above <= 75.),
                cloud_test(|l| l.depth() >= 500.),
                finite(cloud_omega, cloud_omega < 0.),
            ],
            clouds,
        }
    }
}
