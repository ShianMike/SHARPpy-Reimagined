use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use worker::Url;

pub const MAX_RPC_BYTES: usize = 64 * 1024;
pub const MAX_CACHE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReadRequest {
    pub id: u64,
    pub method: String,
    pub params: Map<String, Value>,
}

/// A subset of the hosted gateway's read-only weather methods and cache lifetimes.
pub fn read_ttl(method: &str) -> Option<u32> {
    Some(match method {
        "get_model_catalog" | "get_map_catalog" => 300,
        "get_station_catalog" | "get_radar_sites" | "get_weather_field" | "get_field_contours" => {
            3600
        }
        "get_radar_frame" | "get_radar_site_frame" => 60,
        "get_radar_tilts" => 45,
        _ => return None,
    })
}

/// These files are explicitly accessible without a session at the OVH gateway.
pub fn public_file(name: &str) -> Option<&str> {
    matches!(
        name,
        "share-card-v3.png"
            | "share-card.svg"
            | "favicon.svg"
            | "favicon.ico"
            | "apple-touch-icon.png"
    )
    .then_some(name)
}

pub fn origin_url(value: &str) -> Option<Url> {
    let url = Url::parse(value).ok()?;
    let loopback = matches!(url.host_str(), Some("127.0.0.1" | "[::1]" | "localhost"));
    let scheme = url.scheme();
    ((scheme == "https" || (scheme == "http" && loopback))
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none()
        && url.path() == "/"
        && url.query().is_none()
        && url.fragment().is_none())
    .then_some(url)
}

pub fn cacheable_response(
    status: u16,
    set_cookie: bool,
    vary: &str,
    cache_control: &str,
    length: Option<usize>,
    max_ttl: u32,
) -> Option<u32> {
    if status != 200
        || set_cookie
        || length.is_none_or(|size| size == 0 || size > MAX_CACHE_BYTES)
        || vary
            .split(',')
            .map(str::trim)
            .any(|header| !header.is_empty() && !header.eq_ignore_ascii_case("Accept-Encoding"))
    {
        return None;
    }
    let mut public = false;
    let mut max_age = None;
    let mut shared_age = None;
    for directive in cache_control.split(',') {
        let (name, value) = directive
            .trim()
            .split_once('=')
            .unwrap_or((directive.trim(), ""));
        match name.trim().to_ascii_lowercase().as_str() {
            "public" => public = true,
            "private" | "no-store" | "no-cache" => return None,
            "max-age" => max_age = Some(value.trim().trim_matches('"').parse::<u32>().ok()?),
            "s-maxage" => shared_age = Some(value.trim().trim_matches('"').parse::<u32>().ok()?),
            _ => {}
        }
    }
    let ttl = shared_age.or(max_age)?.min(max_ttl);
    (public && ttl > 0).then_some(ttl)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn admits_reads_and_excludes_session_mutations() {
        for method in ["get_weather_field", "get_radar_frame", "get_model_catalog"] {
            assert!(read_ttl(method).is_some());
        }
        for method in [
            "open_sounding",
            "cancel_request",
            "get_backend_health",
            "clear_cache",
        ] {
            assert_eq!(read_ttl(method), None);
        }
        assert_eq!(public_file("share-card-v3.png"), Some("share-card-v3.png"));
        assert_eq!(public_file("assets/index.js"), None);
    }

    #[test]
    fn requires_an_explicit_shareable_success_with_known_size() {
        let ttl = |status, cookie, vary, control, length| {
            cacheable_response(status, cookie, vary, control, length, 60)
        };
        let some = Some(1024);
        let public = "public, max-age=500";
        assert_eq!(ttl(200, false, "Accept-Encoding", public, some), Some(60));
        assert_eq!(
            ttl(200, false, "", "public, s-maxage=20, max-age=100", some),
            Some(20)
        );
        assert_eq!(ttl(200, false, "", "max-age=100", some), None);
        assert_eq!(
            ttl(200, false, "", "public, max-age=100, no-store", some),
            None
        );
        assert_eq!(
            ttl(200, false, "", "public, max-age=100, private", some),
            None
        );
        assert_eq!(
            ttl(200, false, "", "public, s-maxage=0, max-age=100", some),
            None
        );
        assert_eq!(ttl(200, false, "Cookie", public, some), None);
        assert_eq!(ttl(200, false, "*", public, some), None);
        assert_eq!(ttl(200, true, "", public, some), None);
        assert_eq!(ttl(202, false, "", public, some), None);
        assert_eq!(ttl(500, false, "", public, some), None);
        assert_eq!(ttl(200, false, "", public, None), None);
        assert_eq!(ttl(200, false, "", public, Some(MAX_CACHE_BYTES + 1)), None);
    }

    #[test]
    fn origin_is_a_fixed_https_origin_or_local_preview() {
        assert!(origin_url("https://app.sharppyre.online").is_some());
        assert!(origin_url("http://127.0.0.1:4198").is_some());
        for url in [
            "http://app.sharppyre.online",
            "https://app.sharppyre.online/__backend",
            "https://app.sharppyre.online?source=test",
            "https://operator@app.sharppyre.online",
        ] {
            assert!(origin_url(url).is_none());
        }
    }
}
