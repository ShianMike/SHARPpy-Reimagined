//! Dispatch native single-site scans without treating unsupported products as US data.

use crate::polar_radar::{Bounds, Palette};
use crate::radar_sites::RadarSite;
use geographiclib_rs::{Geodesic, InverseGeodesic};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use time::OffsetDateTime;

#[derive(Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub product: Option<String>,
    pub site_id: Option<String>,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub valid_time: Option<String>,
    pub tilt: Option<String>,
}

#[derive(Debug)]
pub enum Error {
    Invalid(String),
    ScanMissing,
    Fetch(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) | Self::Fetch(message) => f.write_str(message),
            Self::ScanMissing => f.write_str("No radar scan is available near the requested time."),
        }
    }
}

#[derive(Serialize)]
pub struct Frame {
    #[serde(skip)]
    pub(crate) actual_time: Option<OffsetDateTime>,
    pub product: String,
    pub site_id: String,
    pub elevation_angle: Option<f64>,
    pub palette: Option<Palette>,
    pub legend_png_base64: Option<String>,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub retrieved_at: String,
    pub valid_time: Option<String>,
    pub update_interval_s: f64,
    pub attribution: String,
    pub source_url: String,
    pub byte_length: usize,
    pub png_base64: String,
}

pub fn resolve(request: &Request) -> Result<Option<&'static RadarSite>, Error> {
    if let Some(id) = request.site_id.as_deref() {
        return Ok(crate::radar_sites::sites()
            .iter()
            .find(|site| site.id.eq_ignore_ascii_case(id)));
    }
    let (Some(lat), Some(lon)) = (request.latitude, request.longitude) else {
        return Ok(None);
    };
    if !lat.is_finite()
        || !lon.is_finite()
        || !(-90.0..=90.0).contains(&lat)
        || !(-180.0..=180.0).contains(&lon)
    {
        return Err(Error::Invalid("Radar coordinates must be in range.".into()));
    }
    let earth = Geodesic::wgs84();
    Ok(crate::radar_sites::sites()
        .iter()
        .map(|site| {
            let distance: f64 = earth.inverse(lat, lon, site.lat, site.lon);
            (site, distance)
        })
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .filter(|(site, distance)| {
            let max_distance = if site.country.is_none() {
                460_000.0
            } else {
                500_000.0
            };
            *distance <= max_distance
        })
        .map(|(site, _)| site))
}

/// Combine the bundled catalogue with the current OPERA catalogue when available.
pub async fn sites() -> Vec<RadarSite> {
    let opera = crate::opera_radar::client().sites().await;
    merge_sites(crate::radar_sites::sites(), opera)
}

fn merge_sites(bundled: &[RadarSite], opera: Vec<RadarSite>) -> Vec<RadarSite> {
    let earth = Geodesic::wgs84();
    let mut sites: Vec<RadarSite> = bundled
        .iter()
        .filter(|site| {
            site.provider.as_deref() != Some("MRRL")
                || !opera.iter().any(|candidate| {
                    let distance: f64 =
                        earth.inverse(site.lat, site.lon, candidate.lat, candidate.lon);
                    (candidate.country == site.country
                        || (candidate.country.as_deref() == Some("FR")
                            && matches!(site.country.as_deref(), Some("GP" | "MQ" | "RE" | "NC"))))
                        && distance <= 500.0
                        && site.name.as_ref().zip(candidate.name.as_ref()).is_some_and(
                            |(name, other)| name.to_lowercase() == other.to_lowercase(),
                        )
                })
        })
        .cloned()
        .collect();
    sites.extend(opera);
    sites
}

pub async fn fetch(request: Request, now: OffsetDateTime) -> Result<Option<Arc<Frame>>, Error> {
    if let Some(frame) = fetch_international(request.clone(), now).await? {
        return Ok(Some(frame));
    }
    let Some(site) = resolve(&request)? else {
        return Ok(None);
    };
    if site.country.is_none() {
        return match request.product.as_deref() {
            None | Some("reflectivity" | "correlation-coefficient") => {
                crate::nexrad_level2::client()
                    .fetch_site(request, now, site, None)
                    .await
            }
            Some("velocity" | "echo-tops") => {
                crate::radar_level3::client()
                    .fetch_site(request, now, site)
                    .await
            }
            Some("hydrometeor" | "storm-total" | "one-hour") => {
                crate::radar_site_wms::client()
                    .fetch_site(request, now, site)
                    .await
            }
            Some(other) => Err(Error::Invalid(format!(
                "Unsupported radar product {other:?}."
            ))),
        };
    }
    Ok(None)
}

pub async fn fetch_international(
    request: Request,
    now: OffsetDateTime,
) -> Result<Option<Arc<Frame>>, Error> {
    if request
        .site_id
        .as_deref()
        .is_some_and(crate::opera_radar::is_opera_id)
    {
        return crate::opera_radar::client().fetch(request, now).await;
    }
    let Some(site) = resolve(&request)? else {
        return Ok(None);
    };
    if site.country.as_deref() == Some("AU") || site.provider.as_deref() == Some("MRRL") {
        return crate::australian_radar::client()
            .fetch_site(request, now, site)
            .await;
    }
    match site.country.as_deref() {
        Some("DE") => {
            crate::dwd_radar::client()
                .fetch_site(request, now, site)
                .await
        }
        _ => Ok(None),
    }
}

pub fn clear() {
    crate::australian_radar::client().clear();
    crate::dwd_radar::client().clear();
    crate::nexrad_level2::client().clear();
    crate::opera_radar::client().clear();
    crate::radar_mosaic::client().clear();
    crate::radar_level3::client().clear();
    crate::radar_site_wms::client().clear();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merge_sites_prefers_opera_when_same_antenna_is_present() {
        let bundled = crate::radar_sites::sites();
        let noumea = bundled.iter().find(|site| site.id == "MF94").unwrap();
        let duplicate_opera = RadarSite {
            id: "0-250-0-frnou".into(),
            lat: noumea.lat + 0.001,
            lon: noumea.lon - 0.001,
            half_span_deg: 0.0,
            products: vec!["reflectivity".into()],
            name: Some("Nouméa".into()),
            // OPERA identifies French overseas sites using the FR node prefix.
            country: Some("FR".into()),
            wmo: None,
            provider: Some("OPERA".into()),
        };
        let merged = merge_sites(bundled, vec![duplicate_opera]);
        assert!(!merged.iter().any(|site| site.id == "MF94"));
        assert!(merged.iter().any(|site| site.id == "0-250-0-frnou"));
        assert!(merged.iter().any(|site| site.id == "MF90"));
    }

    #[test]
    fn merge_sites_keeps_nearby_differently_named_antennas() {
        let bundled = crate::radar_sites::sites();
        let noumea = bundled.iter().find(|site| site.id == "MF94").unwrap();
        let nearby = RadarSite {
            id: "0-250-0-frnew".into(),
            lat: noumea.lat + 0.001,
            lon: noumea.lon,
            name: Some("Another radar".into()),
            provider: Some("OPERA".into()),
            ..noumea.clone()
        };
        let merged = merge_sites(bundled, vec![nearby]);
        assert!(merged.iter().any(|site| site.id == "MF94"));
        assert!(merged.iter().any(|site| site.id == "0-250-0-frnew"));
    }
}
